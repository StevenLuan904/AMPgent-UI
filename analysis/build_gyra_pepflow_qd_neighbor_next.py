"""Build a local-only, PG-pending GyrA PepFlow QD-neighbor batch."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from qd_gap_directed_reciprocal_micrograft_v3 import _candidate_cell, phi

from pepagent.autoresearch_quality_diversity import BehaviorSpacePolicy
from pepagent.provenance.hashing import sha256_file


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _sha(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()


def _local_history(artifact_root: Path) -> tuple[set[str], set[tuple[str, int, str]], int]:
    sequence_hashes: set[str] = set()
    edits: set[tuple[str, int, str]] = set()
    csv_count = 0
    for path in sorted(artifact_root.rglob("*.csv")):
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                fields = set(reader.fieldnames or ())
                if not fields.intersection({"sequence", "parent_sequence"}):
                    continue
                csv_count += 1
                for row in reader:
                    for key in ("sequence", "parent_sequence", "donor_sequence"):
                        sequence = (row.get(key) or "").strip().upper()
                        if sequence:
                            sequence_hashes.add(_sha(sequence))
                    parent = (row.get("parent_sequence") or "").strip().upper()
                    child = (row.get("sequence") or "").strip().upper()
                    if not parent or len(parent) != len(child):
                        continue
                    changed = [
                        index
                        for index, (old, new) in enumerate(zip(parent, child, strict=True))
                        if old != new
                    ]
                    if len(changed) == 1:
                        index = changed[0]
                        edits.add((parent, index, child[index]))
        except (OSError, UnicodeError, csv.Error):
            continue
    return sequence_hashes, edits, csv_count


def _parents(score_csv: Path, queue_csv: Path) -> list[dict[str, str]]:
    scores = _rows(score_csv)
    queue = {row["sequence_sha256"]: row for row in _rows(queue_csv)}
    result = []
    for row in scores:
        identity = queue.get(row["sequence_sha256"])
        if identity is None:
            raise ValueError("authoritative queue is missing a selected parent")
        if row.get("display_eligible", "").lower() != "true":
            continue
        if int(row.get("activity_model_support_count_calibrated", "0")) < 2:
            continue
        if row.get("excellent_sequence_stage_calibrated", "").lower() != "true":
            continue
        result.append(
            {
                **row,
                "candidate_id": identity["candidate_id"],
                "run_id": identity["run_id"],
                "qd_cell": identity["qd_cell"],
            }
        )
    if len(result) != 8:
        raise ValueError(f"expected 8 receipt-backed QD parents, found {len(result)}")
    return sorted(result, key=lambda row: row["candidate_id"])


def _donors(path: Path) -> list[dict[str, str]]:
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for row in _rows(path):
        if row.get("donor_source", "").casefold() != "pepflow":
            continue
        fragment = (row.get("donor_fragment") or "").strip().upper()
        if len(fragment) != 1 or not fragment.isalpha():
            continue
        key = (row.get("donor_candidate_id", ""), fragment)
        unique.setdefault(key, {**row, "donor_fragment": fragment})
    return [unique[key] for key in sorted(unique)]


def generate(
    parents: list[dict[str, str]],
    donors: list[dict[str, str]],
    archive: dict[str, Any],
    historical_hashes: set[str],
    prior_edits: set[tuple[str, int, str]],
    *,
    limit: int = 12,
    generation: int = 3,
    seed: int = 20260904,
) -> list[dict[str, Any]]:
    policy = BehaviorSpacePolicy.model_validate(archive["policy"])
    empty_cells = set(archive["empty_cell_ids"])
    seen_hashes = set(historical_hashes)
    seen_cells: set[str] = set()
    proposals: list[dict[str, Any]] = []
    for parent in parents:
        parent_sequence = parent["sequence"].strip().upper()
        before = phi(parent_sequence)
        for donor in donors:
            residue = donor["donor_fragment"]
            for position, old_residue in enumerate(parent_sequence):
                if residue == old_residue:
                    continue
                edit = (parent_sequence, position, residue)
                if edit in prior_edits:
                    continue
                child = parent_sequence[:position] + residue + parent_sequence[position + 1 :]
                digest = _sha(child)
                if digest in seen_hashes:
                    continue
                cell = _candidate_cell(child, policy)
                if cell is None or cell not in empty_cells or cell in seen_cells:
                    continue
                after = phi(child)
                proposals.append(
                    {
                        "proposal_id": f"proposal-{digest[:20]}",
                        "sequence": child,
                        "sequence_sha256": digest,
                        "branch_key": "gyra",
                        "target_key": "GyrA",
                        "generation": generation,
                        "seed": seed,
                        "operator_id": "gyrA-pepflow-qd-neighbor-next-1aa-v1",
                        "proposal_mode": "pepflow_receipt_parent_empty_cell_neighbor",
                        "parent_run_id": parent["run_id"],
                        "parent_candidate_id": parent["candidate_id"],
                        "parent_sequence": parent_sequence,
                        "parent_sequence_sha256": parent["sequence_sha256"],
                        "parent_qd_cell": parent["qd_cell"],
                        "edit_position_zero_based": position,
                        "edit_position_1based": position + 1,
                        "from_residue": old_residue,
                        "to_residue": residue,
                        "donor_candidate_id": donor.get("donor_candidate_id", ""),
                        "donor_source": "PepFlow",
                        "donor_sequence": donor.get("donor_sequence", ""),
                        "donor_fragment": residue,
                        "provisional_qd_cell": cell,
                        "target_cell_hit_provisional": "true",
                        "delta_phi": json.dumps(
                            {
                                "axes": [
                                    "charge_density",
                                    "hydrophobicity",
                                    "hydrophobic_moment",
                                    "length",
                                ],
                                "parent_to_child": [
                                    after_value - before_value
                                    for after_value, before_value in zip(
                                        after, before, strict=True
                                    )
                                ],
                            },
                            sort_keys=True,
                        ),
                        "historical_pg_gate": "pending",
                        "materialization_status": "proposed_not_materialized",
                        "qd_contribution_status": "provisional_only",
                        "candidate_identity_status": "proposal_only",
                    }
                )
                seen_hashes.add(digest)
                seen_cells.add(cell)
                if len(proposals) == limit:
                    return proposals
    return proposals


def build(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    parents = _parents(args.parent_scores, args.parent_queue)
    donors = _donors(args.donor_csv)
    archive = json.loads(args.archive_json.read_text(encoding="utf-8"))
    historical_hashes, prior_edits, scanned_csv_count = _local_history(args.artifact_root)
    proposals = generate(
        parents,
        donors,
        archive,
        historical_hashes,
        prior_edits,
        limit=args.limit,
        generation=args.generation,
        seed=args.seed,
    )
    if not proposals:
        raise ValueError("no local-only empty-cell proposals available")
    proposal_path = output_dir / "proposals.csv"
    with proposal_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(proposals[0]))
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.gyra-pepflow-qd-neighbor-next-generation.1",
        "operator_id": "gyrA-pepflow-qd-neighbor-next-1aa-v1",
        "target_key": "GyrA",
        "generation": args.generation,
        "seed": args.seed,
        "proposal_count": len(proposals),
        "parent_count": len(parents),
        "pepflow_donor_count": len(donors),
        "local_artifact_csv_count": scanned_csv_count,
        "local_history_sequence_hash_count": len(historical_hashes),
        "local_prior_edit_count": len(prior_edits),
        "fixed_qd_cell_count": len(archive["empty_cell_ids"]) + len(archive["covered_cell_ids"]),
        "provisional_empty_cell_count": len(
            {row["provisional_qd_cell"] for row in proposals}
        ),
        "historical_pg_gate": "pending",
        "materialization_status": "proposed_not_materialized",
        "qd_status": "provisional_only",
        "candidate_identity_status": "proposal_only",
        "postgresql_reads": 0,
        "postgresql_writes": 0,
        "structure_status": "not_created",
        "gpu_rosetta_md_submitted": False,
        "proposal_csv_sha256": sha256_file(proposal_path),
    }
    (output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-scores", type=Path, required=True)
    parser.add_argument("--parent-queue", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--generation", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260904)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
