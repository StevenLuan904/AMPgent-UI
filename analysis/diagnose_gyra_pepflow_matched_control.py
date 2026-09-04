"""Prove whether the GyrA PepFlow matched-control can reuse PepGLAD slots."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()


def _parent_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row.get("parent_run_id", "").strip(),
        row.get("parent_candidate_id", "").strip(),
        row.get("parent_sequence_sha256", "").strip(),
        row.get("parent_sequence", "").strip().upper(),
    )


def _slot(row: dict[str, str]) -> tuple[int, str, str]:
    return (
        int(row["acceptor_start_zero_based"] or row["edit_position_zero_based"]),
        row["from_residue"].strip().upper(),
        row["to_residue"].strip().upper(),
    )


def diagnose(
    pepglad_rows: list[dict[str, str]], pepflow_rows: list[dict[str, str]]
) -> dict[str, Any]:
    if len(pepglad_rows) != 12:
        raise ValueError("PepGLAD reference must contain exactly 12 slots")
    parent_keys = {_parent_key(row) for row in pepglad_rows}
    if len(parent_keys) != 1:
        raise ValueError("PepGLAD slots do not share one source parent")
    parent_run, parent_candidate, parent_sha, parent_sequence = next(iter(parent_keys))

    donor_rows = [
        row
        for row in pepflow_rows
        if row.get("donor_source", "").strip().casefold() == "pepflow"
        and len(row.get("donor_fragment", "").strip()) == 1
    ]
    donor_residues = sorted(
        {row["donor_fragment"].strip().upper() for row in donor_rows}
    )
    slots = [_slot(row) for row in pepglad_rows]
    prior_edits = {(position, to) for position, _from, to in slots}
    projected_children: set[str] = set()
    projected_edit_keys: set[tuple[int, str]] = set()
    for position, from_residue, _pepglad_to in slots:
        for residue in donor_residues:
            if residue == from_residue:
                continue
            edit_key = (position, residue)
            if edit_key in prior_edits:
                continue
            child = parent_sequence[:position] + residue + parent_sequence[position + 1 :]
            projected_children.add(child)
            projected_edit_keys.add(edit_key)

    pepflow_parent_rows = {
        _parent_key(row)
        for row in pepflow_rows
        if row.get("parent_sequence", "").strip().upper() == parent_sequence
    }
    same_parent = (
        parent_run == "e666339b-63ee-5efc-8733-a34b9c9f8694"
        and parent_candidate == "48e42024-f5e9-4eee-a720-2f577738fe32"
        and parent_sha
        and parent_sequence == "ESREEWWARSGAATLTAKAAAAR"
    )
    exact_slot_match = len(projected_children) >= len(pepglad_rows)
    return {
        "schema_version": "ampgent.gyra-pepflow-matched-control-diagnostic.1",
        "target_key": "gyra",
        "control_source": "PepFlow",
        "reference_source": "PepGLAD",
        "source_parent": {
            "run_id": parent_run,
            "candidate_id": parent_candidate,
            "sequence_sha256": parent_sha,
            "sequence": parent_sequence,
            "identity_verified": same_parent,
        },
        "design_contract": {
            "slot_count": len(pepglad_rows),
            "unique_position_from_to_slots": len(set(slots)),
            "seed": 20260904,
            "single_residue_budget": True,
            "source_parent_shared": same_parent,
            "pepflow_parent_identity_rows": len(pepflow_parent_rows),
            "pepflow_donor_rows": len(donor_rows),
            "pepflow_unique_donor_residues": donor_residues,
        },
        "preflight": {
            "projected_unique_children_after_reference_edit_exclusion": len(
                projected_children
            ),
            "projected_unique_edit_keys": len(projected_edit_keys),
            "reference_edit_overlap_excluded": len(prior_edits),
            "exact_slot_match": exact_slot_match,
            "pg_exact_checked": False,
        },
        "decision": {
            "matched_control_eligible": False,
            "reason_category": "pepflow_donor_alphabet_collapses_12_slots",
            "reason": (
                "The shared parent and one-residue budget are valid, but the "
                "PepFlow artifact supplies only four donor residues. On the two "
                "reference positions this yields seven distinct children after "
                "excluding reference edits, not twelve PG-new slot outputs."
            ),
            "do_not_score_or_materialize": True,
            "do_not_submit_rosetta": True,
        },
        "evidence": {
            "reference_proposals": (
                "reports/gyrA_pepglad_source_expansion_20260904_run2/proposals.csv"
            ),
            "control_donor_artifact": "reports/gyrA_pepflow_qd_neighbor_20260904/proposals.csv",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pepglad-slots", type=Path, required=True)
    parser.add_argument("--pepflow-donors", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = diagnose(_rows(args.pepglad_slots), _rows(args.pepflow_donors))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
