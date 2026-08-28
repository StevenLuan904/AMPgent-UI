from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.pool import NullPool
from temporalio.worker import ExecuteActivityInput

from pepagent.db import session as db_session
from pepagent.workers import v38_observer_interceptor as observer
from pepagent.workflow_observer_contract import ActivityLifecyclePayload


def _payload(*, status: str = "started") -> ActivityLifecyclePayload:
    return ActivityLifecyclePayload(
        run_id=uuid4(),
        activity_id="activity-1",
        activity_type="mark_run_started",
        logical_stage="knowledge",
        display_category="knowledge",
        attempt=1,
        status=status,
        completed=0 if status != "succeeded" else 1,
        expected=1,
        worker_role="autoresearch-control",
        task_queue="pepagent-autoresearch-control-v1",
    )


def test_observer_uses_a_pool_isolated_from_scientific_activities() -> None:
    assert observer.ObserverSessionFactory is db_session.ObserverSessionFactory
    assert db_session.ObserverSessionFactory is not db_session.SessionFactory
    assert isinstance(db_session.observer_engine.sync_engine.pool, NullPool)
    assert not isinstance(db_session.engine.sync_engine.pool, NullPool)


@pytest.mark.asyncio
async def test_durable_audit_derives_missing_target_from_authoritative_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[ActivityLifecyclePayload] = []

    class Transaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *args: object) -> None:
            del args

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        def begin(self) -> Transaction:
            return Transaction()

        async def get(self, *args: object) -> SimpleNamespace:
            del args
            return SimpleNamespace(spec_json={"branch_key": "ANGPT1"})

    async def capture_event(
        session: object,
        payload: ActivityLifecyclePayload,
    ) -> None:
        del session
        captured.append(payload)

    monkeypatch.setattr(observer, "ObserverSessionFactory", Session)
    monkeypatch.setattr(observer, "append_typed_lifecycle_event", capture_event)

    await observer._persist_durable_event(
        payload=_payload(),
        topology_payload=None,
    )

    assert len(captured) == 1
    assert captured[0].target_key == "ANGPT1"


