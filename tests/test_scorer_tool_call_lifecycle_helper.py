# ruff: noqa: E501

import copy

import pytest

from analysis.scorer_tool_call_lifecycle_helper import (
    build_batch_registration,
    deterministic_batch_tool_call_id,
    validate_terminal_receipt,
)

ROOT = "f72805f4-7547-5017-a069-74042708d228"
RUN = "61d65749-dab0-55db-b62a-6834e4fe604d"
SHA = "a" * 64


def rows():
    return [
        {"candidate_id": "c-1", "sequence": "KWKWWIEGAVEKVKSTREWKE", "target": "acea"},
        {"candidate_id": "c-2", "sequence": "RHFKGDRTYWVLEAWRVRHKK", "target": "vegfa"},
    ]


def registration(kind="formal12"):
    return build_batch_registration(
        root_id=ROOT,
        run_id=RUN,
        campaign_id="campaign",
        round_name="r136",
        batch_kind=kind,
        input_path="/tmp/unique.csv",
        source_path="/tmp/receipt.json",
        candidate_rows=rows(),
        model_release_key="score-release-v1" if kind == "formal12" else "amplify-2.0.1-py36hdfd78af_2",
        source_artifact_sha256=SHA,
    )


def test_builds_two_distinct_batch_calls_with_ordered_ids():
    formal = registration()
    amp = registration("amplify")
    assert formal["id"] != amp["id"]
    assert formal["tool_name"] == "ampgent.score_all_r116_runner_local"
    assert amp["tool_name"] == "ampgent.amplify"
    assert formal["input_json"]["candidate_ids"] == ["c-1", "c-2"]
    assert formal["candidate_count"] == 2
    assert deterministic_batch_tool_call_id(ROOT, RUN, "formal12", "r136") == formal["id"]


def test_round_and_attempt_are_part_of_identity():
    first = deterministic_batch_tool_call_id(ROOT, RUN, "formal12", "r136", 1)
    second_round = deterministic_batch_tool_call_id(ROOT, RUN, "formal12", "r137", 1)
    second_attempt = deterministic_batch_tool_call_id(ROOT, RUN, "formal12", "r136", 2)
    assert len({first, second_round, second_attempt}) == 3
    assert first == deterministic_batch_tool_call_id(ROOT, RUN, "formal12", "r136", 1)


def test_rejects_duplicate_candidate_ids():
    values = rows()
    values[1]["candidate_id"] = values[0]["candidate_id"]
    with pytest.raises(ValueError, match="IDs"):
        build_batch_registration(
            root_id=ROOT, run_id=RUN, campaign_id="campaign", round_name="r136", batch_kind="formal12",
            input_path="in.csv", source_path="receipt.json", candidate_rows=values,
            model_release_key="release", source_artifact_sha256=SHA,
        )


def test_rejects_duplicate_sequences_and_noncanonical_symbols():
    values = rows()
    values[1]["sequence"] = values[0]["sequence"]
    with pytest.raises(ValueError, match="unique sequences"):
        build_batch_registration(
            root_id=ROOT, run_id=RUN, campaign_id="campaign", round_name="r136", batch_kind="formal12",
            input_path="in.csv", source_path="receipt.json", candidate_rows=values,
            model_release_key="release", source_artifact_sha256=SHA,
        )
    values = rows()
    values[0]["sequence"] = "KWKX"
    with pytest.raises(ValueError, match="non-canonical"):
        build_batch_registration(
            root_id=ROOT, run_id=RUN, campaign_id="campaign", round_name="r136", batch_kind="formal12",
            input_path="in.csv", source_path="receipt.json", candidate_rows=values,
            model_release_key="release", source_artifact_sha256=SHA,
        )


def test_completed_receipt_returns_terminal_update():
    reg = registration()
    receipt = {
        "tool_call_id": reg["id"], "batch_kind": "formal12", "status": "completed", "exit_code": 0,
        "started_at": "2026-10-09T03:00:00Z", "finished_at": "2026-10-09T03:01:00Z",
        "request_sha256": reg["input_sha256"], "output_sha256": "b" * 64,
        "candidate_ids": ["c-1", "c-2"],
    }
    update = validate_terminal_receipt(reg, receipt)
    assert update["status"] == "completed"
    assert update["output_sha256"] == "b" * 64
    assert update["error_json"] is None
    assert update["parameters_json_patch"]["actual_execution"]["exit_code"] == 0


def test_failed_receipt_is_terminal_but_nonzero():
    reg = registration("amplify")
    receipt = {
        "tool_call_id": reg["id"], "batch_kind": "amplify", "status": "failed", "exit_code": 17,
        "started_at": "2026-10-09T03:00:00Z", "finished_at": "2026-10-09T03:00:02Z",
        "request_sha256": reg["input_sha256"], "candidate_ids": ["c-1", "c-2"],
        "error_category": "runtime_unavailable", "terminal_artifact_path": "/tmp/stderr.log",
    }
    assert validate_terminal_receipt(reg, receipt)["status"] == "failed"


def test_terminal_timestamps_require_timezone_and_monotonic_order():
    reg = registration()
    receipt = {
        "tool_call_id": reg["id"], "batch_kind": "formal12", "status": "completed", "exit_code": 0,
        "started_at": "2026-10-09T03:01:00", "finished_at": "2026-10-09T03:00:00Z",
        "request_sha256": reg["input_sha256"], "output_sha256": "e" * 64,
        "candidate_ids": ["c-1", "c-2"],
    }
    with pytest.raises(ValueError, match="timezone"):
        validate_terminal_receipt(reg, receipt)
    receipt["started_at"] = "2026-10-09T03:01:00Z"
    with pytest.raises(ValueError, match="precede"):
        validate_terminal_receipt(reg, receipt)


@pytest.mark.parametrize("field", ["tool_call_id", "request_sha256", "candidate_ids"])
def test_rejects_receipt_identity_drift(field):
    reg = registration()
    receipt = {
        "tool_call_id": reg["id"], "batch_kind": "formal12", "status": "completed", "exit_code": 0,
        "started_at": "2026-10-09T03:00:00Z", "finished_at": "2026-10-09T03:01:00Z",
        "request_sha256": reg["input_sha256"], "output_sha256": "d" * 64,
        "candidate_ids": ["c-1", "c-2"],
    }
    drifted = copy.deepcopy(receipt)
    drifted[field] = "wrong" if field != "candidate_ids" else ["c-2", "c-1"]
    with pytest.raises(ValueError):
        validate_terminal_receipt(reg, drifted)
