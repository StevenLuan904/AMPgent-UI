import json
from pathlib import Path

import pytest

from analysis.r133_candidate_evaluation_config import (
    GENERATOR_CALLS,
    ROUND,
    build_r133_occurrences,
    load_approved_requests,
)

REQUEST_DIR = Path("reports/acea_vegfa_lineage2_round133_20261009")


def test_r133_config_loads_four_actions_and_attempt2_generators():
    requests = load_approved_requests(REQUEST_DIR)
    assert set(requests) == {"acea", "vegfa"}
    assert sum(len(value["actions"]) for value in requests.values()) == 4
    assert GENERATOR_CALLS == {
        "acea": "d10a433e-e3e8-531f-9442-a232aaad8578",
        "vegfa": "168cb388-dc3f-5c2c-abf5-77f11bc9eec7",
    }


def test_r133_config_rejects_round_mismatch(tmp_path):
    for target in ("acea", "vegfa"):
        request = json.loads(
            (REQUEST_DIR / f"r133_{target}_request.json").read_text(encoding="utf-8")
        )
        request["proposal_round"] = ROUND + 1
        (tmp_path / f"r133_{target}_request.json").write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(ValueError, match="request identity"):
        load_approved_requests(tmp_path)


def test_r133_occurrence_builder_keeps_four_raw_actions_for_two_unique_sequences():
    requests = load_approved_requests(REQUEST_DIR)
    generated = {}
    canonical = {"ACDEFG": "candidate-a", "HIKLMN": "candidate-b"}
    for target, value in requests.items():
        generated[target] = [
            {
                "action_id": action["action_id"],
                "sequence": sequence,
                "conditional_nll": 1.0,
                "conditional_ppl": 2.0,
            }
            for action, sequence in zip(value["actions"], canonical, strict=True)
        ]
    occurrences = build_r133_occurrences(REQUEST_DIR, generated, canonical)
    assert len(occurrences) == 4
    assert {row["tool_call_id"] for row in occurrences} == set(GENERATOR_CALLS.values())
    assert {row["candidate_id"] for row in occurrences} == set(canonical.values())
    assert sorted(row["occurrence_rank"] for row in occurrences) == [1, 1, 2, 2]
