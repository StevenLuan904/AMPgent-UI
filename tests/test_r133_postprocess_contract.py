import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "analysis"))

import pytest

from r133_postprocess_contract import (
    apply_dual_support_final_fields,
    assert_amp_parent_fields,
    summarize_gate_counts,
)


def calibration(action_id="a", domains=("acea", "vegfa")):
    return [{"action_id": action_id, "branch_key": d,
             "activity_model_support_count_calibrated": "2"} for d in domains]


def full_row(**extra):
    row = {"action_id": "a", "formal12": "true", "display_hard_gate": "true"}
    row.update({f"amplify_submodel_{i}_probability": "0.9" for i in range(1, 6)})
    row["parent_delta_phi"] = json.dumps({"charge_density": 0, "hydrophobicity": 0, "moment": 0, "length": 0})
    row["parent_delta_objectives"] = json.dumps({"hemo": 0, "instab": 0, "quality": 0, "toxin": 0})
    row.update(extra)
    return row


def test_stale_zero_support_is_overwritten():
    out = apply_dual_support_final_fields([full_row(activity_model_support_count="0")], calibration())
    assert out[0]["activity_model_support_count"] == "2"
    assert out[0]["excellent_sequence_stage"] == "true"
    assert summarize_gate_counts(out)["quality_eligible"] == 1
    assert_amp_parent_fields(out)


def test_missing_amp_fails_loudly():
    row = full_row()
    del row["amplify_submodel_5_probability"]
    with pytest.raises(AssertionError, match="AMP"):
        assert_amp_parent_fields([row])


def test_missing_dual_domain_fails_loudly():
    with pytest.raises(ValueError, match="missing dual calibration"):
        apply_dual_support_final_fields([full_row()], calibration(domains=("acea",)))


def test_formal_display_mismatch_is_not_quality_eligible():
    out = apply_dual_support_final_fields([full_row(formal12="false")], calibration())
    assert out[0]["display_hard_gate"] == "true"
    assert out[0]["excellent_sequence_stage"] == "false"
    assert summarize_gate_counts(out)["quality_eligible"] == 0
