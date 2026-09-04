"""Generate a bounded ANGPT1 PepGLAD control matched to PepFlow generation 3."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from generate_angpt1_pepglad_matched_control import (
    _pepglad_donors,
    build_proposals,
    sha256_file,
)
from sqlalchemy import select, text

from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_json, sha256_text

TARGET_KEY = "angpt1"
GENERATION = 3
SEED = 20260904
OPERATOR_ID = "angpt1-pepglad-matched-control-generation3-1aa-v1"
PARENT_RUN_ID = "a9cde5ec-d241-561a-ae19-e8de0c6c95a3"
EXPECTED_PARENT_IDS = {
    "3cf72071-92b5-4ad3-a111-1c9e733a68e2",
    "9adb7487-cdbe-4d75-890c-455959d41328",
    "3ebcb368-d393-47c9-9759-8ebde1ff5e87",
}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _edits(paths: list[Path]) -> set[tuple[str, int, str]]:
    edits: set[tuple[str, int, str]] = set()
    for path in paths:
        for row in _rows(path):
            parent = str(row.get("parent_sequence") or "").strip().upper()
            child = str(row.get("sequence") or "").strip().upper()
            if not parent or len(parent) != len(child):
                continue
            changed = [
                index
                for index, pair in enumerate(zip(parent, child, strict=True))
                if pair[0] != pair[1]
            ]
            if len(changed) == 1:
                edits.add((sha256_text(parent), changed[0], child[changed[0]]))
    return edits


def _validate_slots(rows: list[dict[str, str]]) -> None:
    if len(rows) != 12:
        raise ValueError(f"PepFlow generation3 slot denominator drifted: {len(rows)}")
    if {row.get("target_key") for row in rows} != {TARGET_KEY}:
        raise ValueError("PepFlow generation3 target identity drifted")
    if {row.get("parent_run_id") for row in rows} != {PARENT_RUN_ID}:
        raise ValueError("PepFlow generation3 parent run drifted")
    if not {row.get("parent_candidate_id") for row in rows} <= EXPECTED_PARENT_IDS:
        raise ValueError("PepFlow generation3 parent candidate identity drifted")
    for row in rows:
        sequence = "".join(str(row.get("parent_sequence") or "").split()).upper()
        if sha256_text(sequence) != str(row.get("parent_sequence_sha256") or "").strip().lower():
            raise ValueError("PepFlow generation3 parent sequence hash drifted")


async def _pg_history(hashes: list[str]) -> dict[str, Any]:
    try:
        async with SessionFactory() as session:
            await session.execute(text("SET statement_timeout = '15000ms'"))
            rows = list(
                await session.execute(
                    select(Candidate.sequence_sha256, Candidate.run_id, Candidate.id).where(
                        Candidate.sequence_sha256.in_(hashes)
                    )
                )
            )
    except Exception as exc:
        return {
            "status": "unavailable",
            "error_class": type(exc).__name__,
            "query_mode": "bounded_candidate_sequence_sha256_in",
            "full_sequence_scan": False,
            "queried_hash_count": len(hashes),
        }
    hits = [
        {
            "sequence_sha256": row.sequence_sha256,
            "run_id": str(row.run_id),
            "candidate_id": str(row.id),
        }
        for row in rows
    ]
    return {
        "status": "readback_verified",
        "query_mode": "bounded_candidate_sequence_sha256_in",
        "full_sequence_scan": False,
        "queried_hash_count": len(hashes),
        "history_hit_count": len(hits),
        "history_hits": hits,
    }


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    pepflow_rows = _rows(args.pepflow_proposals)
    _validate_slots(pepflow_rows)
    donors = _pepglad_donors(args.pepglad_donor_csv)
    historical_edits = _edits(args.history_csv)
    generated = build_proposals(
        pepflow_rows,
        donors,
        set(),
        historical_edits,
        limit=len(pepflow_rows),
    )
    for row in generated:
        row["generation"] = str(GENERATION)
        row["operator_id"] = OPERATOR_ID
        row["proposal_mode"] = "pepglad_matched_control_generation3_1aa"
        row["target_key"] = TARGET_KEY
        row["branch_key"] = TARGET_KEY
    generated_slots = {
        (
            row["parent_candidate_id"],
            row["acceptor_start_zero_based"],
            row["matched_pepflow_row_number"],
        ): row
        for row in generated
    }
    all_slots = [
        (
            row["parent_candidate_id"],
            row["acceptor_start_zero_based"],
            str(index + 2),
        )
        for index, row in enumerate(pepflow_rows)
    ]
    missing_slots = [slot for slot in all_slots if slot not in generated_slots]
    hashes = [row["sequence_sha256"] for row in generated]
    history = await _pg_history(hashes) if hashes else {
        "status": "readback_verified",
        "query_mode": "bounded_candidate_sequence_sha256_in",
        "full_sequence_scan": False,
        "queried_hash_count": 0,
        "history_hit_count": 0,
        "history_hits": [],
    }
    if history["status"] != "readback_verified":
        selected: list[dict[str, str]] = []
        unpaired_reasons = {"postgresql_history_unavailable": len(generated)}
    else:
        hits = {item["sequence_sha256"] for item in history.get("history_hits", [])}
        selected = [row for row in generated if row["sequence_sha256"] not in hits]
        unpaired_reasons = Counter(
            {
                "pepglad_slot_generation_failed": len(missing_slots),
                "pg_history_hit": len(generated) - len(selected),
            }
        )
        unpaired_reasons = {key: value for key, value in unpaired_reasons.items() if value}
        for row in selected:
            row["history_gate"] = "postgresql_exact_candidate_hash_preflight_passed"
    proposals_path = output_dir / "proposals.csv"
    _write_rows(proposals_path, selected)
    receipt = {
        "schema_version": "ampgent.angpt1-pepglad-matched-control-generation.2",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "target_key": TARGET_KEY,
        "source": "PepGLAD",
        "generation": GENERATION,
        "seed": SEED,
        "operator_id": OPERATOR_ID,
        "matched_source": "PepFlow_generation3_vs_PepGLAD",
        "pepflow_source": str(args.pepflow_proposals),
        "pepflow_source_sha256": sha256_file(args.pepflow_proposals),
        "matched_slot_count": len(pepflow_rows),
        "generated_pepglad_pair_count": len(generated),
        "pg_new_pair_count": len(selected),
        "proposal_count": len(selected),
        "pepglad_donor_count": len(donors),
        "donor_artifact": str(args.pepglad_donor_csv),
        "donor_artifact_sha256": sha256_file(args.pepglad_donor_csv),
        "historical_edit_count": len(historical_edits),
        "pg_history_preflight": history,
        "unpaired_slot_count": len(missing_slots) + len(generated) - len(selected),
        "unpaired_reasons": unpaired_reasons,
        "same_parent_position_budget": True,
        "budget_contract": "one_residue_only; no slot expansion",
        "full_reports_scan": False,
        "gpu_rosetta_md_submitted": False,
        "historical_runs_modified": False,
        "proposal_csv_sha256": sha256_file(proposals_path) if selected else None,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pepflow-proposals", type=Path, required=True)
    parser.add_argument("--pepglad-donor-csv", type=Path, required=True)
    parser.add_argument("--history-csv", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
