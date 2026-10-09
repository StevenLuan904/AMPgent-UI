import json
import sys
from copy import deepcopy
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


def test_validate_plan_accepts_two_unique_candidates_with_four_raw_occurrences():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan = deepcopy(plan)
    plan["candidates"] = plan["candidates"][:2]
    ids = {x["authoritative_candidate_id"] for x in plan["candidates"]}
    plan["evaluations"] = [x for x in plan["evaluations"] if x["authoritative_candidate_id"] in ids]
    occurrences = []
    for rank, source in enumerate(plan["candidate_occurrences"], start=1):
        occurrence = deepcopy(source)
        candidate = plan["candidates"][(rank - 1) % 2]
        occurrence["candidate_id"] = candidate["id"]
        occurrence["parent_candidate_id"] = candidate["parent_id"]
        occurrence["sequence"] = candidate["sequence"]
        occurrence["sequence_sha256"] = candidate["sequence_sha256"]
        occurrence["occurrence_rank"] = rank
        occurrences.append(occurrence)
    plan["candidate_occurrences"] = occurrences
    checked = validate_plan(plan)
    assert len(checked["candidates"]) == 2
    assert len(checked["evaluations"]) == 32
    assert len(checked["candidate_occurrences"]) == 4


def test_validate_plan_rejects_round_request_mismatch():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    plan = deepcopy(plan)
    plan["score_import_tool_call"]["input_json"]["round"] = "r133"
    with pytest.raises(RuntimeError, match="round"):
        validate_plan(plan)


def test_validate_plan_accepts_explicit_formal_and_amplify_bindings():
    plan = deepcopy(json.loads(PLAN.read_text(encoding="utf-8")))
    formal = "74b91dd5-2028-5d00-b1a8-3ce84b6bc14c"
    amplify = "08d7aa76-090b-5dde-978e-dd21ad733e78"
    ids = [x["id"] for x in plan["candidates"]]
    plan["scorer_call_bindings"] = [
        {"tool_call_id": formal, "batch_kind": "formal12", "candidate_ids": ids},
        {"tool_call_id": amplify, "batch_kind": "amplify", "candidate_ids": ids},
    ]
    for index, evaluation in enumerate(plan["evaluations"]):
        evaluation["scorer_tool_call_id"] = amplify if index % 16 == 12 else formal
    assert validate_plan(plan)["evaluations"]


def test_validate_plan_rejects_foreign_scorer_binding():
    plan = deepcopy(json.loads(PLAN.read_text(encoding="utf-8")))
    ids = [x["id"] for x in plan["candidates"]]
    plan["scorer_call_bindings"] = [
        {
            "tool_call_id": "74b91dd5-2028-5d00-b1a8-3ce84b6bc14c",
            "batch_kind": "formal12",
            "candidate_ids": ids,
        },
        {
            "tool_call_id": "08d7aa76-090b-5dde-978e-dd21ad733e78",
            "batch_kind": "amplify",
            "candidate_ids": ids,
        },
    ]
    plan["evaluations"][0]["scorer_tool_call_id"] = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(RuntimeError, match="binding"):
        validate_plan(plan)
