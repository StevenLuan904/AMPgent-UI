"""Bounded FGF2 PepFlow-source grafts from authoritative PepGLAD QD parents."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from autoresearch_safety_rescue_variants import _historical_sequence_sha256s
from fgf2_qd_gap_v5 import cell, phi

from pepagent.provenance.hashing import sha256_file, sha256_json

OPERATOR_ID = "fgf2-pepflow-source-expansion-1aa-v1"
TARGET_KEY = "fgf2"
SEED = 20260904
GENERATION = 4
_POLICY: Any


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _row_sha(row: dict[str, str]) -> str:
    return sha256_json({key: row[key] for key in sorted(row)})


def _truth(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def load_authoritative_parents(
    parent_scores: Path, authority_queue: Path, qd_json: Path
) -> list[dict[str, str]]:
    with parent_scores.open(encoding="utf-8-sig", newline="") as stream:
        score_rows = list(csv.DictReader(stream))
    with authority_queue.open(encoding="utf-8-sig", newline="") as stream:
        authority_rows = list(csv.DictReader(stream))
    qd = json.loads(qd_json.read_text(encoding="utf-8"))
    elite_hashes = {
        sha256_text(str(item.get("sequence", "")).strip().upper())
        for item in qd.get("elites", [])
        if item.get("sequence")
    }
    authority_by_sha = {
        row["sequence_sha256"]: row
        for row in authority_rows
        if (row.get("candidate_id") or row.get("authoritative_candidate_id"))
        and row.get("sequence_sha256")
    }
    selected: list[dict[str, str]] = []
    for row in score_rows:
        sequence = row.get("sequence", "").strip().upper()
        sequence_sha = row.get("sequence_sha256", "")
        authority = authority_by_sha.get(sequence_sha)
        if not authority or sequence_sha not in elite_hashes:
            continue
        if not (
            _truth(row.get("display_eligible"))
            and _truth(row.get("formal_12_complete"))
            and int(row.get("activity_model_support_count_calibrated", 0) or 0) >= 2
        ):
            continue
        authority_candidate_id = authority.get("candidate_id") or authority.get(
            "authoritative_candidate_id"
        )
        selected.append(
            {
                "candidate_id": authority_candidate_id,
                "parent_run_id": authority["run_id"],
                "sequence": sequence,
                "sequence_sha256": sequence_sha,
                "qd_cell": row.get("parent_qd_cell", ""),
                "display_eligible": "true",
                "activity_support_calibrated": row.get(
                    "activity_model_support_count_calibrated", ""
                ),
            }
        )
    if not selected:
        raise ValueError("no authoritative FGF2 PepGLAD QD parents resolved")
    if len({row["candidate_id"] for row in selected}) != len(selected):
        raise ValueError("parent authoritative candidate identity is not unique")
    return sorted(selected, key=lambda row: (row["qd_cell"], row["sequence_sha256"]))


def load_pepflow_donors(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    donors: dict[tuple[str, str, str], dict[str, str]] = {}
    for row_number, row in enumerate(source_rows, start=2):
        if row.get("donor_source", "").strip().lower() != "pepflow":
            continue
        fragment = row.get("donor_fragment", "").strip().upper()
        donor_sequence = row.get("donor_sequence", "").strip().upper()
        donor_id = row.get("donor_candidate_id", "").strip()
        if len(fragment) != 1 or not donor_sequence or not donor_id:
            continue
        key = (donor_id, fragment, donor_sequence)
        donors.setdefault(
            key,
            {
                "donor_candidate_id": donor_id,
                "donor_source": "PepFlow",
                "donor_sequence": donor_sequence,
                "donor_fragment": fragment,
                "donor_artifact": str(path),
                "donor_row_number": str(row_number),
                "donor_row_sha256": _row_sha(row),
            },
        )
    if not donors:
        raise ValueError("PepFlow donor artifact contains no one-residue donor rows")
    return sorted(
        donors.values(),
        key=lambda row: (row["donor_candidate_id"], row["donor_fragment"]),
    )


def load_prior_edits(paths: Iterable[Path]) -> set[tuple[str, int, str]]:
    edits: set[tuple[str, int, str]] = set()
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                parent_sha = row.get("parent_sequence_sha256", "")
                position = row.get(
                    "acceptor_start_zero_based",
                    row.get("edit_position_zero_based", ""),
                )
                residue = row.get("to_residue", row.get("donor_fragment", ""))
                if parent_sha and position not in {"", None} and residue:
                    edits.add((parent_sha, int(position), residue.upper()))
    return edits


def build_proposals(
    parents: list[dict[str, str]],
    donors: list[dict[str, str]],
    history: set[str],
    prior_edits: set[tuple[str, int, str]],
    empty_cells: set[str],
    *,
    limit: int = 12,
) -> list[dict[str, str]]:
    candidates_by_parent: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen = set(history)
    for parent in parents:
        sequence = parent["sequence"]
        for donor in donors:
            residue = donor["donor_fragment"]
            for position in range(len(sequence)):
                if sequence[position] == residue:
                    continue
                if (parent["sequence_sha256"], position, residue) in prior_edits:
                    continue
                child = sequence[:position] + residue + sequence[position + 1 :]
                child_sha = sha256_text(child)
                target_cell = cell(child, _POLICY)
                if child_sha in seen or target_cell not in empty_cells:
                    continue
                before, after = phi(sequence), phi(child)
                row = {
                    "sequence": child,
                    "sequence_sha256": child_sha,
                    "branch_key": TARGET_KEY,
                    "target_key": TARGET_KEY,
                    "source": "PepFlow",
                    "generation": str(GENERATION),
                    "seed": str(SEED),
                    "operator_id": OPERATOR_ID,
                    "proposal_mode": "pepflow_qd_neighbor_1aa",
                    "parent_run_id": parent["parent_run_id"],
                    "parent_candidate_id": parent["candidate_id"],
                    "parent_sequence": sequence,
                    "parent_sequence_sha256": parent["sequence_sha256"],
                    "parent_qd_cell": parent["qd_cell"],
                    "acceptor_start_zero_based": str(position),
                    "from_residue": sequence[position],
                    "to_residue": residue,
                    **donor,
                    "actual_cell_preflight": target_cell,
                    "target_cell": target_cell,
                    "target_cell_hit_preflight": "true",
                    "delta_phi_skill": json.dumps(
                        {
                            "axes": [
                                "charge_density",
                                "hydrophobicity",
                                "hydrophobic_moment",
                                "length",
                            ],
                            "acceptor_to_child": [
                                after_value - before_value
                                for before_value, after_value in zip(before, after, strict=True)
                            ],
                        },
                        sort_keys=True,
                    ),
                    "history_gate": "postgresql_exact_sequence_and_prior_edit_passed",
                    "parent_display_eligible": "true",
                    "parent_activity_support_calibrated": parent["activity_support_calibrated"],
                }
                candidates_by_parent[parent["candidate_id"]].append(row)
                seen.add(child_sha)

    for rows in candidates_by_parent.values():
        rows.sort(key=lambda row: (row["target_cell"], row["sequence"], row["donor_row_sha256"]))
    result: list[dict[str, str]] = []
    parent_ids = [parent["candidate_id"] for parent in parents]
    while len(result) < limit and any(candidates_by_parent[parent_id] for parent_id in parent_ids):
        for parent_id in parent_ids:
            if len(result) >= limit:
                break
            if candidates_by_parent[parent_id]:
                result.append(candidates_by_parent[parent_id].pop(0))
    return result


def _read_policy(path: Path) -> Any:
    from pepagent.autoresearch_quality_diversity import BehaviorSpacePolicy

    payload = json.loads(path.read_text(encoding="utf-8"))
    return BehaviorSpacePolicy.model_validate(payload["policy"])


async def run(args: argparse.Namespace) -> None:
    global _POLICY
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    _POLICY = _read_policy(args.qd_json)
    parents = load_authoritative_parents(args.parent_scores, args.authority_queue, args.qd_json)
    donors = load_pepflow_donors(args.donor_csv)
    prior_edits = load_prior_edits(args.historical_proposals)
    history = await _historical_sequence_sha256s()
    proposals = build_proposals(
        parents,
        donors,
        history,
        prior_edits,
        set(json.loads(args.qd_json.read_text(encoding="utf-8")).get("empty_cell_ids", [])),
        limit=args.limit,
    )
    if not proposals:
        raise RuntimeError("no PG-new FGF2 PepFlow proposals reached an archive empty cell")
    output = output_dir / "proposals.csv"
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(proposals[0]))
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.fgf2-pepflow-source-expansion-generation.1",
        "target_key": TARGET_KEY,
        "source": "PepFlow",
        "operator_id": OPERATOR_ID,
        "seed": SEED,
        "generation": GENERATION,
        "parent_count": len(parents),
        "authoritative_parent_candidate_ids": [row["candidate_id"] for row in parents],
        "donor_count": len(donors),
        "donor_artifact": str(args.donor_csv),
        "proposal_count": len(proposals),
        "empty_cell_preflight_count": len(proposals),
        "historical_sequence_count": len(history),
        "historical_edit_count": len(prior_edits),
        "pg_exact_observed": True,
        "gpu_rosetta_md_submitted": False,
        "proposal_csv_sha256": sha256_file(output),
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-scores", type=Path, required=True)
    parser.add_argument("--authority-queue", type=Path, required=True)
    parser.add_argument("--qd-json", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, required=True)
    parser.add_argument("--historical-proposals", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
