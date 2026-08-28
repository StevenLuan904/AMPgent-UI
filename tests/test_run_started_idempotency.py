from __future__ import annotations

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from pepagent.db.models import ExperimentRun, LifecycleEvent
from pepagent.db.repository import ExperimentRepository
from pepagent.domain.enums import RunStatus


def _running_reserved_run() -> ExperimentRun:
    return ExperimentRun(
        id=uuid4(),
        target_id=uuid4(),
        spec_json={},
        spec_sha256="a" * 64,
        status=RunStatus.RUNNING,
        temporal_workflow_id="workflow-1",
        temporal_run_id="temporal-run-1",
        started_at=None,
    )


@pytest.mark.asyncio
async def test_mark_run_started_repairs_reserved_running_run_exactly_once() -> None:
    run = _running_reserved_run()
    session = Mock()
    session.get = AsyncMock(return_value=run)
    # First scalar checks for an existing run.started event; the second
    # allocates the lifecycle sequence number in append_event.
    session.scalar = AsyncMock(side_effect=[None, None])
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = Mock()
    repository = ExperimentRepository(session)

    await repository.mark_run_started(
        run.id,
        "workflow-1",
        "temporal-run-1",
    )
    first_started_at = run.started_at
    await repository.mark_run_started(
        run.id,
        "workflow-1",
        "temporal-run-1",
    )

    assert first_started_at is not None
    assert run.started_at == first_started_at
    assert session.add.call_count == 1
    event = session.add.call_args.args[0]
    assert isinstance(event, LifecycleEvent)
    assert event.event_type == "run.started"
    assert event.aggregate_type == "run"
    assert event.aggregate_id == run.id


@pytest.mark.asyncio
async def test_mark_run_started_repairs_timestamp_without_duplicate_existing_event() -> None:
    run = _running_reserved_run()
    session = Mock()
    session.get = AsyncMock(return_value=run)
    session.scalar = AsyncMock(return_value=uuid4())
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = Mock()

    await ExperimentRepository(session).mark_run_started(
        run.id,
        "workflow-1",
        "temporal-run-1",
    )

    assert run.started_at is not None
    session.add.assert_not_called()
