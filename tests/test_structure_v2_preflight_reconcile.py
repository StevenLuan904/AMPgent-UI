from __future__ import annotations

import copy

import pytest

from pepagent.structure_v2_preflight_reconcile import (
    PREFLIGHT_ACTIVITY,
    extract_preflight_failure_boundary,
)


def _history() -> list[dict[str, object]]:
    return [
        {
            "event_id": "1",
            "event_time": "2026-08-29T00:52:00Z",
            "event_type": "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED",
        },
        {
            "event_id": "5",
            "event_time": "2026-08-29T00:52:01Z",
            "event_type": "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED",
            "activity_task_scheduled_event_attributes": {
                "activity_id": "1",
                "activity_type": {"name": PREFLIGHT_ACTIVITY},
                "task_queue": {"name": "pepagent-structure-v2-persist"},
            },
        },
        {
            "event_id": "6",
            "event_time": "2026-08-29T00:55:01Z",
            "event_type": "EVENT_TYPE_ACTIVITY_TASK_STARTED",
            "activity_task_started_event_attributes": {
                "scheduled_event_id": "5",
                "attempt": 5,
                "identity": "pepagent:structure_v2_persist:5796@host:cb6df73",
                "last_failure": {
                    "message": (
                        "structure v2 request differs from its current PG eligibility binding"
                    )
                },
            },
        },
        {
            "event_id": "7",
            "event_time": "2026-08-29T00:55:02Z",
            "event_type": "EVENT_TYPE_ACTIVITY_TASK_FAILED",
            "activity_task_failed_event_attributes": {
                "scheduled_event_id": "5",
                "started_event_id": "6",
                "retry_state": "RETRY_STATE_MAXIMUM_ATTEMPTS_REACHED",
                "failure": {
                    "message": (
                        "structure v2 request differs from its current PG eligibility binding"
                    ),
                    "application_failure_info": {"type": "ValueError"},
                },
            },
        },
        {
            "event_id": "8",
            "event_time": "2026-08-29T00:55:03Z",
            "event_type": "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED",
            "activity_task_scheduled_event_attributes": {
                "activity_id": "2",
                "activity_type": {"name": "mark_run_failed"},
                "task_queue": {"name": "pepagent-control"},
            },
        },
        {
            "event_id": "20",
            "event_time": "2026-08-29T00:55:05Z",
            "event_type": "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
            "workflow_execution_failed_event_attributes": {
                "failure": {"message": "Activity task failed"}
            },
        },
    ]


def test_extracts_retained_final_preflight_attempt_without_fabricating_retries() -> None:
    boundary = extract_preflight_failure_boundary(_history())

    assert boundary.activity_id == "1"
    assert boundary.final_attempt == 5
    assert boundary.worker_identity.startswith("pepagent:structure_v2_persist:5796")
    assert boundary.error_type == "ValueError"
    assert boundary.retry_state == "RETRY_STATE_MAXIMUM_ATTEMPTS_REACHED"


def test_reconciler_rejects_any_scientific_activity_in_failed_history() -> None:
    history = _history()
    scientific = copy.deepcopy(history[1])
    scientific["event_id"] = "9"
    scientific["activity_task_scheduled_event_attributes"]["activity_id"] = "3"
    scientific["activity_task_scheduled_event_attributes"]["activity_type"] = {
        "name": "predict_structure"
    }
    history.insert(-1, scientific)

    with pytest.raises(ValueError, match="contains scientific activities"):
        extract_preflight_failure_boundary(history)


def test_reconciler_rejects_changed_failure_reason() -> None:
    history = _history()
    history[3]["activity_task_failed_event_attributes"]["failure"]["message"] = (
        "different failure"
    )

    with pytest.raises(ValueError, match="failure message differs"):
        extract_preflight_failure_boundary(history)
