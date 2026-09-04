"""Build a local-only target-conditioned PepMLM x PepFlow motif batch."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from itertools import permutations
from pathlib import Path
from typing import Any

from build_pbp2a_pepmlm_pepflow_hybrid import (
    archive_cells,
    candidate_cell,
    phi,
    select_donors,
)

OPERATOR_ID = "pbp2a-pepmlm-pepflow-hybrid-local-v2"
TARGET_KEY = "PBP2a"
PARENT_SOURCE = "PepMLM-target-conditioned"
DONOR_SOURCE = "PepFlow"
FORBIDDEN_POSITIONS = frozenset({2, 10, 19})
AA = frozenset("ACDEFGHIKLMNPQRSTVWY")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _changed(parent: str, child: str) -> tuple[tuple[int, ...], str] | None:
    if not parent or not child or len(parent) != len(child):
        return None
    positions = tuple(
        index for index, (old, new) in enumerate(zip(parent, child, strict=True)) if old != new
    )
    return (positions, "".join(child[index] for index in positions)) if positions else None


def local_history(
    root: Path,
) -> tuple[set[str], set[tuple[str, int, str]], set[tuple[str, tuple[int, ...], str]], int]:
    sequence_hashes: set[str] = set()
    prior_edits: set[tuple[str, int, str]] = set()
    prior_motifs: set[tuple[str, tuple[int, ...], str]] = set()
    csv_count = 0
    for path in root.rglob("*.csv"):
        try:
            rows = read_csv(path)
        except (OSError, UnicodeError, csv.Error):
            continue
        if not rows or not any(
            key in rows[0] for key in ("sequence", "parent_sequence", "donor_sequence")
        ):
            continue
        csv_count += 1
        for row in rows:
            sequences = {
                (row.get(key) or "").strip().upper()
                for key in ("sequence", "parent_sequence", "donor_sequence")
            }
            sequence_hashes.update(sha256_text(value) for value in sequences if value)
            parent = (row.get("parent_sequence") or "").strip().upper()
            child = (row.get("sequence") or "").strip().upper()
            change = _changed(parent, child)
            if change is None:
                continue
            positions, motif = change
            prior_motifs.add((parent, positions, motif))
            prior_edits.update((parent, position, child[position]) for position in positions)
    return sequence_hashes, prior_edits, prior_motifs, csv_count


def load_parents(path: Path, excluded_hashes: set[str]) -> list[dict[str, str]]:
    parents = read_csv(path)
    if len(parents) != 5 or len({row.get("run_id") for row in parents}) != 1:
        raise ValueError("exactly five PepMLM parents from one run are required")
    for row in parents:
        sequence = row.get("sequence", "").strip().upper()
        if (
            row.get("display_eligible", "").lower() != "true"
            or int(row.get("activity_model_support_count_calibrated", "0")) < 2
            or row.get("excellent_sequence_stage_calibrated", "").lower() != "true"
            or row.get("hemopi2_conflict_status") != "no_conflict"
            or row.get("source") != "PepMLM"
            or sha256_text(sequence) in excluded_hashes
        ):
            raise ValueError("parent is not a verified PepMLM elite or is a support=0 exclusion")
        if sha256_text(sequence) != row.get("sequence_sha256"):
            raise ValueError("PepMLM parent sequence hash drifted")
    return sorted(parents, key=lambda row: row["candidate_id"])


def motif_library(donors: list[dict[str, str]]) -> list[dict[str, Any]]:
    residues = [row["donor_residue"] for row in donors]
    donor_by_residue = {row["donor_residue"]: row for row in donors}
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for size in (2, 3):
        for permutation in permutations(residues, size):
            motif = "".join(permutation)
            if motif in seen or not all(residue in AA for residue in motif):
                continue
            seen.add(motif)
            result.append(
                {
                    "motif": motif,
                    "donor_candidate_ids": ";".join(
                        donor_by_residue[residue].get("donor_candidate_id", "")
                        for residue in permutation
                    ),
                    "donor_fragments": ";".join(
                        donor_by_residue[residue]["donor_fragment"] for residue in permutation
                    ),
                }
            )
    return result


def _candidate(
    parent: dict[str, str],
    motif: dict[str, Any],
    position: int,
    archive: Path,
    *,
    generation: int,
    seed: int,
    variant: str,
) -> dict[str, Any]:
    sequence = parent["sequence"].strip().upper()
    graft = motif["motif"]
    child = sequence[:position] + graft + sequence[position + len(graft) :]
    before, after = phi(sequence), phi(child)
    return {
        "branch_key": "pbp2a",
        "target_key": TARGET_KEY,
        "generation": generation,
        "seed": seed,
        "operator_id": OPERATOR_ID,
        "operator_variant": variant,
        "source_arm": "PepMLM-target-conditioned-ancestry_x_PepFlow-donor-motif",
        "parent_source": PARENT_SOURCE,
        "donor_source": DONOR_SOURCE,
        "parent_run_id": parent["run_id"],
        "parent_candidate_id": parent["candidate_id"],
        "parent_sequence": sequence,
        "parent_sequence_sha256": parent["sequence_sha256"],
        "parent_qd_cell": parent["qd_cell"],
        "edit_position_zero_based": position,
        "edit_positions_zero_based": ";".join(
            str(position + offset) for offset in range(len(graft))
        ),
        "edit_position_1based": position + 1,
        "edit_positions_1based": ";".join(
            str(position + offset + 1) for offset in range(len(graft))
        ),
        "edit_length": len(graft),
        "from_residues": sequence[position : position + len(graft)],
        "to_residues": graft,
        "edit": f"{position + 1}:{sequence[position : position + len(graft)]}>{graft}",
        "donor_candidate_ids": motif["donor_candidate_ids"],
        "donor_fragments": motif["donor_fragments"],
        "sequence": child,
        "sequence_sha256": sha256_text(child),
        "actual_cell_preflight": candidate_cell(child, archive),
        "target_empty_cell_preflight": candidate_cell(child, archive),
        "edit_fraction": len(graft) / len(sequence),
        "edit_fraction_limit": 0.25,
        "delta_phi": json.dumps(
            {
                "axes": [
                    "net_charge_over_length",
                    "hydrophobic_ratio",
                    "hydrophobic_moment",
                    "length",
                ],
                "parent_to_child": [
                    after_value - before_value
                    for after_value, before_value in zip(after, before, strict=True)
                ],
            },
            sort_keys=True,
        ),
        "historical_sequence_exclusion": "passed_local_reports_exact_history",
        "prior_edit_exclusion": "passed_local_reports_parent_position_to_history",
        "motif_history_exclusion": "passed_local_reports_parent_motif_history",
        "protected_key_motif_policy": (
            "preserve_charged_scaffold;mutate_only_low_attribution_neutral_positions"
            if variant == "activity_preserving_fallback"
            else "not_applied_primary_variant"
        ),
        "historical_pg_gate": "pending",
        "materialization_status": "proposed_not_materialized",
        "candidate_identity_status": "proposal_only",
        "qd_contribution_status": "provisional_only",
    }


def generate(
    parents: list[dict[str, str]],
    motifs: list[dict[str, Any]],
    history_hashes: set[str],
    prior_edits: set[tuple[str, int, str]],
    prior_motifs: set[tuple[str, tuple[int, ...], str]],
    archive: Path,
    *,
    generation: int,
    seed: int,
    limit: int,
    variant: str,
) -> tuple[list[dict[str, Any]], dict[str, int | str]]:
    if not 1 <= limit <= 12:
        raise ValueError("limit must be in [1, 12]")
    empty_cells, _, _ = archive_cells(archive)
    parent_cells = {candidate_cell(row["sequence"].strip().upper(), archive) for row in parents}
    registered_empty = empty_cells - parent_cells
    counts: dict[str, int | str] = {
        "motif_position_considered": 0,
        "edit_fraction_rejected": 0,
        "forbidden_position_rejected": 0,
        "same_residue_rejected": 0,
        "prior_edit_rejected": 0,
        "prior_motif_rejected": 0,
        "historical_sequence_rejected": 0,
        "non_empty_cell_rejected": 0,
        "duplicate_cell_rejected": 0,
        "protected_key_motif_rejected": 0,
        "eligible_unmaterialized": 0,
    }
    seen_hashes = set(history_hashes)
    seen_cells: set[str] = set()
    proposals: list[dict[str, Any]] = []
    parent_order = sorted(parents, key=lambda row: row["candidate_id"])
    if variant == "activity_preserving_fallback":
        motif_order = sorted(
            motifs,
            key=lambda row: (
                any(residue == "P" for residue in row["motif"]),
                row["motif"],
                row["donor_candidate_ids"],
            ),
        )
    else:
        motif_order = sorted(motifs, key=lambda row: (row["motif"], row["donor_candidate_ids"]))
    for parent in parent_order:
        sequence = parent["sequence"].strip().upper()
        for motif in motif_order:
            graft = motif["motif"]
            for position in range(3, len(sequence) - len(graft) + 1):
                counts["motif_position_considered"] += 1
                if position in FORBIDDEN_POSITIONS or any(
                    position + offset in FORBIDDEN_POSITIONS for offset in range(len(graft))
                ):
                    counts["forbidden_position_rejected"] += 1
                    continue
                if len(graft) / len(sequence) > 0.25:
                    counts["edit_fraction_rejected"] += 1
                    continue
                original = sequence[position : position + len(graft)]
                if variant == "activity_preserving_fallback" and not all(
                    residue in "AGLNSQTV" for residue in original
                ):
                    counts["protected_key_motif_rejected"] += 1
                    continue
                if any(old == new for old, new in zip(original, graft, strict=True)):
                    counts["same_residue_rejected"] += 1
                    continue
                positions = tuple(position + offset for offset in range(len(graft)))
                edit = [(sequence, item, graft[offset]) for offset, item in enumerate(positions)]
                if any(item in prior_edits for item in edit):
                    counts["prior_edit_rejected"] += 1
                    continue
                if (sequence, positions, graft) in prior_motifs:
                    counts["prior_motif_rejected"] += 1
                    continue
                child = sequence[:position] + graft + sequence[position + len(graft) :]
                digest = sha256_text(child)
                if digest in seen_hashes:
                    counts["historical_sequence_rejected"] += 1
                    continue
                cell = candidate_cell(child, archive)
                if cell not in registered_empty:
                    counts["non_empty_cell_rejected"] += 1
                    continue
                if cell in seen_cells:
                    counts["duplicate_cell_rejected"] += 1
                    continue
                proposals.append(
                    _candidate(
                        parent,
                        motif,
                        position,
                        archive,
                        generation=generation,
                        seed=seed,
                        variant=variant,
                    )
                )
                seen_hashes.add(digest)
                seen_cells.add(cell)
                counts["eligible_unmaterialized"] += 1
                if len(proposals) >= limit:
                    return proposals, counts
    counts["failure_reason"] = (
        "no_non_overlapping_motif_reached_registered_empty_cell"
        if not proposals
        else "generation_limit_or_deterministic_ordering_reached"
    )
    return proposals, counts


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write empty proposals")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build(args: argparse.Namespace) -> dict[str, Any]:
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    negative_hashes: set[str] = set()
    negative_csv = args.negative_fallback_csv
    if negative_csv.exists():
        negative_hashes = {
            sha256_text(row["sequence"].strip().upper())
            for row in read_csv(negative_csv)
            if row.get("sequence")
        }
    parents = load_parents(args.parents_csv, negative_hashes)
    donors = select_donors(read_csv(args.pepflow_csv))
    motifs = motif_library(donors)
    history_hashes, prior_edits, prior_motifs, csv_count = local_history(args.artifact_root)
    proposals, funnel = generate(
        parents,
        motifs,
        history_hashes,
        prior_edits,
        prior_motifs,
        args.archive,
        generation=args.generation,
        seed=args.seed,
        limit=args.limit,
        variant=args.variant,
    )
    if not proposals:
        raise ValueError(f"no local proposal: {funnel}")
    proposal_path = output / "proposals.csv"
    write_csv(proposal_path, proposals)
    displacement_path = output / "property_displacement.csv"
    displacement_rows = []
    for row in proposals:
        displacement = json.loads(row["delta_phi"])
        displacement_rows.append(
            {
                "proposal_id": row.get("proposal_id", f"proposal-{row['sequence_sha256'][:20]}"),
                "parent_candidate_id": row["parent_candidate_id"],
                "sequence_sha256": row["sequence_sha256"],
                "motif": row["to_residues"],
                "edit_length": row["edit_length"],
                "edit_fraction": row["edit_fraction"],
                "target_cell": row["actual_cell_preflight"],
                "delta_net_charge_over_length": displacement["parent_to_child"][0],
                "delta_hydrophobic_ratio": displacement["parent_to_child"][1],
                "delta_hydrophobic_moment": displacement["parent_to_child"][2],
                "delta_length": displacement["parent_to_child"][3],
                "variant": args.variant,
            }
        )
    write_csv(displacement_path, displacement_rows)
    funnel_path = output / "failure_funnel.json"
    funnel_payload = {
        "schema_version": "ampgent.pbp2a-pepmlm-pepflow-hybrid-local-funnel.1",
        "variant": args.variant,
        "parent_count": len(parents),
        "donor_count": len(donors),
        "motif_count": len(motifs),
        "local_artifact_csv_count": csv_count,
        "local_history_sequence_hash_count": len(history_hashes),
        "local_prior_edit_count": len(prior_edits),
        "local_prior_motif_count": len(prior_motifs),
        "negative_fallback_parent_exclusion_count": len(negative_hashes),
        "fixed_archive_cell_count": len(archive_cells(args.archive)[0]) + len(
            json.loads(args.archive.read_text(encoding="utf-8"))["elites"]
        ),
        "counts": funnel,
    }
    funnel_path.write_text(
        json.dumps(funnel_payload, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    receipt = {
        "schema_version": "ampgent.pbp2a-pepflow-hybrid-local-generation.1",
        "operator_id": OPERATOR_ID,
        "operator_variant": args.variant,
        "target_key": TARGET_KEY,
        "generation": args.generation,
        "seed": args.seed,
        "source_arm": "PepMLM-target-conditioned-ancestry_x_PepFlow-donor-motif",
        "parent_source": PARENT_SOURCE,
        "donor_source": DONOR_SOURCE,
        "parent_run_id": parents[0]["run_id"],
        "parent_candidate_ids": [row["candidate_id"] for row in parents],
        "parent_count": len(parents),
        "donor_residues": [row["donor_residue"] for row in donors],
        "motifs": [row["motif"] for row in motifs],
        "edit_budget": "non_overlapping_2_or_3_residue_equal_length_motif_graft",
        "edit_fraction_limit": 0.25,
        "forbidden_positions_zero_based": sorted(FORBIDDEN_POSITIONS),
        "proposal_count": len(proposals),
        "proposal_cells": sorted({row["actual_cell_preflight"] for row in proposals}),
        "fixed_archive_cell_count": funnel_payload["fixed_archive_cell_count"],
        "historical_sequence_exclusion": "local_reports_exact_sequence_hash",
        "prior_edit_exclusion": "local_reports_parent_position_to",
        "motif_history_exclusion": "local_reports_parent_motif",
        "negative_fallback_parent_exclusion_count": len(negative_hashes),
        "historical_pg_gate": "pending",
        "materialization_status": "proposed_not_materialized",
        "candidate_identity_status": "proposal_only",
        "postgresql_reads": 0,
        "postgresql_writes": 0,
        "structure_status": "not_created",
        "gpu_rosetta_md_submitted": False,
        "historical_run_modified": False,
        "proposal_csv_sha256": sha256_file(proposal_path),
        "failure_funnel": "failure_funnel.json",
        "property_displacement": "property_displacement.csv",
    }
    (output / "generation_receipt.json").write_text(
        json.dumps(receipt, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parents-csv", type=Path, required=True)
    parser.add_argument("--pepflow-csv", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--negative-fallback-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--variant",
        choices=("primary_motif", "activity_preserving_fallback"),
        default="primary_motif",
    )
    parser.add_argument("--generation", type=int, default=106)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--limit", type=int, default=12)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
