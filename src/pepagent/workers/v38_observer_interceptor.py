from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import CancelledError as TemporalCancelledError
from temporalio.worker import (
    ActivityInboundInterceptor,
    ActivityOutboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
)

from pepagent.db.models import ExperimentRun
from pepagent.db.session import ObserverSessionFactory
from pepagent.workflow_observer_contract import (
    ACTIVITY_STAGE_BINDINGS,
    ActivityLifecyclePayload,
    FormalWorkflowTopology,
    ObserverTransientSnapshot,
    append_typed_lifecycle_event,
    display_category_for_stage,
    persist_observer_checkpoints,
    write_transient_snapshot,
)

# Observer durability is deliberately best effort.  A database pool wait, a
# tunnel outage, or a slow local filesystem must never consume a scientific
# activity slot.  These deadlines bound the background writer itself; callers
# also schedule every lifecycle write off the activity's critical path below.
OBSERVER_DATABASE_TIMEOUT_SECONDS = 2.0
OBSERVER_SNAPSHOT_TIMEOUT_SECONDS = 0.5
OBSERVER_MAX_PENDING_PROGRESS_WRITES = 128

_OBSERVER_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


def _request_from_input(input: ExecuteActivityInput) -> dict[str, Any] | None:
    if not input.args or not isinstance(input.args[0], dict):
        return None
    return input.args[0]


def _expected_work(activity_type: str, request: dict[str, Any]) -> int:
    if activity_type == "generate_v38_sequence_cell":
        cell = request.get("cell", request)
        return int(cell.get("requested_proposals", 1)) if isinstance(cell, dict) else 1
    for key in ("candidates", "candidate_ids", "tasks", "proposals"):
        value = request.get(key)
        if isinstance(value, list):
            return len(value)
    if activity_type == "score_v38_multitarget_rosetta":
        return int(request.get("nstruct", request.get("decoy_count", 1)))
    return 1


def _tool_call_id(result: Any) -> UUID | None:
    if not isinstance(result, dict):
        return None
    raw = result.get("tool_call_id")
    if raw is None:
        for key in ("structure", "rosetta_evidence", "persistence_receipt"):
            nested = result.get(key)
            if isinstance(nested, dict) and nested.get("tool_call_id"):
                raw = nested["tool_call_id"]
                break
    try:
        return UUID(str(raw)) if raw else None
    except ValueError:
        return None


async def _persist_durable_event(
    *,
    payload: ActivityLifecyclePayload,
    topology_payload: dict[str, Any] | None,
) -> None:
    # Never share the scientific activity pool.  asyncio.wait_for cancels a
    # timed-out writer and connection cleanup can itself be slow when a tunnel
    # is degraded; the dedicated NullPool confines that failure to telemetry.
    async with ObserverSessionFactory() as session, session.begin():
        await append_typed_lifecycle_event(session, payload)
        if topology_payload is None:
            run = await session.get(ExperimentRun, payload.run_id)
            candidate = run.spec_json.get("workflow_topology") if run is not None else None
            if isinstance(candidate, dict):
                topology_payload = candidate
        if topology_payload is not None:
            topology = FormalWorkflowTopology.model_validate(topology_payload)
            await persist_observer_checkpoints(
                session, run_id=payload.run_id, topology=topology
            )


async def _persist_event(
    *,
    payload: ActivityLifecyclePayload,
    topology_payload: dict[str, Any] | None,
) -> None:
    try:
        await asyncio.wait_for(
            _persist_durable_event(payload=payload, topology_payload=topology_payload),
            timeout=OBSERVER_DATABASE_TIMEOUT_SECONDS,
        )
        return
    except Exception as error:  # Observer writes must not mutate the scientific result.
        snapshot = ObserverTransientSnapshot(
            run_id=payload.run_id,
            updated_at=datetime.now(UTC),
            ttl_seconds=3600,
            source="v38-observer-interceptor",
            transient={
                "observer_write_status": "failed",
                "activity_id": payload.activity_id,
                "activity_type": payload.activity_type,
                "event_status": payload.status,
                "error_type": type(error).__name__,
            },
        )
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                write_transient_snapshot,
                snapshot,
                root=Path("var/observer"),
            ),
            timeout=OBSERVER_SNAPSHOT_TIMEOUT_SECONDS,
        )
    except Exception:
        # The transient fallback is observer-only as well.  It is intentionally
        # fail-open after its own deadline.
        return


def _observer_task_done(task: asyncio.Task[None]) -> None:
    _OBSERVER_BACKGROUND_TASKS.discard(task)
    if task.cancelled():
        return
    # Retrieve a defensive unexpected exception so the event loop does not
    # emit an unhandled-task warning.  _persist_event normally absorbs all
    # observer failures itself.
    task.exception()


