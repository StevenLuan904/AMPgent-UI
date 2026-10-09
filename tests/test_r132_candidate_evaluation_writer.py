import json
import sys
from pathlib import Path

import pytest

# The production writer imports the repository package from ``src``.  Keep
# this test self-contained when pytest is invoked without an editable install.
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from analysis.r132_execute_candidate_evaluation_once import norm, validate_plan

PLAN = Path("reports/acea_vegfa_lineage2_round132_20261009/r132_candidate_evaluation_plan.json")


def test_final_plan_has_atomic_counts_and_distinct_generators():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    checked = validate_plan(plan)
    assert len(checked["candidates"]) == 4
    assert len(checked["evaluations"]) == 64
    assert len(checked["candidate_occurrences"]) == 4
    assert len(set(checked["transaction_guards"]["generator_calls"])) == 2


def test_validate_plan_rejects_partial_or_count_drift():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    with pytest.raises(RuntimeError, match="status"):
        validate_plan({**plan, "plan_status": "partial_pending_scores"})
    with pytest.raises(RuntimeError, match="count"):
        validate_plan({**plan, "evaluations": plan["evaluations"][:-1]})


def test_norm_handles_jsonb_string_and_object():
    payload = {"target": "acea", "rank": 1}
    assert norm(payload) == payload
    assert norm(json.dumps(payload)) == payload
