from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from temporalio.worker import ExecuteActivityInput

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
async def test_started_and_succeeded_observer_writes_do_not_block_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    never = asyncio.Event()
    statuses: list[str] = []

    async def stalled_observer_write(
        *, payload: ActivityLifecyclePayload, topology_payload: dict | None
    ) -> None:
        del topology_payload
        statuses.append(payload.status)
        await never.wait()

    class ScientificActivity:
        async def execute_activity(self, input: ExecuteActivityInput) -> dict[str, bool]:
            del input
            return {"scientific_result": True}

    monkeypatch.setattr(observer, "_persist_event", stalled_observer_write)
    monkeypatch.setattr(
        observer.activity,
        "info",
        lambda: SimpleNamespace(
            activity_id="activity-1",
            activity_type="mark_run_started",
            attempt=1,
            task_queue="pepagent-autoresearch-control-v1",
        ),
    )
    inbound = observer._ObserverInbound(ScientificActivity(), "autoresearch-control")
    input = ExecuteActivityInput(
        fn=lambda: None,
        args=({"run_id": str(uuid4())},),
        executor=None,
        headers={},
    )

    result = await asyncio.wait_for(inbound.execute_activity(input), timeout=0.1)
    await asyncio.sleep(0)

    assert result == {"scientific_result": True}
    assert sorted(statuses) == ["started", "succeeded"]
    await _drain_background_tasks()


@pytest.mark.asyncio
async def test_progress_and_failed_observer_writes_do_not_block_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    never = asyncio.Event()
    statuses: list[str] = []

    async def stalled_observer_write(
        *, payload: ActivityLifecyclePayload, topology_payload: dict | None
    ) -> None:
        del topology_payload
        statuses.append(payload.status)
        await never.wait()

    class Outbound:
        def heartbeat(self, *details: object) -> None:
            del details

    class FailingScientificActivity:
        async def execute_activity(self, input: ExecuteActivityInput) -> None:
            del input
            outbound.heartbeat({"completed": 1, "expected": 1})
            raise RuntimeError("scientific failure")

    monkeypatch.setattr(observer, "_persist_event", stalled_observer_write)
    monkeypatch.setattr(
        observer.activity,
        "info",
        lambda: SimpleNamespace(
            activity_id="activity-2",
            activity_type="mark_run_started",
            attempt=1,
            task_queue="pepagent-autoresearch-control-v1",
        ),
    )
    inbound = observer._ObserverInbound(FailingScientificActivity(), "autoresearch-control")
    outbound = observer._ObserverOutbound(Outbound(), inbound._state)
    input = ExecuteActivityInput(
        fn=lambda: None,
        args=({"run_id": str(uuid4())},),
        executor=None,
        headers={},
    )

    with pytest.raises(RuntimeError, match="scientific failure"):
        await asyncio.wait_for(inbound.execute_activity(input), timeout=0.1)
    await asyncio.sleep(0)

    assert sorted(statuses) == ["failed", "progress", "started"]
    await _drain_background_tasks()
