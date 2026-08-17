from __future__ import annotations

import pytest

from pepagent.v38_science_execution import build_default_v38_sequence_contract
from pepagent.workers.v37_temporal_worker import V37_ROLE_CONFIG
from pepagent.workflows.v38_sequence_first import (
    V38SequenceFirstAgentWorkflow,
    _validate_request,
)


def _request() -> dict:
    return {
        "submission_preflight": {"status": "ready_to_submit_unique_run"},
        "knowledge_context_pack_sha256": "a" * 64,
        "refinement_provider": {
            "activity_name": "refine_v38_sequences_with_knowledge",
            "task_queue": "pepagent-refinement-provider-v38",
            "provider_task_id": "019fad3e-76b8-7e32-8455-d2e9b31d33e5",
            "release_revision": "provider-release-v1",
            "runtime_manifest_sha256": "b" * 64,
        },
        "execution_contract": build_default_v38_sequence_contract().model_dump(
            mode="json"
        ),
    }


def test_v38_sequence_workflow_accepts_only_full_score_all_contract() -> None:
    request = _request()
    _validate_request(request)

    request["execution_contract"]["cells"].pop()
    with pytest.raises(ValueError, match="nine generator cells"):
        _validate_request(request)


def test_v38_sequence_workflow_rejects_first_k_and_missing_metric_plugin() -> None:
    first_k = _request()
    first_k["execution_contract"]["first_k_retention_forbidden"] = False
    with pytest.raises(ValueError, match="first-K"):
        _validate_request(first_k)

    missing_metric = _request()
    missing_metric["execution_contract"]["metric_plugins"].pop()
    with pytest.raises(ValueError, match="five sequence metric plugins"):
        _validate_request(missing_metric)


def test_v38_sequence_workflow_requires_frozen_refinement_provider() -> None:
    request = _request()
    request.pop("refinement_provider")
    with pytest.raises(ValueError, match="frozen refinement provider"):
        _validate_request(request)

    invalid = _request()
    invalid["refinement_provider"]["runtime_manifest_sha256"] = "not-a-sha"
    with pytest.raises(ValueError, match="runtime identity"):
        _validate_request(invalid)


def test_v38_control_worker_registers_sequence_workflow_and_admission_activities() -> None:
    _, activities, workflows = V37_ROLE_CONFIG["v37-control"]
    assert V38SequenceFirstAgentWorkflow in workflows
    registered = {activity.__temporal_activity_definition.name for activity in activities}
    assert {
        "persist_v38_score_all_generation",
        "persist_v38_refinement_children",
        "persist_v38_sequence_metric",
        "evaluate_v38_sequence_admission",
        "persist_v38_sequence_admission",
    } <= registered
