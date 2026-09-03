import copy
import json
from pathlib import Path

import pytest

from analysis.validate_candidate_source_evaluation import validate

REPORT = (
    Path(__file__).parents[1]
    / "reports/ampgent_goal_completion_20260903/source_evaluation_summary.json"
)


def payload():
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_current_source_evaluation_is_count_consistent_and_uses_current_gate():
    result = validate(payload())
    assert result["source_count"] == 3
    assert result["pepglad_strict_unique_candidate_count"] == 87989
    assert result["pepmlm_formal_12_complete_count"] == 24576
    assert result["pepflow_formal_12_complete_count"] == 8
    assert result["pepflow_pool_a_addition_count"] == 0
    assert result["display_hard_gate"]["guruprasad_instability"] == "<=50"


def test_rejects_obsolete_instability_gate():
    value = copy.deepcopy(payload())
    value["sources"]["pepglad"]["strict_display_contract"][
        "guruprasad_instability"
    ] = "<50"
    with pytest.raises(ValueError, match="display hard gate drifted"):
        validate(value)


def test_rejects_source_stage_count_inversion():
    value = copy.deepcopy(payload())
    value["sources"]["pepflow"]["pool_a_addition_count"] = 1
    with pytest.raises(ValueError, match="PepFlow downstream counts"):
        validate(value)
