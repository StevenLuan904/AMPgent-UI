from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import ExperimentRun, LifecycleEvent
from pepagent.workflow_observer_contract import (
    ACTIVITY_STAGE_BINDINGS,
    ActivityLifecyclePayload,
    append_typed_lifecycle_event,
    display_category_for_stage,
)

BoundaryStatus = Literal["started", "succeeded", "failed", "cancelled"]
RECONCILER_ACTOR = "temporal-activity-history-reconciler"

_TASK_QUEUE_ROLES = {
    "pepagent-autoresearch-control-v1": "autoresearch-control",
    "pepagent-autoresearch-generator-v1": "autoresearch-generator",
    "pepagent-autoresearch-persistence-v1": "autoresearch-persistence",
    "pepagent-autoresearch-metrics-v1": "autoresearch-metrics",
}
_TIMEOUT_ERROR_TYPES = {
    "TIMEOUT_TYPE_START_TO_CLOSE": "TemporalStartToCloseTimeout",
    "TIMEOUT_TYPE_HEARTBEAT": "TemporalHeartbeatTimeout",
    "TIMEOUT_TYPE_SCHEDULE_TO_CLOSE": "TemporalScheduleToCloseTimeout",
    "TIMEOUT_TYPE_SCHEDULE_TO_START": "TemporalScheduleToStartTimeout",
}


@dataclass(frozen=True)
class TemporalActivityBoundary:
    activity_id: str
    activity_type: str
    task_queue: str
    attempt: int
    status: BoundaryStatus
    worker_identity: str | None
    temporal_event_id: int
    temporal_event_time: datetime
    error_type: str | None = None
    error_message: str | None = None
    timeout_type: str | None = None
    retry_state: str | None = None

    @property
    def semantic_key(self) -> tuple[str, int, BoundaryStatus]:
        return (self.activity_id, self.attempt, self.status)


@dataclass(frozen=True)
class TemporalActivityReconciliation:
    run_id: UUID
    workflow_id: str
    temporal_run_id: str
    temporal_status: str
    extracted_count: int
    appended_event_ids: tuple[UUID, ...]
    skipped_semantic_keys: tuple[tuple[str, int, BoundaryStatus], ...]
    missing_semantic_keys: tuple[tuple[str, int, BoundaryStatus], ...]


def _event_attributes(event: dict[str, Any], suffix: str) -> dict[str, Any]:
    value = event.get(suffix)
    if not isinstance(value, dict):
        raise ValueError(f"Temporal history event lacks {suffix}")
    return value


def _parse_event_time(value: Any) -> datetime:
    normalized = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.utcoffset() is None:
        raise ValueError("Temporal history event time is not timezone-aware")
    return parsed


def _failure_timeout_type(failure: dict[str, Any]) -> str | None:
    timeout = failure.get("timeout_failure_info")
    if isinstance(timeout, dict) and timeout.get("timeout_type"):
        return str(timeout["timeout_type"])
    cause = failure.get("cause")
    if isinstance(cause, dict):
        return _failure_timeout_type(cause)
    return None


def _failure_error_type(failure: dict[str, Any]) -> tuple[str, str | None]:
    timeout_type = _failure_timeout_type(failure)
    if timeout_type is not None:
        return _TIMEOUT_ERROR_TYPES.get(timeout_type, "TemporalActivityTimeout"), timeout_type
    application = failure.get("application_failure_info")
    if isinstance(application, dict) and application.get("type"):
        return str(application["type"]), None
    if failure.get("canceled_failure_info") is not None:
        return "TemporalActivityCancelled", None
    if str(failure.get("source") or "").lower() == "server":
        return "TemporalServerActivityFailure", None
    return "TemporalActivityFailure", None


def _failed_boundary(
    *,
    scheduled: dict[str, Any],
    attempt: int,
    worker_identity: str | None,
    event: dict[str, Any],
    failure: dict[str, Any],
    retry_state: Any = None,
) -> TemporalActivityBoundary:
    error_type, timeout_type = _failure_error_type(failure)
    message = str(failure.get("message") or "Temporal activity failure")[:4000]
    return TemporalActivityBoundary(
        activity_id=str(scheduled["activity_id"]),
        activity_type=str(scheduled["activity_type"]),
        task_queue=str(scheduled["task_queue"]),
        attempt=attempt,
        status="failed",
        worker_identity=worker_identity,
        temporal_event_id=int(event["event_id"]),
        temporal_event_time=_parse_event_time(event["event_time"]),
        error_type=error_type,
        error_message=message,
        timeout_type=timeout_type,
        retry_state=(str(retry_state) if retry_state else None),
    )


