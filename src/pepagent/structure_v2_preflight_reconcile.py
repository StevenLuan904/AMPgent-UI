from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from google.protobuf.json_format import MessageToDict
from sqlalchemy import func, select
from temporalio.client import Client

from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    persist_operational_call,
)
from pepagent.db.models import Candidate, Evaluation, ExperimentRun, LifecycleEvent, ToolCall
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import ObserverSessionFactory
from pepagent.domain.enums import RunStatus
from pepagent.settings import get_settings
from pepagent.structure_v2_binding import STRUCTURE_COHORT_IMPORT_TOOL

PREFLIGHT_ACTIVITY = "preflight_structure_v2_target_request_v2"
FAILURE_MESSAGE = "structure v2 request differs from its current PG eligibility binding"
RECONCILE_SCHEMA = "ampgent.structure-v2-preflight-failure-reconciliation.1"
RECONCILE_ACTOR = "structure-v2-preflight-history-reconciler"
ALLOWED_ACTIVITY_TYPES = frozenset({PREFLIGHT_ACTIVITY, "mark_run_failed"})


@dataclass(frozen=True)
class PreflightFailureBoundary:
    activity_id: str
    activity_type: str
    task_queue: str
    final_attempt: int
    worker_identity: str
    scheduled_event_id: int
    started_event_id: int
    failed_event_id: int
    scheduled_at: str
    started_at: str
    failed_at: str
    error_message: str
    error_type: str | None
    retry_state: str | None


