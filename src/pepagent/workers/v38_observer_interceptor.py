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

# Progress telemetry is best effort. Activity boundary events are authoritative
# and use a finite retry budget: a database outage cannot hang a worker slot,
# but an activity cannot silently proceed or finish without a PostgreSQL audit.
# The independent NullPool below keeps those bounded writes out of the
# scientific transaction pool.
OBSERVER_DATABASE_TIMEOUT_SECONDS = 2.0
OBSERVER_SNAPSHOT_TIMEOUT_SECONDS = 0.5
OBSERVER_MAX_PENDING_PROGRESS_WRITES = 128
OBSERVER_BOUNDARY_RETRY_INITIAL_SECONDS = 0.25
OBSERVER_BOUNDARY_RETRY_MAX_SECONDS = 30.0
OBSERVER_BOUNDARY_MAX_ATTEMPTS = 3

_OBSERVER_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


class ActivityAuditPersistenceError(RuntimeError):
    """A bounded PostgreSQL lifecycle write could not be made authoritative."""


def _request_from_input(input: ExecuteActivityInput) -> dict[str, Any] | None:
    if not input.args or not isinstance(input.args[0], dict):
        return None
    return input.args[0]


def _expected_work(activity_type: str, request: dict[str, Any]) -> int:
    if activity_type == "generate_v38_sequence_cell":
        cell = request.get("cell", request)
        return int(cell.get("requested_proposals", 1)) if isinstance(cell, dict) else 1
    for key in ("candidates", "candidate_ids", "tasks", "proposals", "actions"):
        value = request.get(key)
        if isinstance(value, list):
            return len(value)
    action_plan = request.get("action_plan")
    if isinstance(action_plan, dict) and isinstance(action_plan.get("actions"), list):
        return len(action_plan["actions"])
    if activity_type == "score_v38_multitarget_rosetta":
        return int(request.get("nstruct", request.get("decoy_count", 1)))
    return 1


def _target_key(request: dict[str, Any]) -> str | None:
    for candidate in (
        request.get("branch_key"),
        request.get("target_key"),
        (request.get("action_plan") or {}).get("branch_key")
        if isinstance(request.get("action_plan"), dict)
        else None,
    ):
        if candidate is not None and str(candidate).strip():
            return str(candidate)
    return None


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
    # is degraded; the dedicated NullPool confines it to the audit path.
    async with ObserverSessionFactory() as session, session.begin():
        run: ExperimentRun | None = None
        if payload.target_key is None or topology_payload is None:
            run = await session.get(ExperimentRun, payload.run_id)
        if run is not None:
            run_target = run.spec_json.get("branch_key") or run.spec_json.get(
                "target_key"
            )
            if run_target is not None:
                if payload.target_key is None:
                    payload = payload.model_copy(
                        update={"target_key": str(run_target)}
                    )
                elif payload.target_key != str(run_target):
                    raise ValueError("activity audit target differs from its run")
        await append_typed_lifecycle_event(session, payload)
        if topology_payload is None:
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
) -> bool:
    try:
        await _await_observer_operation(
            _persist_durable_event(payload=payload, topology_payload=topology_payload),
            timeout_seconds=OBSERVER_DATABASE_TIMEOUT_SECONDS,
        )
        return True
    except Exception as error:  # One audit attempt is bounded; the caller owns policy.
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
        await _await_observer_operation(
            asyncio.to_thread(
                write_transient_snapshot, snapshot, root=Path("var/observer")
            ),
            timeout_seconds=OBSERVER_SNAPSHOT_TIMEOUT_SECONDS,
        )
    except Exception:
        # The transient fallback is observer-only as well.  It is intentionally
        # fail-open after its own deadline.
        pass
    return False


async def _await_observer_operation(
    operation: Any,
    *,
    timeout_seconds: float,
) -> Any:
    """Bound observer latency without joining a slow cancellation cleanup.

    ``asyncio.wait_for`` cancels a timed-out task and then waits for that task to
    finish cancelling.  A degraded asyncpg connect/rollback can remain in that
    cleanup path well past the declared timeout and occupy the scientific
    worker slot.  Detach the cleanup instead; its result is still observed by
    the normal background-task callback.
    """

    task = asyncio.create_task(operation)
    done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
    if task in done:
        return task.result()
    task.cancel()
    _OBSERVER_BACKGROUND_TASKS.add(task)
    task.add_done_callback(_observer_task_done)
    raise TimeoutError("observer operation exceeded its bounded deadline")