async def _drain_background_tasks() -> None:
    tasks = tuple(observer._OBSERVER_BACKGROUND_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_durable_and_transient_observer_timeouts_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    never = asyncio.Event()

    async def stalled_durable_write(**_: object) -> None:
        await never.wait()

    async def stalled_to_thread(*_: object, **__: object) -> None:
        await never.wait()

    monkeypatch.setattr(observer, "_persist_durable_event", stalled_durable_write)
    monkeypatch.setattr(observer.asyncio, "to_thread", stalled_to_thread)
    monkeypatch.setattr(observer, "OBSERVER_DATABASE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(observer, "OBSERVER_SNAPSHOT_TIMEOUT_SECONDS", 0.01)

    await asyncio.wait_for(
        observer._persist_event(payload=_payload(), topology_payload=None),
        timeout=0.1,
    )


@pytest.mark.asyncio
async def test_transient_boundary_audit_failure_recovers_before_science_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter((False, True, True))
    payloads: list[ActivityLifecyclePayload] = []
    scientific_calls = 0

    async def flaky_observer_write(
        *, payload: ActivityLifecyclePayload, topology_payload: dict | None
    ) -> bool:
        del topology_payload
        payloads.append(payload)
        return next(outcomes)

    async def no_delay(_: float) -> None:
        return None

    class ScientificActivity:
        async def execute_activity(self, input: ExecuteActivityInput) -> dict[str, bool]:
            nonlocal scientific_calls
            del input
            scientific_calls += 1
            return {"scientific_result": True}

    monkeypatch.setattr(observer, "_persist_event", flaky_observer_write)
    monkeypatch.setattr(observer.asyncio, "sleep", no_delay)
    monkeypatch.setattr(
        observer.activity,
        "info",
        lambda: SimpleNamespace(
            activity_id="activity-1",
            activity_type="mark_run_started",
            attempt=2,
            task_queue="pepagent-autoresearch-control-v1",
            workflow_id="workflow-1",
            workflow_run_id="workflow-run-1",
        ),
    )
    inbound = observer._ObserverInbound(
        ScientificActivity(),
        "autoresearch-control",
        "pepagent:autoresearch-control:123@worker:revision",
    )
    input = ExecuteActivityInput(
        fn=lambda: None,
        args=({"run_id": str(uuid4()), "branch_key": "ANGPT1"},),
        executor=None,
        headers={},
    )

    result = await inbound.execute_activity(input)

    assert result == {"scientific_result": True}
    assert scientific_calls == 1
    assert [payload.status for payload in payloads] == [
        "started",
        "started",
        "succeeded",
    ]
    assert {payload.attempt for payload in payloads} == {2}
    assert {payload.worker_identity for payload in payloads} == {
        "pepagent:autoresearch-control:123@worker:revision"
    }
    assert {payload.target_key for payload in payloads} == {"ANGPT1"}


@pytest.mark.asyncio
async def test_scientific_failure_is_audited_before_original_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[ActivityLifecyclePayload] = []

    async def durable_observer_write(
        *, payload: ActivityLifecyclePayload, topology_payload: dict | None
    ) -> bool:
        del topology_payload
        payloads.append(payload)
        return True

    class Outbound:
        def heartbeat(self, *details: object) -> None:
            del details

    class FailingScientificActivity:
        async def execute_activity(self, input: ExecuteActivityInput) -> None:
            del input
            outbound.heartbeat({"completed": 1, "total": 1})
            raise RuntimeError("scientific failure")

    monkeypatch.setattr(observer, "_persist_event", durable_observer_write)
    monkeypatch.setattr(
        observer.activity,
        "info",
        lambda: SimpleNamespace(
            activity_id="activity-2",
            activity_type="mark_run_started",
            attempt=1,
            task_queue="pepagent-autoresearch-control-v1",
            workflow_id="workflow-1",
            workflow_run_id="workflow-run-1",
        ),
    )
    inbound = observer._ObserverInbound(
        FailingScientificActivity(),
        "autoresearch-control",
        "pepagent:autoresearch-control:123@worker:revision",
    )
    outbound = observer._ObserverOutbound(Outbound(), inbound._state)
    input = ExecuteActivityInput(
        fn=lambda: None,
        args=({"run_id": str(uuid4()), "branch_key": "FGF2"},),
        executor=None,
        headers={},
    )

    with pytest.raises(RuntimeError, match="scientific failure"):
        await inbound.execute_activity(input)
    await asyncio.sleep(0)

    assert sorted(payload.status for payload in payloads) == [
        "failed",
        "progress",
        "started",
    ]
    failed = next(payload for payload in payloads if payload.status == "failed")
    assert failed.activity_id == "activity-2"
    assert failed.attempt == 1
    assert failed.worker_identity == "pepagent:autoresearch-control:123@worker:revision"
    assert failed.workflow_id == "workflow-1"
    assert failed.workflow_run_id == "workflow-run-1"
    assert failed.target_key == "FGF2"
    assert failed.error_type == "RuntimeError"
    assert failed.error_message == "scientific failure"
    await _drain_background_tasks()


@pytest.mark.asyncio
async def test_permanent_boundary_audit_failure_is_bounded_and_blocks_science(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads: list[ActivityLifecyclePayload] = []
    scientific_calls = 0

    async def failed_observer_write(
        *, payload: ActivityLifecyclePayload, topology_payload: dict | None
    ) -> bool:
        del topology_payload
        payloads.append(payload)
        return False

    async def no_delay(_: float) -> None:
        return None

    class ScientificActivity:
        async def execute_activity(self, input: ExecuteActivityInput) -> None:
            nonlocal scientific_calls
            del input
            scientific_calls += 1

    monkeypatch.setattr(observer, "_persist_event", failed_observer_write)
    monkeypatch.setattr(observer.asyncio, "sleep", no_delay)
    monkeypatch.setattr(observer, "OBSERVER_BOUNDARY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(
        observer.activity,
        "info",
        lambda: SimpleNamespace(
            activity_id="activity-permanent-failure",
            activity_type="mark_run_started",
            attempt=4,
            task_queue="pepagent-autoresearch-control-v1",
            workflow_id="workflow-1",
            workflow_run_id="workflow-run-1",
        ),
    )
    inbound = observer._ObserverInbound(ScientificActivity(), "autoresearch-control")
    input = ExecuteActivityInput(
        fn=lambda: None,
        args=({"run_id": str(uuid4()), "branch_key": "GyrA"},),
        executor=None,
        headers={},
    )

    with pytest.raises(
        observer.ActivityAuditPersistenceError,
        match=(
            r"mark_run_started/activity-permanent-failure/attempt-4/started"
        ),
    ):
        await inbound.execute_activity(input)

    assert scientific_calls == 0
    assert len(payloads) == 3
    assert {payload.status for payload in payloads} == {"started"}
    assert {payload.attempt for payload in payloads} == {4}


@pytest.mark.asyncio
async def test_succeeded_science_cannot_silently_lose_its_terminal_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter((True, False, False, False))
    statuses: list[str] = []
    scientific_calls = 0

    async def terminal_audit_outage(
        *, payload: ActivityLifecyclePayload, topology_payload: dict | None
    ) -> bool:
        del topology_payload
        statuses.append(payload.status)
        return next(outcomes)

    async def no_delay(_: float) -> None:
        return None

    class ScientificActivity:
        async def execute_activity(self, input: ExecuteActivityInput) -> str:
            nonlocal scientific_calls
            del input
            scientific_calls += 1
            return "completed"

    monkeypatch.setattr(observer, "_persist_event", terminal_audit_outage)
    monkeypatch.setattr(observer.asyncio, "sleep", no_delay)
    monkeypatch.setattr(observer, "OBSERVER_BOUNDARY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(
        observer.activity,
        "info",
        lambda: SimpleNamespace(
            activity_id="activity-terminal-audit",
            activity_type="mark_run_started",
            attempt=1,
            task_queue="pepagent-autoresearch-control-v1",
            workflow_id="workflow-1",
            workflow_run_id="workflow-run-1",
        ),
    )
    inbound = observer._ObserverInbound(ScientificActivity(), "autoresearch-control")
    input = ExecuteActivityInput(
        fn=lambda: None,
        args=({"run_id": str(uuid4()), "branch_key": "AceA"},),
        executor=None,
        headers={},
    )

    with pytest.raises(
        observer.ActivityAuditPersistenceError,
        match=r"mark_run_started/activity-terminal-audit/attempt-1/succeeded",
    ):
        await inbound.execute_activity(input)

    assert scientific_calls == 1
    assert statuses == ["started", "succeeded", "succeeded", "succeeded"]


@pytest.mark.asyncio
async def test_boundary_event_retries_until_postgres_accepts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = iter((False, False, True))
    attempts = 0

    async def flaky_persist(**_: object) -> bool:
        nonlocal attempts
        attempts += 1
        return next(outcomes)

    async def no_delay(_: float) -> None:
        return None

    monkeypatch.setattr(observer, "_persist_event", flaky_persist)
    monkeypatch.setattr(observer.asyncio, "sleep", no_delay)

    await observer._persist_boundary_event_until_durable(
        payload=_payload(status="succeeded"),
        topology_payload=None,
    )

    assert attempts == 3