def _schedule_event(
    *,
    payload: ActivityLifecyclePayload,
    topology_payload: dict[str, Any] | None,
) -> None:
    # Progress is lossy observer telemetry, so cap it under backpressure.  The
    # boundary events are still admitted and each write has a hard deadline.
    if (
        payload.status == "progress"
        and len(_OBSERVER_BACKGROUND_TASKS) >= OBSERVER_MAX_PENDING_PROGRESS_WRITES
    ):
        return
    task = asyncio.create_task(
        _persist_event(payload=payload, topology_payload=topology_payload),
        name=(
            f"v38-observer:{payload.run_id}:{payload.activity_id}:"
            f"{payload.status}:{payload.completed}"
        ),
    )
    _OBSERVER_BACKGROUND_TASKS.add(task)
    task.add_done_callback(_observer_task_done)


class _ObserverOutbound(ActivityOutboundInterceptor):
    def __init__(
        self,
        next: ActivityOutboundInterceptor,
        state: ContextVar[dict[str, Any] | None],
    ) -> None:
        super().__init__(next)
        self.state = state

    def heartbeat(self, *details: Any) -> None:
        self.next.heartbeat(*details)
        state = self.state.get()
        if state is None:
            return
        progress = details[0] if details and isinstance(details[0], dict) else {}
        completed = progress.get("completed")
        expected = progress.get("expected", state["expected"])
        if not isinstance(completed, int) or completed == state["last_completed"]:
            return
        if not isinstance(expected, int) or expected < 0 or completed < 0:
            return
        state["last_completed"] = completed
        stage = ACTIVITY_STAGE_BINDINGS[state["activity_type"]]
        _schedule_event(
            payload=ActivityLifecyclePayload(
                run_id=state["run_id"],
                activity_id=state["activity_id"],
                activity_type=state["activity_type"],
                logical_stage=stage,
                display_category=display_category_for_stage(stage),
                attempt=state["attempt"],
                status="progress",
                completed=completed,
                expected=expected,
                worker_role=state["worker_role"],
                task_queue=state["task_queue"],
            ),
            topology_payload=state["topology_payload"],
        )


class _ObserverInbound(ActivityInboundInterceptor):
    def __init__(self, next: ActivityInboundInterceptor, worker_role: str) -> None:
        super().__init__(next)
        self.worker_role = worker_role
        self._state: ContextVar[dict[str, Any] | None] = ContextVar(
            "v38_observer_activity_state", default=None
        )

    def init(self, outbound: ActivityOutboundInterceptor) -> None:
        self.next.init(_ObserverOutbound(outbound, self._state))

    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        info = activity.info()
        activity_type = info.activity_type
        request = _request_from_input(input)
        if request is None or activity_type not in ACTIVITY_STAGE_BINDINGS:
            return await self.next.execute_activity(input)
        try:
            run_id = UUID(str(request["run_id"]))
        except (KeyError, ValueError):
            return await self.next.execute_activity(input)
        expected = _expected_work(activity_type, request)
        topology_payload = request.get("workflow_topology")
        if not isinstance(topology_payload, dict):
            topology_payload = None
        stage = ACTIVITY_STAGE_BINDINGS[activity_type]
        state_token = self._state.set(
            {
                "run_id": run_id,
                "activity_id": info.activity_id,
                "activity_type": activity_type,
                "attempt": info.attempt,
                "worker_role": self.worker_role,
                "task_queue": info.task_queue,
                "expected": expected,
                "topology_payload": topology_payload,
                "last_completed": -1,
            }
        )
        base = {
            "run_id": run_id,
            "activity_id": info.activity_id,
            "activity_type": activity_type,
            "logical_stage": stage,
            "display_category": display_category_for_stage(stage),
            "attempt": info.attempt,
            "expected": expected,
            "worker_role": self.worker_role,
            "task_queue": info.task_queue,
        }
        try:
            _schedule_event(
                payload=ActivityLifecyclePayload(
                    **base, status="started", completed=0
                ),
                topology_payload=topology_payload,
            )
            try:
                result = await self.next.execute_activity(input)
            except (asyncio.CancelledError, TemporalCancelledError):
                _schedule_event(
                    payload=ActivityLifecyclePayload(
                        **base, status="cancelled", completed=0
                    ),
                    topology_payload=topology_payload,
                )
                raise
            except Exception:
                _schedule_event(
                    payload=ActivityLifecyclePayload(
                        **base, status="failed", completed=0
                    ),
                    topology_payload=topology_payload,
                )
                raise
            _schedule_event(
                payload=ActivityLifecyclePayload(
                    **base,
                    status="succeeded",
                    completed=expected,
                    tool_call_id=_tool_call_id(result),
                ),
                topology_payload=topology_payload,
            )
            return result
        finally:
            self._state.reset(state_token)


class V38WorkflowObserverInterceptor(Interceptor):
    def __init__(self, worker_role: str) -> None:
        self.worker_role = worker_role

    def intercept_activity(
        self, next: ActivityInboundInterceptor
    ) -> ActivityInboundInterceptor:
        return _ObserverInbound(next, self.worker_role)
