from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.diagnose_gyra_pepflow_matched_control import diagnose


def _row(position: int, from_residue: str, to_residue: str) -> dict[str, str]:
    return {
        "parent_run_id": "e666339b-63ee-5efc-8733-a34b9c9f8694",
        "parent_candidate_id": "48e42024-f5e9-4eee-a720-2f577738fe32",
        "parent_sequence_sha256": "parent-sha",
        "parent_sequence": "ESREEWWARSGAATLTAKAAAAR",
        "acceptor_start_zero_based": str(position),
        "from_residue": from_residue,
        "to_residue": to_residue,
    }


def test_matched_control_rejects_collapsed_donor_alphabet() -> None:
    slots = [
        _row(2, "R", residue)
        for residue in ("I", "L", "T", "A", "E", "K")
    ] + [_row(3, "E", residue) for residue in ("I", "L", "T", "S", "A", "K")]
    donors = [
        {"donor_source": "PepFlow", "donor_fragment": residue}
        for residue in ("G", "P", "S", "V")
    ]
    result = diagnose(slots, donors)
    assert result["source_parent"]["identity_verified"] is True
    assert result["design_contract"]["slot_count"] == 12
    assert result["design_contract"]["seed"] == 20260904
    assert result["design_contract"]["single_residue_budget"] is True
    assert result["preflight"][
        "projected_unique_children_after_reference_edit_exclusion"
    ] == 7
    assert result["decision"]["matched_control_eligible"] is False
    assert result["decision"]["do_not_score_or_materialize"] is True