def extract_temporal_activity_boundaries(
    events: Sequence[dict[str, Any]],
) -> tuple[TemporalActivityBoundary, ...]:
    """Translate immutable Temporal history into per-attempt audit boundaries."""

    scheduled: dict[int, dict[str, Any]] = {}
    started: dict[int, dict[str, Any]] = {}
    result: dict[tuple[str, int, BoundaryStatus], TemporalActivityBoundary] = {}
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED":
            attrs = _event_attributes(event, "activity_task_scheduled_event_attributes")
            activity_type = attrs.get("activity_type")
            task_queue = attrs.get("task_queue")
            if not isinstance(activity_type, dict) or not isinstance(task_queue, dict):
                raise ValueError("Temporal scheduled activity identity is incomplete")
            row = {
                "activity_id": str(attrs.get("activity_id") or ""),
                "activity_type": str(activity_type.get("name") or ""),
                "task_queue": str(task_queue.get("name") or ""),
            }
            if not all(row.values()):
                raise ValueError("Temporal scheduled activity identity is empty")
            scheduled[int(event["event_id"])] = row
            continue
        if event_type == "EVENT_TYPE_ACTIVITY_TASK_STARTED":
            attrs = _event_attributes(event, "activity_task_started_event_attributes")
            scheduled_id = int(attrs["scheduled_event_id"])
            schedule = scheduled.get(scheduled_id)
            if schedule is None:
                raise ValueError("Temporal started activity has no scheduled event")
            attempt = int(attrs.get("attempt") or 1)
            worker_identity = str(attrs.get("identity") or "") or None
            started[int(event["event_id"])] = {
                "scheduled_event_id": scheduled_id,
                "attempt": attempt,
                "worker_identity": worker_identity,
            }
            current = TemporalActivityBoundary(
                activity_id=str(schedule["activity_id"]),
                activity_type=str(schedule["activity_type"]),
                task_queue=str(schedule["task_queue"]),
                attempt=attempt,
                status="started",
                worker_identity=worker_identity,
                temporal_event_id=int(event["event_id"]),
                temporal_event_time=_parse_event_time(event["event_time"]),
            )
            result[current.semantic_key] = current
            last_failure = attrs.get("last_failure")
            if attempt > 1 and isinstance(last_failure, dict) and last_failure:
                previous = _failed_boundary(
                    scheduled=schedule,
                    attempt=attempt - 1,
                    worker_identity=None,
                    event=event,
                    failure=last_failure,
                )
                result[previous.semantic_key] = previous
            continue
        terminal_suffixes = {
            "EVENT_TYPE_ACTIVITY_TASK_COMPLETED": (
                "activity_task_completed_event_attributes",
                "succeeded",
            ),
            "EVENT_TYPE_ACTIVITY_TASK_FAILED": (
                "activity_task_failed_event_attributes",
                "failed",
            ),
            "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT": (
                "activity_task_timed_out_event_attributes",
                "failed",
            ),
            "EVENT_TYPE_ACTIVITY_TASK_CANCELED": (
                "activity_task_canceled_event_attributes",
                "cancelled",
            ),
        }
        terminal = terminal_suffixes.get(event_type)
        if terminal is None:
            continue
        suffix, status = terminal
        attrs = _event_attributes(event, suffix)
        scheduled_id = int(attrs["scheduled_event_id"])
        schedule = scheduled.get(scheduled_id)
        if schedule is None:
            raise ValueError("Temporal terminal activity has no scheduled event")
        started_event_id = int(attrs.get("started_event_id") or 0)
        attempt_state = started.get(started_event_id, {})
        attempt = int(attempt_state.get("attempt") or 1)
        worker_identity = str(
            attrs.get("identity") or attempt_state.get("worker_identity") or ""
        ) or None
        if status == "failed":
            failure = attrs.get("failure")
            if not isinstance(failure, dict) or not failure:
                raise ValueError("Temporal failed activity has no failure payload")
            boundary = _failed_boundary(
                scheduled=schedule,
                attempt=attempt,
                worker_identity=worker_identity,
                event=event,
                failure=failure,
                retry_state=attrs.get("retry_state"),
            )
        else:
            boundary = TemporalActivityBoundary(
                activity_id=str(schedule["activity_id"]),
                activity_type=str(schedule["activity_type"]),
                task_queue=str(schedule["task_queue"]),
                attempt=attempt,
                status=status,
                worker_identity=worker_identity,
                temporal_event_id=int(event["event_id"]),
                temporal_event_time=_parse_event_time(event["event_time"]),
                retry_state=(
                    str(attrs["retry_state"]) if attrs.get("retry_state") else None
                ),
            )
        result[boundary.semantic_key] = boundary
    return tuple(
        sorted(
            result.values(),
            key=lambda item: (
                item.temporal_event_id,
                item.activity_id,
                item.attempt,
                item.status,
            ),
        )
    )


def _payload_semantic_key(payload: dict[str, Any]) -> tuple[str, int, str] | None:
    activity_id = payload.get("activity_id")
    attempt = payload.get("attempt")
    status = payload.get("status")
    if not activity_id or not isinstance(attempt, int) or not status:
        return None
    return (str(activity_id), attempt, str(status))


def _context_for_attempt(
    events: Iterable[LifecycleEvent], activity_id: str, attempt: int
) -> list[dict[str, Any]]:
    return [
        event.payload_json
        for event in events
        if event.payload_json.get("activity_id") == activity_id
        and event.payload_json.get("attempt") == attempt
    ]


