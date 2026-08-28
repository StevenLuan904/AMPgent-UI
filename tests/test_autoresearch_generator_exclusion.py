from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from pepagent.workers import autoresearch_activities
from pepagent.workers.v38_temporal_worker import _max_concurrent_activities_for_role


def test_autoresearch_generator_worker_forces_one_activity_slot() -> None:
    assert _max_concurrent_activities_for_role("autoresearch-generator", 16) == 1
    assert _max_concurrent_activities_for_role("autoresearch-control", 16) == 16
    with pytest.raises(ValueError, match="concurrency must be positive"):
        _max_concurrent_activities_for_role("autoresearch-generator", 0)


@pytest.mark.asyncio
async def test_autoresearch_generator_batches_are_serialized(monkeypatch, tmp_path) -> None:
    entered: list[str] = []
    active = 0
    maximum_active = 0
    first_entered = asyncio.Event()
    release_first = asyncio.Event()

    async def fake_unlocked(request):
        nonlocal active, maximum_active
        run_id = request["action_plan"]["run_id"]
        entered.append(run_id)
        active += 1
        maximum_active = max(maximum_active, active)
        if run_id == "first":
            first_entered.set()
            await release_first.wait()
        active -= 1
        return {"run_id": run_id}

    monkeypatch.setattr(
        autoresearch_activities,
        "_AUTORESEARCH_GENERATOR_SEMAPHORE",
        asyncio.Semaphore(1),
    )
    monkeypatch.setattr(
        autoresearch_activities,
        "_execute_autoresearch_action_batch_unlocked",
        fake_unlocked,
    )
    monkeypatch.setattr(
        autoresearch_activities,
        "get_settings",
        lambda: SimpleNamespace(work_root=str(tmp_path)),
    )

    first = asyncio.create_task(
        autoresearch_activities.execute_autoresearch_action_batch(
            {"action_plan": {"run_id": "first", "iteration_no": 0}}
        )
    )
    await asyncio.wait_for(first_entered.wait(), timeout=2)
    second = asyncio.create_task(
        autoresearch_activities.execute_autoresearch_action_batch(
            {"action_plan": {"run_id": "second", "iteration_no": 0}}
        )
    )
    await asyncio.sleep(0.05)
    assert entered == ["first"]
    assert maximum_active == 1

    release_first.set()
    assert await first == {"run_id": "first"}
    assert await second == {"run_id": "second"}
    assert entered == ["first", "second"]
    assert maximum_active == 1
