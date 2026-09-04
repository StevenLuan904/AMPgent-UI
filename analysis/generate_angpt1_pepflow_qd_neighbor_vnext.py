"""Bounded ANGPT1 PepFlow QD-neighbor generation with exact identity gates.

This wrapper deliberately reads only the three authoritative parent rows and
the compact prior proposal artifact.  PostgreSQL history is queried once for
the generated candidate SHA-256 values; no sequence-wide history scan is
performed and no candidate is materialized here.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import generate_fgf2_pepflow_source_expansion as source_expansion
from generate_fgf2_pepflow_source_expansion import (
    build_proposals,
    load_pepflow_donors,
    load_prior_edits,
    sha256_text,
)
from sqlalchemy import select, text

from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json

TARGET_KEY = "angpt1"
GENERATION = 3
SEED = 20260904
OPERATOR_ID = "angpt1-pepflow-qd-neighbor-generation3-1aa-v1"
PARENT_RUN_ID = "a9cde5ec-d241-561a-ae19-e8de0c6c95a3"
PARENT_IDS = (
    "3cf72071-92b5-4ad3-a111-1c9e733a68e2",
    "9adb7487-cdbe-4d75-890c-455959d41328",
    "3ebcb368-d393-47c9-9759-8ebde1ff5e87",
)
EXPECTED_PARENT_EVALS = 51


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_parents(queue_path: Path, readback_path: Path) -> list[dict[str, str]]:
    rows = _rows(queue_path)
    by_id = {row.get("candidate_id", "").strip(): row for row in rows}
    if set(by_id) != set(PARENT_IDS) or len(rows) != len(PARENT_IDS):
        raise ValueError("ANGPT1 parent queue does not contain exactly the three required IDs")
    if any(row.get("run_id", "").strip() != PARENT_RUN_ID for row in rows):
        raise ValueError("ANGPT1 parent queue run identity drifted")
    receipt = json.loads(readback_path.read_text(encoding="utf-8"))
    if (
        receipt.get("status") != "readback_verified"
        or receipt.get("run_id") != PARENT_RUN_ID
        or receipt.get("candidate_ids") != list(PARENT_IDS)
        or receipt.get("candidate_count") != len(PARENT_IDS)
        or receipt.get("evaluation_count") != EXPECTED_PARENT_EVALS
        or receipt.get("identity_drift") != 0
        or not receipt.get("tool_call_run_binding")
    ):
        raise ValueError("ANGPT1 parent PG readback contract is not verified")
    for row in rows:
        sequence = "".join(row.get("sequence", "").split()).upper()
        digest = sha256_text(sequence)
        if digest != row.get("sequence_sha256", "").strip().lower():
            raise ValueError("ANGPT1 parent sequence hash drifted")
        if row.get("qd_cell", "").strip() == "":
            raise ValueError("ANGPT1 parent QD cell is missing")
    normalized: list[dict[str, str]] = []
    for parent_id in PARENT_IDS:
        row = dict(by_id[parent_id])
        row["parent_run_id"] = row["run_id"]
        normalized.append(row)
    return normalized


def _local_history(
    paths: Iterable[Path], parents: list[dict[str, str]]
) -> tuple[set[str], set[tuple[str, int, str]]]:
    sequence_history = {
        row["sequence_sha256"].strip().lower()
        for path in paths
        for row in _rows(path)
        if row.get("sequence_sha256")
    }
    sequence_history.update(row["sequence_sha256"].strip().lower() for row in parents)
    edits = load_prior_edits(paths)
    return sequence_history, edits


async def bounded_pg_history(sequence_hashes: list[str]) -> dict[str, Any]:
    """Return only exact rows for this bounded candidate hash set."""
    normalized = [value.strip().lower() for value in sequence_hashes]
    try:
        async with SessionFactory() as session:
            await session.execute(text("SET statement_timeout = '15000ms'"))
            rows = list(
                await session.execute(
                    select(Candidate.sequence_sha256, Candidate.run_id, Candidate.id).where(
                        Candidate.sequence_sha256.in_(normalized)
                    )
                )
            )
    except Exception as exc:  # fail closed without persisting connection details
        return {
            "status": "unavailable",
            "error_class": type(exc).__name__,
            "query_mode": "bounded_candidate_sequence_sha256_in",
            "full_sequence_scan": False,
            "queried_hash_count": len(normalized),
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
        "queried_hash_count": len(normalized),
        "history_hit_count": len(hits),
        "history_hits": hits,
    }


def select_pg_new(
    proposals: list[dict[str, str]], history_receipt: dict[str, Any], limit: int = 12
) -> list[dict[str, str]]:
    if history_receipt.get("status") != "readback_verified":
        raise ValueError("cannot select PG-new proposals without an exact history readback")
    hits = {
        str(item["sequence_sha256"]).strip().lower()
        for item in history_receipt.get("history_hits", [])
    }
    selected = [row for row in proposals if row["sequence_sha256"].lower() not in hits]
    if len(selected) < limit:
        raise ValueError(f"bounded PepFlow pool yielded only {len(selected)} PG-new proposals")
    return selected[:limit]


async def run(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    parents = _load_parents(args.parent_queue, args.parent_readback)
    source_expansion._POLICY = source_expansion._read_policy(args.qd_json)
    source_expansion.TARGET_KEY = TARGET_KEY
    source_expansion.GENERATION = GENERATION
    source_expansion.OPERATOR_ID = OPERATOR_ID
    donors = load_pepflow_donors(args.donor_csv)
    local_history, prior_edits = _local_history(args.historical_proposals, parents)
    archive = json.loads(args.qd_json.read_text(encoding="utf-8"))
    pool = build_proposals(
        parents,
        donors,
        local_history,
        prior_edits,
        set(archive.get("empty_cell_ids", [])),
        limit=max(args.limit * 8, 48),
    )
    for row in pool:
        row["generation"] = str(GENERATION)
        row["seed"] = str(SEED)
        row["operator_id"] = OPERATOR_ID
        row["target_key"] = TARGET_KEY
        row["branch_key"] = TARGET_KEY
    history = await bounded_pg_history([row["sequence_sha256"] for row in pool])
    selected = select_pg_new(pool, history, args.limit)
    for row in selected:
        row["history_gate"] = "postgresql_exact_candidate_hash_preflight_passed"
        row["proposal_mode"] = "pepflow_qd_neighbor_1aa"
    proposals_path = output_dir / "proposals.csv"
    with proposals_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)
    history_path = output_dir / "pg_bounded_history_receipt.json"
    history_receipt = {
        "schema_version": "ampgent.angpt1-generation3-bounded-sequence-history.1",
        **history,
        "selected_pg_new_count": len(selected),
        "selected_sequence_sha256s": [row["sequence_sha256"] for row in selected],
        "materialization_performed": False,
    }
    history_path.write_text(
        json.dumps(history_receipt, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "schema_version": "ampgent.angpt1-pepflow-qd-neighbor-generation.2",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "target_key": TARGET_KEY,
        "source": "PepFlow",
        "generation": GENERATION,
        "seed": SEED,
        "operator_id": OPERATOR_ID,
        "parent_run_id": PARENT_RUN_ID,
        "authoritative_parent_candidate_ids": list(PARENT_IDS),
        "parent_count": len(parents),
        "parent_readback": {
            "path": str(args.parent_readback),
            "sha256": sha256_file(args.parent_readback),
            "candidate_count": 3,
            "evaluation_count": EXPECTED_PARENT_EVALS,
            "identity_drift": 0,
        },
        "donor_artifact": str(args.donor_csv),
        "donor_artifact_sha256": sha256_file(args.donor_csv),
        "donor_count": len(donors),
        "archive_json": str(args.qd_json),
        "archive_sha256": sha256_file(args.qd_json),
        "fixed_archive_cell_count": 2160,
        "candidate_pool_count": len(pool),
        "proposal_count": len(selected),
        "history_preflight": history_receipt,
        "fallback_operator": "not_used; one-aa pool satisfied twelve PG-new rows",
        "full_reports_scan": False,
        "proposal_csv_sha256": sha256_file(proposals_path),
        "gpu_rosetta_md_submitted": False,
        "materialization_performed": False,
        "pool_a_admitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-queue", type=Path, required=True)
    parser.add_argument("--parent-readback", type=Path, required=True)
    parser.add_argument("--qd-json", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, required=True)
    parser.add_argument("--historical-proposals", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