async def _persist_boundary_event_until_durable(
    *,
    payload: ActivityLifecyclePayload,
    topology_payload: dict[str, Any] | None,
) -> None:
    """Retry a boundary write finitely or make its audit failure explicit."""

    delay = OBSERVER_BOUNDARY_RETRY_INITIAL_SECONDS
    for attempt in range(1, OBSERVER_BOUNDARY_MAX_ATTEMPTS + 1):
        if await _persist_event(payload=payload, topology_payload=topology_payload):
            return
        if attempt == OBSERVER_BOUNDARY_MAX_ATTEMPTS:
            break
        await asyncio.sleep(delay)
        delay = min(delay * 2.0, OBSERVER_BOUNDARY_RETRY_MAX_SECONDS)
    raise ActivityAuditPersistenceError(
        "PostgreSQL activity audit persistence failed after "
        f"{OBSERVER_BOUNDARY_MAX_ATTEMPTS} bounded attempts: "
        f"{payload.activity_type}/{payload.activity_id}/attempt-{payload.attempt}/"
        f"{payload.status}"
    )


def _observer_task_done(task: asyncio.Task[Any]) -> None:
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
    # Only progress is lossy observer telemetry. Semantic boundary events are
    # awaited with a finite retry budget by the inbound interceptor below.
    if payload.status != "progress":
        raise ValueError("semantic activity boundary events cannot be fire-and-forget")
    if len(_OBSERVER_BACKGROUND_TASKS) >= OBSERVER_MAX_PENDING_PROGRESS_WRITES:
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
        expected = progress.get("expected")
        if not isinstance(expected, int):
            expected = progress.get("total")
        if not isinstance(expected, int):
            expected = state["expected"]
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
                worker_identity=state["worker_identity"],
                workflow_id=state["workflow_id"],
                workflow_run_id=state["workflow_run_id"],
                target_key=state["target_key"],
            ),
            topology_payload=state["topology_payload"],
        )


class _ObserverInbound(ActivityInboundInterceptor):
    def __init__(
        self,
        next: ActivityInboundInterceptor,
        worker_role: str,
        worker_identity: str | None = None,
    ) -> None:
        super().__init__(next)
        self.worker_role = worker_role
        self.worker_identity = worker_identity
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
                "worker_identity": self.worker_identity,
                "workflow_id": getattr(info, "workflow_id", None),
                "workflow_run_id": getattr(info, "workflow_run_id", None),
                "target_key": _target_key(request),
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
            "worker_identity": self.worker_identity,
            "workflow_id": getattr(info, "workflow_id", None),
            "workflow_run_id": getattr(info, "workflow_run_id", None),
            "target_key": _target_key(request),
        }
        try:
            await _persist_boundary_event_until_durable(
                payload=ActivityLifecyclePayload(**base, status="started", completed=0),
                topology_payload=topology_payload,
            )
            try:
                result = await self.next.execute_activity(input)
            except (asyncio.CancelledError, TemporalCancelledError) as error:
                try:
                    await _persist_boundary_event_until_durable(
                        payload=ActivityLifecyclePayload(
                            **base, status="cancelled", completed=0
                        ),
                        topology_payload=topology_payload,
                    )
                except ActivityAuditPersistenceError as audit_error:
                    raise audit_error from error
                raise
            except Exception as error:
                try:
                    await _persist_boundary_event_until_durable(
                        payload=ActivityLifecyclePayload(
                            **base,
                            status="failed",
                            completed=0,
                            error_type=type(error).__name__,
                            error_message=(str(error) or repr(error))[:4000],
                        ),
                        topology_payload=topology_payload,
                    )
                except ActivityAuditPersistenceError as audit_error:
                    raise audit_error from error
                raise
            await _persist_boundary_event_until_durable(
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
    def __init__(self, worker_role: str, worker_identity: str | None = None) -> None:
        self.worker_role = worker_role
        self.worker_identity = worker_identity

    def intercept_activity(
        self, next: ActivityInboundInterceptor
    ) -> ActivityInboundInterceptor:
        return _ObserverInbound(next, self.worker_role, self.worker_identity)