async def reconcile_temporal_activity_boundaries(
    session: AsyncSession,
    *,
    run_id: UUID,
    workflow_id: str,
    temporal_run_id: str,
    temporal_status: str,
    boundaries: Sequence[TemporalActivityBoundary],
    execute: bool,
) -> TemporalActivityReconciliation:
    """Append only missing lifecycle facts; never mutate Temporal or run science state."""

    run = await session.get(ExperimentRun, run_id, with_for_update=execute)
    if run is None:
        raise ValueError("Temporal activity reconciliation run does not exist")
    if (
        run.temporal_workflow_id != workflow_id
        or run.temporal_run_id != temporal_run_id
    ):
        raise ValueError("Temporal activity reconciliation binding drifted")
    target_key = str(
        run.spec_json.get("branch_key") or run.spec_json.get("target_key") or ""
    )
    if not target_key:
        raise ValueError("Temporal activity reconciliation target is missing")
    lifecycle_events = list(
        await session.scalars(
            select(LifecycleEvent)
            .where(
                LifecycleEvent.aggregate_type == "run",
                LifecycleEvent.aggregate_id == run_id,
                LifecycleEvent.event_type.like("activity.%"),
            )
            .order_by(LifecycleEvent.sequence_no)
        )
    )
    existing: dict[tuple[str, int, str], list[LifecycleEvent]] = {}
    for event in lifecycle_events:
        key = _payload_semantic_key(event.payload_json)
        if key is not None and key[2] != "progress":
            existing.setdefault(key, []).append(event)
    duplicates = {key: rows for key, rows in existing.items() if len(rows) > 1}
    if duplicates:
        raise ValueError(
            "PostgreSQL has duplicate activity boundary events: "
            + ", ".join(str(key) for key in sorted(duplicates))
        )
    appended: list[UUID] = []
    skipped: list[tuple[str, int, BoundaryStatus]] = []
    missing: list[tuple[str, int, BoundaryStatus]] = []
    for boundary in boundaries:
        semantic_key = boundary.semantic_key
        if semantic_key in existing:
            current = existing[semantic_key][0].payload_json
            if current.get("activity_type") != boundary.activity_type:
                raise ValueError("existing activity boundary type drifted")
            skipped.append(semantic_key)
            continue
        missing.append(semantic_key)
        context = _context_for_attempt(
            lifecycle_events, boundary.activity_id, boundary.attempt
        )
        expected = max((int(item.get("expected") or 0) for item in context), default=0)
        progressed = max((int(item.get("completed") or 0) for item in context), default=0)
        completed = expected if boundary.status == "succeeded" else progressed
        prior_identity = next(
            (
                str(item["worker_identity"])
                for item in reversed(context)
                if item.get("worker_identity")
            ),
            None,
        )
        worker_identity = boundary.worker_identity or prior_identity
        stage = ACTIVITY_STAGE_BINDINGS.get(boundary.activity_type)
        if stage is None:
            raise ValueError(
                f"unknown activity stage binding: {boundary.activity_type}"
            )
        worker_role = _TASK_QUEUE_ROLES.get(boundary.task_queue)
        if worker_role is None:
            raise ValueError(
                f"unknown AutoResearch activity task queue: {boundary.task_queue}"
            )
        payload = ActivityLifecyclePayload(
            run_id=run_id,
            activity_id=boundary.activity_id,
            activity_type=boundary.activity_type,
            logical_stage=stage,
            display_category=display_category_for_stage(stage),
            attempt=boundary.attempt,
            status=boundary.status,
            completed=completed,
            expected=expected,
            worker_role=worker_role,
            task_queue=boundary.task_queue,
            worker_identity=worker_identity,
            workflow_id=workflow_id,
            workflow_run_id=temporal_run_id,
            target_key=target_key,
            error_type=boundary.error_type,
            error_message=boundary.error_message,
            event_source="temporal_history_reconciler",
            temporal_event_id=boundary.temporal_event_id,
            temporal_event_time=boundary.temporal_event_time,
            temporal_timeout_type=boundary.timeout_type,
            temporal_retry_state=boundary.retry_state,
        )
        if not execute:
            continue
        event = await append_typed_lifecycle_event(
            session,
            payload,
            actor=RECONCILER_ACTOR,
        )
        lifecycle_events.append(event)
        existing[semantic_key] = [event]
        appended.append(event.id)
    return TemporalActivityReconciliation(
        run_id=run_id,
        workflow_id=workflow_id,
        temporal_run_id=temporal_run_id,
        temporal_status=temporal_status,
        extracted_count=len(boundaries),
        appended_event_ids=tuple(appended),
        skipped_semantic_keys=tuple(skipped),
        missing_semantic_keys=tuple(missing),
    )


__all__ = [
    "RECONCILER_ACTOR",
    "TemporalActivityBoundary",
    "TemporalActivityReconciliation",
    "extract_temporal_activity_boundaries",
    "reconcile_temporal_activity_boundaries",
]
