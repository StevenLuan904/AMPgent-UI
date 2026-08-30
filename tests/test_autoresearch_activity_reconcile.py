from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import pepagent.autoresearch_activity_reconcile as reconcile
from pepagent.autoresearch_activity_reconcile import (
    TemporalActivityBoundary,
    extract_temporal_activity_boundaries,
    reconcile_temporal_activity_boundaries,
)


def test_persistence_queue_has_an_explicit_observer_role() -> None:
    assert reconcile._TASK_QUEUE_ROLES["pepagent-autoresearch-persistence-v1"] == (
        "autoresearch-persistence"
    )


def _scheduled() -> dict[str, object]:
    return {
        "event_id": "71",
        "event_time": "2026-08-28T20:56:57.176289084Z",
        "event_type": "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED",
        "activity_task_scheduled_event_attributes": {
            "activity_id": "10",
            "activity_type": {"name": "persist_v38_sequence_metric"},
            "task_queue": {"name": "pepagent-autoresearch-control-v1"},
        },
    }


def test_extracts_retry_failure_from_next_started_event() -> None:
    events = [
        _scheduled(),
        {
            "event_id": "72",
            "event_time": "2026-08-28T21:57:10.172300607Z",
            "event_type": "EVENT_TYPE_ACTIVITY_TASK_STARTED",
            "activity_task_started_event_attributes": {
                "scheduled_event_id": "71",
                "identity": "pepagent:autoresearch-control:48356@host:revision",
                "attempt": 2,
                "last_failure": {
                    "message": "activity StartToClose timeout",
                    "source": "Server",
                    "timeout_failure_info": {
                        "timeout_type": "TIMEOUT_TYPE_START_TO_CLOSE"
                    },
                },
            },
        },
        {
            "event_id": "73",
            "event_time": "2026-08-28T21:57:17.974801733Z",
            "event_type": "EVENT_TYPE_ACTIVITY_TASK_COMPLETED",
            "activity_task_completed_event_attributes": {
                "scheduled_event_id": "71",
                "started_event_id": "72",
                "identity": "pepagent:autoresearch-control:48356@host:revision",
            },
        },
    ]

    boundaries = extract_temporal_activity_boundaries(events)

    by_key = {item.semantic_key: item for item in boundaries}
    failed = by_key[("10", 1, "failed")]
    assert failed.error_type == "TemporalStartToCloseTimeout"
    assert failed.error_message == "activity StartToClose timeout"
    assert failed.timeout_type == "TIMEOUT_TYPE_START_TO_CLOSE"
    assert failed.worker_identity is None
    assert by_key[("10", 2, "started")].worker_identity.endswith("revision")
    assert by_key[("10", 2, "succeeded")].worker_identity.endswith("revision")


def test_extracts_both_retry_and_terminal_heartbeat_timeouts() -> None:
    events = [
        _scheduled(),
        {
            "event_id": "72",
            "event_time": "2026-08-28T20:57:10Z",
            "event_type": "EVENT_TYPE_ACTIVITY_TASK_STARTED",
            "activity_task_started_event_attributes": {
                "scheduled_event_id": "71",
                "identity": "metrics-worker",
                "attempt": 2,
                "last_failure": {
                    "message": "activity Heartbeat timeout",
                    "source": "Server",
                    "timeout_failure_info": {
                        "timeout_type": "TIMEOUT_TYPE_HEARTBEAT"
                    },
                },
            },
        },
        {
            "event_id": "73",
            "event_time": "2026-08-28T21:57:10Z",
            "event_type": "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT",
            "activity_task_timed_out_event_attributes": {
                "scheduled_event_id": "71",
                "started_event_id": "72",
                "retry_state": "RETRY_STATE_MAXIMUM_ATTEMPTS_REACHED",
                "failure": {
                    "message": "activity Heartbeat timeout",
                    "source": "Server",
                    "timeout_failure_info": {
                        "timeout_type": "TIMEOUT_TYPE_HEARTBEAT"
                    },
                },
            },
        },
    ]

    boundaries = extract_temporal_activity_boundaries(events)
    failed = [item for item in boundaries if item.status == "failed"]

    assert [item.attempt for item in failed] == [1, 2]
    assert {item.error_type for item in failed} == {"TemporalHeartbeatTimeout"}
    assert failed[1].retry_state == "RETRY_STATE_MAXIMUM_ATTEMPTS_REACHED"


class _Session:
    def __init__(self, run: object, events: list[object]) -> None:
        self.run = run
        self.events = events

    async def get(self, *args: object, **kwargs: object) -> object:
        del args, kwargs
        return self.run

    async def scalars(self, *args: object, **kwargs: object) -> list[object]:
        del args, kwargs
        return self.events


@pytest.mark.asyncio
async def test_reconciliation_derives_prior_worker_and_is_semantically_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = UUID("71c44dc3-4461-5963-a84a-ad266c8a668a")
    workflow_id = "workflow-acea"
    temporal_run_id = "b4245d43-7a39-479e-870a-17af8530fb49"
    prior_started = SimpleNamespace(
        payload_json={
            "activity_id": "10",
            "activity_type": "persist_v38_sequence_metric",
            "attempt": 1,
            "status": "started",
            "completed": 0,
            "expected": 4,
            "worker_identity": "stuck-worker",
        }
    )
    run = SimpleNamespace(
        id=run_id,
        temporal_workflow_id=workflow_id,
        temporal_run_id=temporal_run_id,
        spec_json={"branch_key": "acea"},
    )
    captured = []

    async def append(_session: object, payload: object, *, actor: str) -> object:
        captured.append((payload, actor))
        return SimpleNamespace(id=uuid4(), payload_json=payload.model_dump(mode="json"))

    monkeypatch.setattr(reconcile, "append_typed_lifecycle_event", append)
    boundary = TemporalActivityBoundary(
        activity_id="10",
        activity_type="persist_v38_sequence_metric",
        task_queue="pepagent-autoresearch-control-v1",
        attempt=1,
        status="failed",
        worker_identity=None,
        temporal_event_id=72,
        temporal_event_time=datetime(2026, 8, 28, 21, 57, 10, tzinfo=UTC),
        error_type="TemporalStartToCloseTimeout",
        error_message="activity StartToClose timeout",
        timeout_type="TIMEOUT_TYPE_START_TO_CLOSE",
    )

    first = await reconcile_temporal_activity_boundaries(
        _Session(run, [prior_started]),  # type: ignore[arg-type]
        run_id=run_id,
        workflow_id=workflow_id,
        temporal_run_id=temporal_run_id,
        temporal_status="RUNNING",
        boundaries=[boundary],
        execute=True,
    )

    assert len(first.appended_event_ids) == 1
    payload, actor = captured[0]
    assert payload.worker_identity == "stuck-worker"
    assert payload.expected == 4
    assert payload.event_source == "temporal_history_reconciler"
    assert actor == reconcile.RECONCILER_ACTOR

    existing_failed = SimpleNamespace(
        payload_json=payload.model_dump(mode="json")
    )
    captured.clear()
    replay = await reconcile_temporal_activity_boundaries(
        _Session(run, [prior_started, existing_failed]),  # type: ignore[arg-type]
        run_id=run_id,
        workflow_id=workflow_id,
        temporal_run_id=temporal_run_id,
        temporal_status="RUNNING",
        boundaries=[boundary],
        execute=True,
    )

    assert not replay.appended_event_ids
    assert replay.skipped_semantic_keys == (("10", 1, "failed"),)
    assert captured == []
