"""Build a bounded GyrA PepFlow one-residue QD-neighbor batch.

This operator keeps authoritative GyrA parents fixed and uses residue donors
from an existing PepFlow artifact.  It is a proposal stage only: PG exact
history is consulted, but no candidate/evaluation is materialized here.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from autoresearch_safety_rescue_variants import _historical_sequence_sha256s
from qd_gap_directed_reciprocal_micrograft_v3 import _candidate_cell, phi

from pepagent.autoresearch_quality_diversity import BehaviorSpacePolicy


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _edits(paths: list[Path]) -> set[tuple[str, int, str]]:
    seen: set[tuple[str, int, str]] = set()
    for path in paths:
        for row in _rows(path):
            parent = row.get("parent_sequence", "").strip().upper()
            child = row.get("sequence", "").strip().upper()
            if not parent or len(parent) != len(child):
                continue
            changed = [
                index
                for index, (old, new) in enumerate(zip(parent, child, strict=True))
                if old != new
            ]
            if len(changed) == 1:
                index = changed[0]
                seen.add((parent, index, child[index]))
    return seen


def _safe_parents(rows: list[dict[str, str]], target: str) -> list[dict[str, str]]:
    result = []
    for row in rows:
        if (
            row.get("target_key", row.get("branch_key", "")).casefold() == target.casefold()
            and row.get("candidate_id", "").count("-") == 4
            and row.get("display_eligible", "").casefold() == "true"
            and row.get("formal_12_complete", "").casefold() == "true"
            and int(row.get("activity_model_support_count_calibrated", "0")) >= 2
            and row.get("excellent_sequence_stage_calibrated", "").casefold() == "true"
        ):
            result.append(row)
    return sorted(result, key=lambda row: (row["candidate_id"], row["sequence"]))


def generate(
    parents: list[dict[str, str]],
    donors: list[dict[str, str]],
    archive: dict[str, Any],
    history: set[str],
    prior_edits: set[tuple[str, int, str]],
    *,
    target: str,
    generation: int,
    seed: int,
    limit: int,
    operator_id: str = "gyrA-pepflow-qd-neighbor-1aa-v1",
    proposal_mode: str = "pepflow_source_qd_empty_cell_neighbor",
    min_fragment_length: int = 1,
    max_fragment_length: int = 1,
    min_edit_position_zero_based: int = 0,
    registered_empty_cells: set[str] | None = None,
) -> list[dict[str, Any]]:
    if limit != 12:
        raise ValueError("this frozen batch requires exactly 12 proposals")
    parent_occupied_cells = {
        row.get("qd_cell", "").strip()
        for row in parents
        if row.get("qd_cell", "").strip()
    }
    empty = set(archive["empty_cell_ids"]) - parent_occupied_cells
    registered = set(registered_empty_cells or ())
    overlap = sorted(registered & parent_occupied_cells)
    if overlap:
        raise ValueError(
            "registered empty cells overlap parent occupied cells: "
            + ",".join(overlap)
        )
    policy = BehaviorSpacePolicy.model_validate(archive["policy"])
    donor_rows = []
    for row in sorted(
        donors,
        key=lambda item: (
            item.get("donor_candidate_id", ""),
            item.get("donor_fragment", ""),
            item.get("sequence", ""),
        ),
    ):
        if row.get("donor_source", "").casefold() != "pepflow":
            continue
        fragment = (row.get("donor_fragment") or "").strip().upper()
        valid_length = min_fragment_length <= len(fragment) <= max_fragment_length
        if not valid_length or not fragment.isalpha():
            continue
        donor_rows.append(row)
    if not donor_rows:
        raise ValueError("no one-residue PepFlow donor asset available")

    proposals: list[dict[str, Any]] = []
    seen = set(history)
    seen_cells: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for parent_index, parent in enumerate(parents):
        sequence = parent["sequence"].strip().upper()
        for donor_index, donor in enumerate(donor_rows):
            residue = donor["donor_fragment"].strip().upper()
            for position in range(
                min_edit_position_zero_based, len(sequence) - len(residue) + 1
            ):
                if residue == sequence[position : position + len(residue)]:
                    continue
                edit = (sequence, position, residue)
                if edit in prior_edits:
                    continue
                child = sequence[:position] + residue + sequence[position + len(residue) :]
                digest = _sha(child)
                if digest in seen:
                    continue
                cell = _candidate_cell(child, policy)
                if cell not in empty:
                    continue
                if registered_empty_cells and cell not in registered_empty_cells:
                    continue
                before, after = phi(sequence), phi(child)
                candidates.append(
                    {
                        "sequence": child,
                        "sequence_sha256": digest,
                        "branch_key": target.casefold(),
                        "target_key": target,
                        "generation": generation,
                        "seed": seed,
                        "operator_id": operator_id,
                        "proposal_mode": proposal_mode,
                        "parent_source": "PepGLAD",
                        "source_run_id": parent["run_id"],
                        "parent_run_id": parent["run_id"],
                        "parent_candidate_id": parent["candidate_id"],
                        "parent_sequence": sequence,
                        "parent_sequence_sha256": parent["sequence_sha256"],
                        "edit_position_zero_based": position,
                        "edit_position_1based": position + 1,
                        "from_residue": sequence[position : position + len(residue)],
                        "to_residue": residue,
                        "donor_candidate_id": donor.get("donor_candidate_id", ""),
                        "donor_source": "PepFlow",
                        "donor_sequence": donor.get("donor_sequence", ""),
                        "donor_fragment": residue,
                        "donor_artifact_row": donor.get("proposal_id", ""),
                        "donor_artifact_id": donor.get("proposal_id", ""),
                        "donor_artifact": donor.get("donor_artifact", ""),
                        "donor_row_number": donor.get("donor_row_number", ""),
                        "donor_row_sha256": donor.get("donor_row_sha256", ""),
                        "actual_cell_preflight": cell,
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
                                    for after_value, before_value in zip(
                                        after, before, strict=True
                                    )
                                ],
                            },
                            sort_keys=True,
                        ),
                        "history_gate": "postgresql_exact_sequence_and_prior_edit_passed",
                        "parent_index": parent_index,
                        "donor_index": donor_index,
                    }
                )
    candidates.sort(
        key=lambda row: (
            row["actual_cell_preflight"] in seen_cells,
            row["actual_cell_preflight"],
            row["parent_candidate_id"],
            row["edit_position_zero_based"],
            row["to_residue"],
            row["sequence"],
        )
    )
    for row in candidates:
        cell = row["actual_cell_preflight"]
        if cell in seen_cells and len(seen_cells) < 12:
            continue
        proposals.append(row)
        seen.add(row["sequence_sha256"])
        seen_cells.add(cell)
        if len(proposals) == limit:
            break
    if len(proposals) < limit:
        for row in candidates:
            if row["sequence_sha256"] in {item["sequence_sha256"] for item in proposals}:
                continue
            proposals.append(row)
            if len(proposals) == limit:
                break
    return proposals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parents-csv", type=Path, required=True)
    parser.add_argument("--donors-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--prior-csv", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target", default="GyrA")
    parser.add_argument("--generation", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--operator-id", default="gyrA-pepflow-qd-neighbor-1aa-v1")
    parser.add_argument("--proposal-mode", default="pepflow_source_qd_empty_cell_neighbor")
    parser.add_argument("--min-donor-fragment-length", type=int, default=1)
    parser.add_argument("--max-donor-fragment-length", type=int, default=1)
    parser.add_argument("--min-edit-position-zero-based", type=int, default=0)
    parser.add_argument("--registered-empty-cell", action="append", default=[])
    args = parser.parse_args()
    parents = _safe_parents(_rows(args.parents_csv), args.target)
    if not parents:
        raise ValueError("no authoritative display/activity-supported QD parents")
    archive = json.loads(args.archive_json.read_text(encoding="utf-8"))
    history = asyncio.run(_historical_sequence_sha256s())
    prior_paths = [*args.prior_csv, args.parents_csv]
    prior_edit_set = _edits(prior_paths)
    proposals = generate(
        parents,
        _rows(args.donors_csv),
        archive,
        history,
        prior_edit_set,
        target=args.target,
        generation=args.generation,
        seed=args.seed,
        limit=args.limit,
        operator_id=args.operator_id,
        proposal_mode=args.proposal_mode,
        min_fragment_length=args.min_donor_fragment_length,
        max_fragment_length=args.max_donor_fragment_length,
        min_edit_position_zero_based=args.min_edit_position_zero_based,
        registered_empty_cells=set(args.registered_empty_cell),
    )
    if len(proposals) != args.limit:
        raise ValueError(f"only {len(proposals)} PG-new empty-cell proposals available")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / "proposals.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(proposals[0]))
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.gyra-pepflow-qd-neighbor-generation.1",
        "operator_id": args.operator_id,
        "target_key": args.target,
        "generation": args.generation,
        "seed": args.seed,
        "proposal_count": len(proposals),
        "parent_count": len(parents),
        "pepflow_donor_count": len({row["donor_candidate_id"] for row in proposals}),
        "preflight_empty_cell_count": len({row["actual_cell_preflight"] for row in proposals}),
        "postgresql_history_sequence_count": len(history),
        "prior_edit_count": len(prior_edit_set),
        "archive_sha256": _sha(args.archive_json.read_text(encoding="utf-8")),
        "proposal_csv_sha256": _sha(path.read_text(encoding="utf-8-sig")),
        "gpu_rosetta_md_submitted": False,
        "materialization_pending": True,
        "proposal_mode": args.proposal_mode,
        "parent_source": "PepGLAD",
        "donor_source": "PepFlow",
        "source_run_ids": sorted({row["parent_run_id"] for row in proposals}),
        "donor_artifact_ids": sorted({row["donor_artifact_id"] for row in proposals}),
        "donor_fragment_length": [
            args.min_donor_fragment_length,
            args.max_donor_fragment_length,
        ],
        "min_edit_position_zero_based": args.min_edit_position_zero_based,
        "registered_empty_cells": sorted(set(args.registered_empty_cell)),
        "parent_occupied_cells": sorted(
            {
                row.get("qd_cell", "").strip()
                for row in parents
                if row.get("qd_cell", "").strip()
            }
        ),
    }
    (args.output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
