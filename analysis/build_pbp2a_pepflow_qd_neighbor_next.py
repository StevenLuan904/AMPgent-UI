"""Build a local-only, PG-pending PBP2a PepFlow QD-neighbor batch."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from build_gyra_pepflow_qd_neighbor_next import (
    _candidate_cell,
    _donors,
    _local_history,
    _rows,
    phi,
)
from build_gyra_pepflow_qd_neighbor_next import (
    generate as generate_neighbors,
)

from pepagent.autoresearch_quality_diversity import BehaviorSpacePolicy
from pepagent.provenance.hashing import sha256_file


def _read(path: Path) -> list[dict[str, str]]:
    return _rows(path)


def load_authoritative_parents(
    scores_csv: Path,
    qd_csv: Path,
    pg_verification: Path,
    prepared_receipt: Path,
) -> list[dict[str, str]]:
    scores = {row["sequence_sha256"]: row for row in _read(scores_csv)}
    replacements = [
        row for row in _read(qd_csv) if row.get("replacement", "").lower() == "true"
    ]
    evidence = json.loads(pg_verification.read_text(encoding="utf-8"))
    prepared = json.loads(prepared_receipt.read_text(encoding="utf-8"))
    evidence_rows = evidence.get("candidate_evidence", [])
    expected_ids = set(prepared.get("candidate_ids", []))
    if len(replacements) != 5 or len(evidence_rows) != 5 or len(expected_ids) != 5:
        raise ValueError("authoritative parent replacement count is not exactly five")
    if evidence.get("candidate_count") != 5 or not evidence.get("per_candidate_evidence_count_17"):
        raise ValueError("authoritative parent evidence is not 5 candidates with 17 evaluations")
    run_id = str(evidence.get("run", {}).get("run_id", ""))
    if not run_id or run_id != prepared.get("run_id"):
        raise ValueError("authoritative parent run identity drifted")
    by_hash = {row["sequence_sha256"]: row for row in replacements}
    parents: list[dict[str, str]] = []
    for item in evidence_rows:
        candidate_id = str(item["candidate_id"])
        sequence_hash = str(item["sequence_sha256"])
        if candidate_id not in expected_ids or sequence_hash not in by_hash:
            raise ValueError("authoritative parent UUID/hash is missing from replacement queue")
        qd_row = by_hash[sequence_hash]
        score = scores.get(sequence_hash)
        sequence = score.get("sequence", "").strip().upper() if score else ""
        if score is None or hashlib.sha256(sequence.encode("utf-8")).hexdigest() != sequence_hash:
            raise ValueError("authoritative parent sequence identity drifted")
        parents.append(
            {
                "candidate_id": candidate_id,
                "run_id": run_id,
                "sequence": sequence,
                "sequence_sha256": sequence_hash,
                "qd_cell": qd_row.get("actual_cell_id", ""),
                "generation": score.get("generation", "132"),
            }
        )
    if {row["candidate_id"] for row in parents} != expected_ids:
        raise ValueError("prepared parent UUID coverage drifted")
    return sorted(parents, key=lambda row: row["candidate_id"])


def diagnose_candidate_space(
    parents: list[dict[str, str]],
    donors: list[dict[str, str]],
    archive: dict[str, Any],
    historical_hashes: set[str],
    prior_edits: set[tuple[str, int, str]],
) -> dict[str, int | str]:
    policy = BehaviorSpacePolicy.model_validate(archive["policy"])
    empty_cells = set(archive["empty_cell_ids"])
    counts = {
        "neighbor_considered": 0,
        "same_residue_skipped": 0,
        "prior_edit_skipped": 0,
        "historical_sequence_skipped": 0,
        "non_empty_cell_skipped": 0,
        "duplicate_target_cell_skipped": 0,
        "eligible_unmaterialized": 0,
    }
    seen_hashes = set(historical_hashes)
    seen_cells: set[str] = set()
    for parent in parents:
        sequence = parent["sequence"].strip().upper()
        for donor in donors:
            residue = donor["donor_fragment"]
            for position, old_residue in enumerate(sequence):
                if residue == old_residue:
                    counts["same_residue_skipped"] += 1
                    continue
                counts["neighbor_considered"] += 1
                edit = (sequence, position, residue)
                if edit in prior_edits:
                    counts["prior_edit_skipped"] += 1
                    continue
                child = sequence[:position] + residue + sequence[position + 1 :]
                digest = hashlib.sha256(child.encode("utf-8")).hexdigest()
                if digest in seen_hashes:
                    counts["historical_sequence_skipped"] += 1
                    continue
                cell = _candidate_cell(child, policy)
                if cell is None or cell not in empty_cells:
                    counts["non_empty_cell_skipped"] += 1
                    continue
                if cell in seen_cells:
                    counts["duplicate_target_cell_skipped"] += 1
                    continue
                counts["eligible_unmaterialized"] += 1
                seen_hashes.add(digest)
                seen_cells.add(cell)
    counts["fixed_qd_cell_count"] = len(archive["empty_cell_ids"]) + len(
        archive["covered_cell_ids"]
    )
    counts["empty_cell_count"] = len(empty_cells)
    counts["failure_reason"] = (
        "all_local_one_aa_neighbors_excluded_by_history_edit_or_non_empty_cell"
        if counts["eligible_unmaterialized"] == 0
        else "eligible_neighbors_exist_but_generation_limit_or_ordering_requires_review"
    )
    return counts


def generate_two_aa_fallback(
    parents: list[dict[str, str]],
    donors: list[dict[str, str]],
    archive: dict[str, Any],
    historical_hashes: set[str],
    prior_edits: set[tuple[str, int, str]],
    *,
    limit: int,
    generation: int,
    seed: int,
) -> list[dict[str, Any]]:
    """Try non-adjacent two-residue PepFlow micrografts after 1-aa exhaustion."""
    policy = BehaviorSpacePolicy.model_validate(archive["policy"])
    empty_cells = set(archive["empty_cell_ids"])
    seen_hashes = set(historical_hashes)
    seen_cells: set[str] = set()
    result: list[dict[str, Any]] = []
    for parent in parents:
        sequence = parent["sequence"].strip().upper()
        before = phi(sequence)
        for donor_index, first in enumerate(donors):
            for second in donors[donor_index + 1 :]:
                first_residue = first["donor_fragment"]
                second_residue = second["donor_fragment"]
                for left in range(len(sequence)):
                    if sequence[left] == first_residue:
                        continue
                    for right in range(left + 2, len(sequence)):
                        if sequence[right] == second_residue:
                            continue
                        if (
                            (sequence, left, first_residue) in prior_edits
                            or (sequence, right, second_residue) in prior_edits
                        ):
                            continue
                        child = (
                            sequence[:left]
                            + first_residue
                            + sequence[left + 1 : right]
                            + second_residue
                            + sequence[right + 1 :]
                        )
                        digest = hashlib.sha256(child.encode("utf-8")).hexdigest()
                        if digest in seen_hashes:
                            continue
                        cell = _candidate_cell(child, policy)
                        if cell is None or cell not in empty_cells or cell in seen_cells:
                            continue
                        after = phi(child)
                        result.append(
                            {
                                "proposal_id": f"proposal-{digest[:20]}",
                                "sequence": child,
                                "sequence_sha256": digest,
                                "branch_key": "pbp2a",
                                "target_key": "PBP2a",
                                "generation": generation,
                                "seed": seed,
                                "operator_id": "pbp2a-pepflow-qd-neighbor-next-2aa-v1",
                                "proposal_mode": "pepflow_two_aa_nonadjacent_micrograft_fallback",
                                "parent_run_id": parent["run_id"],
                                "parent_candidate_id": parent["candidate_id"],
                                "parent_sequence": sequence,
                                "parent_sequence_sha256": parent["sequence_sha256"],
                                "parent_qd_cell": parent["qd_cell"],
                                "edit_positions_zero_based": f"{left};{right}",
                                "edit_positions_1based": f"{left + 1};{right + 1}",
                                "edit_length": 2,
                                "from_residues": f"{sequence[left]};{sequence[right]}",
                                "to_residues": f"{first_residue};{second_residue}",
                                "donor_candidate_ids": (
                                    f"{first.get('donor_candidate_id', '')};"
                                    f"{second.get('donor_candidate_id', '')}"
                                ),
                                "donor_source": "PepFlow",
                                "donor_fragments": f"{first_residue};{second_residue}",
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
                        if len(result) == limit:
                            return result
    return result


def _write_zero_receipts(
    output_dir: Path,
    *,
    args: argparse.Namespace,
    parents: list[dict[str, str]],
    donors: list[dict[str, str]],
    diagnosis: dict[str, int | str],
) -> None:
    common = {
        "target_key": "PBP2a",
        "generation": args.generation,
        "proposal_count": 0,
        "historical_pg_gate": "pending",
        "materialization_status": "proposed_not_materialized",
        "postgresql_reads": 0,
        "postgresql_writes": 0,
        "gpu_rosetta_md_submitted": False,
        "candidate_space_diagnosis": diagnosis,
    }
    (output_dir / "generation_receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "ampgent.pbp2a-pepflow-qd-neighbor-next-generation.1",
                "operator_id": "pbp2a-pepflow-qd-neighbor-next-1aa-v1",
                "seed": args.seed,
                "parent_count": len(parents),
                "authoritative_parent_candidate_ids": [
                    row["candidate_id"] for row in parents
                ],
                "authoritative_parent_run_id": parents[0]["run_id"],
                "pepflow_donor_count": len(donors),
                "qd_status": "not_run_no_proposals",
                "generation_strategy": "exhausted_one_aa_then_two_aa_fallback",
                "candidate_identity_status": "proposal_only",
                **common,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "score_all").mkdir(exist_ok=True)
    (output_dir / "challenger").mkdir(exist_ok=True)
    score = {
        "schema_version": "ampgent.pbp2a-pepflow-qd-neighbor-next-score.1",
        "status": "not_run_no_proposals",
        "formal_12_complete_count": 0,
        "display_eligible_count": 0,
        "candidate_space_diagnosis": diagnosis,
    }
    (output_dir / "score_all" / "receipt.json").write_text(
        json.dumps(score, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    challenger = {
        "schema_version": "ampgent.pbp2a-pepflow-qd-neighbor-next-challenger.1",
        "status": "not_run_no_proposals",
        "reviewed_candidate_count": 0,
        "challenger_no_conflict_count": 0,
        "challenger_conflict_count": 0,
        "missing_verified_runtimes": ["apex", "peptiverse"],
    }
    (output_dir / "challenger" / "receipt.json").write_text(
        json.dumps(challenger, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    qd = {
        "schema_version": "ampgent.pbp2a-pepflow-qd-neighbor-next-qd.1",
        "status": "not_run_no_proposals",
        "fixed_cell_count": diagnosis["fixed_qd_cell_count"],
        "quality_eligible_count": 0,
        "new_cell_count": 0,
        "replacement_count": 0,
    }
    (output_dir / "provisional_qd_receipt.json").write_text(
        json.dumps(qd, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    close = {
        "schema_version": "ampgent.pbp2a-pepflow-qd-neighbor-next-close.1",
        "target_key": "PBP2a",
        "generation": args.generation,
        "proposal_count": 0,
        "formal12_count": 0,
        "display_count": 0,
        "support_ge_2_count": 0,
        "challenger_reviewed_count": 0,
        "provisional_qd": {
            "status": "not_run_no_proposals",
            "new_cell_count": 0,
            "replacement_count": 0,
        },
        "persistence": {
            "historical_pg_gate": "pending",
            "materialization_status": "proposed_not_materialized",
            "pool_a_admitted": False,
            "postgresql_reads": 0,
            "postgresql_writes": 0,
            "materialization_writes": 0,
            "exact_history_status": "not_run_no_proposals",
        },
        "candidate_space_diagnosis": diagnosis,
        "execution_safety": {"structure_status": "not_created", "gpu_rosetta_md_submitted": False},
    }
    (output_dir / "close_receipt.json").write_text(
        json.dumps(close, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def build(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    parents = load_authoritative_parents(
        args.parent_scores,
        args.parent_qd,
        args.pg_verification,
        args.prepared_receipt,
    )
    donors = _donors(args.donor_csv)
    archive_payload = json.loads(args.archive_json.read_text(encoding="utf-8"))
    archive = archive_payload.get("branches", {}).get("pbp2a", archive_payload)
    historical_hashes, prior_edits, scanned_csv_count = _local_history(args.artifact_root)
    proposals = generate_neighbors(
        parents,
        donors,
        archive,
        historical_hashes,
        prior_edits,
        limit=args.limit,
        generation=args.generation,
        seed=args.seed,
    )
    generation_strategy = "one_aa_empty_cell_neighbor"
    if not proposals:
        proposals = generate_two_aa_fallback(
            parents,
            donors,
            archive,
            historical_hashes,
            prior_edits,
            limit=args.limit,
            generation=args.generation,
            seed=args.seed,
        )
        generation_strategy = "two_aa_nonadjacent_pepflow_micrograft_fallback"
    if not proposals:
        diagnosis = diagnose_candidate_space(
            parents, donors, archive, historical_hashes, prior_edits
        )
        _write_zero_receipts(
            output_dir, args=args, parents=parents, donors=donors, diagnosis=diagnosis
        )
        return {
            "proposal_count": 0,
            "candidate_space_diagnosis": diagnosis,
        }
    operator_id = (
        "pbp2a-pepflow-qd-neighbor-next-2aa-v1"
        if generation_strategy == "two_aa_nonadjacent_pepflow_micrograft_fallback"
        else "pbp2a-pepflow-qd-neighbor-next-1aa-v1"
    )
    proposal_mode = (
        "pepflow_two_aa_nonadjacent_micrograft_fallback"
        if generation_strategy == "two_aa_nonadjacent_pepflow_micrograft_fallback"
        else "pepflow_receipt_parent_empty_cell_neighbor"
    )
    for row in proposals:
        row.update(
            {
                "branch_key": "pbp2a",
                "target_key": "PBP2a",
                "operator_id": operator_id,
                "proposal_mode": proposal_mode,
            }
        )
    proposal_path = output_dir / "proposals.csv"
    with proposal_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(proposals[0]))
        writer.writeheader()
        writer.writerows(proposals)
    fixed_cell_count = len(archive["empty_cell_ids"]) + len(archive["covered_cell_ids"])
    receipt = {
        "schema_version": "ampgent.pbp2a-pepflow-qd-neighbor-next-generation.1",
        "operator_id": operator_id,
        "target_key": "PBP2a",
        "generation": args.generation,
        "seed": args.seed,
        "proposal_count": len(proposals),
        "generation_strategy": generation_strategy,
        "parent_count": len(parents),
        "authoritative_parent_candidate_ids": [row["candidate_id"] for row in parents],
        "authoritative_parent_run_id": parents[0]["run_id"],
        "pepflow_donor_count": len(donors),
        "fixed_qd_cell_count": fixed_cell_count,
        "provisional_empty_cell_count": len(
            {row["provisional_qd_cell"] for row in proposals}
        ),
        "local_artifact_csv_count": scanned_csv_count,
        "local_history_sequence_hash_count": len(historical_hashes),
        "local_prior_edit_count": len(prior_edits),
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
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-scores", type=Path, required=True)
    parser.add_argument("--parent-qd", type=Path, required=True)
    parser.add_argument("--pg-verification", type=Path, required=True)
    parser.add_argument("--prepared-receipt", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--generation", type=int, default=133)
    parser.add_argument("--seed", type=int, default=20260904)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