def _attrs(event: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = event.get(name)
    if not isinstance(value, Mapping):
        raise ValueError(f"structure v2 Temporal event lacks {name}")
    return value


def _failure_details(failure: Mapping[str, Any]) -> tuple[str, str | None]:
    message = str(failure.get("message") or "")
    application = failure.get("application_failure_info")
    error_type = (
        str(application.get("type"))
        if isinstance(application, Mapping) and application.get("type")
        else None
    )
    cause = failure.get("cause")
    if isinstance(cause, Mapping):
        cause_message, cause_type = _failure_details(cause)
        if cause_message:
            return cause_message, cause_type or error_type
    return message, error_type


def extract_preflight_failure_boundary(
    events: Sequence[Mapping[str, Any]],
) -> PreflightFailureBoundary:
    scheduled = [
        event
        for event in events
        if event.get("event_type") == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED"
    ]
    scheduled_types = {
        str(
            _attrs(
                _attrs(event, "activity_task_scheduled_event_attributes"),
                "activity_type",
            ).get("name")
        )
        for event in scheduled
    }
    if scheduled_types - ALLOWED_ACTIVITY_TYPES:
        raise ValueError("structure v2 failed preflight history contains scientific activities")
    preflight_scheduled = [
        event
        for event in scheduled
        if _attrs(_attrs(event, "activity_task_scheduled_event_attributes"), "activity_type").get(
            "name"
        )
        == PREFLIGHT_ACTIVITY
    ]
    if len(preflight_scheduled) != 1:
        raise ValueError("structure v2 failed history lacks one preflight schedule")
    scheduled_event = preflight_scheduled[0]
    scheduled_event_id = int(scheduled_event["event_id"])
    schedule_attrs = _attrs(
        scheduled_event,
        "activity_task_scheduled_event_attributes",
    )
    starts = [
        event
        for event in events
        if event.get("event_type") == "EVENT_TYPE_ACTIVITY_TASK_STARTED"
        and int(
            _attrs(event, "activity_task_started_event_attributes").get(
                "scheduled_event_id", 0
            )
        )
        == scheduled_event_id
    ]
    failures = [
        event
        for event in events
        if event.get("event_type") == "EVENT_TYPE_ACTIVITY_TASK_FAILED"
        and int(
            _attrs(event, "activity_task_failed_event_attributes").get(
                "scheduled_event_id", 0
            )
        )
        == scheduled_event_id
    ]
    if len(starts) != 1 or len(failures) != 1:
        raise ValueError("structure v2 failed history lacks one retained final attempt")
    start = starts[0]
    failure = failures[0]
    start_attrs = _attrs(start, "activity_task_started_event_attributes")
    failure_attrs = _attrs(failure, "activity_task_failed_event_attributes")
    if int(failure_attrs.get("started_event_id", 0)) != int(start["event_id"]):
        raise ValueError("structure v2 preflight failure start identity differs")
    raw_failure = _attrs(failure_attrs, "failure")
    error_message, error_type = _failure_details(raw_failure)
    if error_message != FAILURE_MESSAGE:
        raise ValueError("structure v2 preflight failure message differs")
    final_attempt = int(start_attrs.get("attempt") or 1)
    if final_attempt < 1:
        raise ValueError("structure v2 preflight attempt count differs")
    task_queue = str(_attrs(schedule_attrs, "task_queue").get("name") or "")
    worker_identity = str(start_attrs.get("identity") or "")
    if not task_queue or not worker_identity:
        raise ValueError("structure v2 preflight worker routing evidence is incomplete")
    if not events or events[-1].get("event_type") != "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED":
        raise ValueError("structure v2 preflight workflow is not terminal failed")
    return PreflightFailureBoundary(
        activity_id=str(schedule_attrs.get("activity_id") or ""),
        activity_type=PREFLIGHT_ACTIVITY,
        task_queue=task_queue,
        final_attempt=final_attempt,
        worker_identity=worker_identity,
        scheduled_event_id=scheduled_event_id,
        started_event_id=int(start["event_id"]),
        failed_event_id=int(failure["event_id"]),
        scheduled_at=str(scheduled_event["event_time"]),
        started_at=str(start["event_time"]),
        failed_at=str(failure["event_time"]),
        error_message=error_message,
        error_type=error_type,
        retry_state=(
            str(failure_attrs.get("retry_state"))
            if failure_attrs.get("retry_state")
            else None
        ),
    )


async def _history(
    client: Client,
    run: ExperimentRun,
) -> tuple[str, list[dict[str, Any]]]:
    handle = client.get_workflow_handle(
        str(run.temporal_workflow_id),
        run_id=str(run.temporal_run_id),
    )
    description = await handle.describe()
    events = [
        MessageToDict(
            event,
            preserving_proto_field_name=True,
            use_integers_for_enums=False,
        )
        async for event in handle.fetch_history_events()
    ]
    return description.status.name, events


async def reconcile_failed_structure_v2_preflights(
    predecessor_reservation_key: str,
    *,
    execute: bool,
) -> dict[str, Any]:
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    async with ObserverSessionFactory() as session:
        runs = tuple(
            await session.scalars(
                select(ExperimentRun)
                .where(
                    ExperimentRun.spec_json["structure_v2_reservation_key"].as_string()
                    == predecessor_reservation_key
                )
                .order_by(ExperimentRun.spec_json["target_key"].as_string())
            )
        )
    if len(runs) != 6:
        raise ValueError("structure v2 preflight reconciliation requires six runs")
    observations = []
    for run in runs:
        status, events = await _history(client, run)
        if status != "FAILED":
            raise ValueError("structure v2 preflight reconciliation workflow is not failed")
        observations.append((run, events, extract_preflight_failure_boundary(events)))

    receipts = []
    for observed_run, events, boundary in observations:
        async with ObserverSessionFactory() as session, session.begin():
            run = await session.get(ExperimentRun, observed_run.id, with_for_update=True)
            if (
                run is None
                or run.status != RunStatus.FAILED
                or run.finished_at is None
                or run.temporal_workflow_id != observed_run.temporal_workflow_id
                or run.temporal_run_id != observed_run.temporal_run_id
            ):
                raise ValueError("structure v2 failed run PG binding differs")
            failed_events = tuple(
                await session.scalars(
                    select(LifecycleEvent).where(
                        LifecycleEvent.aggregate_type == "run",
                        LifecycleEvent.aggregate_id == run.id,
                        LifecycleEvent.event_type == "run.failed",
                    )
                )
            )
            if len(failed_events) != 1:
                raise ValueError("structure v2 failed run lacks one run.failed event")
            calls = tuple(
                await session.scalars(select(ToolCall).where(ToolCall.run_id == run.id))
            )
            if any(call.tool_name != STRUCTURE_COHORT_IMPORT_TOOL for call in calls):
                raise ValueError("structure v2 failed preflight run has scientific ToolCalls")
            evaluation_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Evaluation)
                    .where(Evaluation.tool_call_id.in_([call.id for call in calls]))
                )
                or 0
            )
            if evaluation_count:
                raise ValueError("structure v2 failed preflight run has evaluations")
            candidate_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Candidate)
                    .where(Candidate.run_id == run.id)
                )
                or 0
            )
            target_key = str(run.spec_json.get("target_key", ""))
            common = {
                "schema_version": RECONCILE_SCHEMA,
                "run_id": str(run.id),
                "workflow_id": run.temporal_workflow_id,
                "workflow_run_id": run.temporal_run_id,
                "target_key": target_key,
                "activity_id": boundary.activity_id,
                "activity_type": boundary.activity_type,
                "task_queue": boundary.task_queue,
                "attempt": boundary.final_attempt,
                "attempts_total": boundary.final_attempt,
                "worker_identity": boundary.worker_identity,
                "event_source": "temporal_history_reconciler",
            }
            payloads = (
                (
                    "activity.started",
                    {
                        **common,
                        "status": "started",
                        "temporal_event_id": boundary.started_event_id,
                        "temporal_event_time": boundary.started_at,
                    },
                ),
                (
                    "activity.failed",
                    {
                        **common,
                        "status": "failed",
                        "error_type": boundary.error_type,
                        "error_message": boundary.error_message,
                        "temporal_retry_state": boundary.retry_state,
                        "temporal_event_id": boundary.failed_event_id,
                        "temporal_event_time": boundary.failed_at,
                    },
                ),
                (
                    "activity.retry_attempts_reconciled",
                    {
                        **common,
                        "status": "failed",
                        "attempt_range": [1, boundary.final_attempt],
                        "retained_attempt": boundary.final_attempt,
                        "earlier_attempt_timestamps_available": False,
                        "evidence_note": (
                            "Temporal retained the final attempt number and last failure; "
                            "individual earlier retry timestamps/identities were not fabricated"
                        ),
                        "scheduled_event_id": boundary.scheduled_event_id,
                        "failed_event_id": boundary.failed_event_id,
                        "history_event_count": len(events),
                    },
                ),
            )
            event_ids = []
            if execute:
                repository = ExperimentRepository(session)
                for event_type, payload in payloads:
                    key = (
                        "structure-v2-preflight-reconcile:"
                        f"{run.temporal_run_id}:{boundary.activity_id}:"
                        f"{boundary.final_attempt}:{event_type}"
                    )
                    payload["event_idempotency_key"] = key
                    event = await repository.append_event(
                        "run",
                        run.id,
                        event_type,
                        RECONCILE_ACTOR,
                        payload,
                        idempotency_key=key,
                    )
                    event_ids.append(str(event.id))
                started_at = datetime.now(UTC)
                operational_run, operational_call = await persist_operational_call(
                    session,
                    OperationalCallRecord(
                        operation_key=(
                            "structure-v2-preflight-failure-reconcile:"
                            f"{predecessor_reservation_key}:{run.temporal_run_id}"
                        ),
                        target_key=target_key,
                        purpose="audit_reconciliation",
                        tool_name="structure-v2-preflight-history-reconciler",
                        tool_version="1",
                        status="succeeded",
                        input_payload={
                            "formal_run_id": str(run.id),
                            "workflow_id": run.temporal_workflow_id,
                            "temporal_run_id": run.temporal_run_id,
                            "history_event_count": len(events),
                        },
                        parameters={"write_mode": "append_only_exact_history_evidence"},
                        execution_context={
                            "host": platform.node(),
                            "process_id": os.getpid(),
                            "interface": (
                                "pepagent.structure_v2_preflight_reconcile"
                            ),
                        },
                        output_payload={
                            "activity_id": boundary.activity_id,
                            "attempts_total": boundary.final_attempt,
                            "appended_or_reused_event_ids": event_ids,
                            "run_failed_event_id": str(failed_events[0].id),
                            "scientific_tool_call_count": 0,
                            "evaluation_count": 0,
                        },
                        started_at=started_at,
                        finished_at=datetime.now(UTC),
                        actor=RECONCILE_ACTOR,
                    ),
                )
                operational = {
                    "operational_run_id": str(operational_run.id),
                    "tool_call_id": str(operational_call.id),
                }
            else:
                operational = None
            receipts.append(
                {
                    "target_key": target_key,
                    "run_id": str(run.id),
                    "workflow_id": run.temporal_workflow_id,
                    "temporal_run_id": run.temporal_run_id,
                    "temporal_status": "FAILED",
                    "pg_status": str(run.status),
                    "history_event_count": len(events),
                    "preflight": asdict(boundary),
                    "run_failed_event_id": str(failed_events[0].id),
                    "candidate_count": candidate_count,
                    "scientific_tool_call_count": 0,
                    "evaluation_count": 0,
                    "event_ids": event_ids,
                    "operational_call": operational,
                }
            )
    return {
        "schema_version": RECONCILE_SCHEMA,
        "executed": execute,
        "run_count": len(receipts),
        "scientific_tool_call_count": 0,
        "evaluation_count": 0,
        "runs": receipts,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcile failed Structure v2 preflight attempts into PostgreSQL"
    )
    parser.add_argument("--predecessor-reservation-key", required=True)
    parser.add_argument("--execute", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    result = asyncio.run(
        reconcile_failed_structure_v2_preflights(
            args.predecessor_reservation_key,
            execute=bool(args.execute),
        )
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()


__all__ = [
    "PreflightFailureBoundary",
    "extract_preflight_failure_boundary",
    "reconcile_failed_structure_v2_preflights",
]
