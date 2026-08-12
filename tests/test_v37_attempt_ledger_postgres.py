from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import pepagent.v37_attempt_ledger as attempt_ledger
from pepagent.db.models import LifecycleEvent
from pepagent.v37_attempt_ledger import V37AttemptContext


@pytest.mark.asyncio
async def test_postgres_attempt_lineage_lock_fences_retry_against_late_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.getenv("PEPAGENT_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("set PEPAGENT_TEST_POSTGRES_URL for PostgreSQL concurrency coverage")
    schema = f"test_v37_attempt_lock_{uuid.uuid4().hex}"
    engine = create_async_engine(database_url, pool_size=2, max_overflow=0)
    mapped_engine = engine.execution_options(schema_translate_map={None: schema})
    sessions = async_sessionmaker(mapped_engine, expire_on_commit=False)
    monkeypatch.setattr(attempt_ledger, "SessionFactory", sessions)
    run_id = uuid.uuid4()
    first = V37AttemptContext(run_id, "v37:generate:test:1", "generate", 1)
    second = V37AttemptContext(run_id, "v37:generate:test:1", "generate", 2)

    def identity(context: V37AttemptContext) -> dict[str, object]:
        return {
            "schema_version": "v37.attempt-event.1",
            "run_id": str(context.run_id),
            "v37_logical_id": context.logical_id,
            "activity_name": context.activity_name,
            "attempt": context.attempt,
        }

    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with mapped_engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: LifecycleEvent.__table__.create(sync_connection)
            )

        await attempt_ledger._begin_database_attempt(first, identity(first))
        outcomes = await asyncio.gather(
            attempt_ledger._begin_database_attempt(second, identity(second)),
            attempt_ledger._persist_attempt_event(
                first,
                "v37.attempt_succeeded",
                {
                    **identity(first),
                    "status": "succeeded",
                    "output_sha256": "1" * 64,
                },
            ),
            return_exceptions=True,
        )
        assert sum(item is None for item in outcomes) == 1
        failures = [item for item in outcomes if isinstance(item, Exception)]
        assert len(failures) == 1
        assert isinstance(failures[0], (RuntimeError, ValueError))

        async with sessions() as session:
            rows = list(
                await session.scalars(
                    select(LifecycleEvent).order_by(
                        LifecycleEvent.occurred_at, LifecycleEvent.id
                    )
                )
            )
        event_types = [row.event_type for row in rows]
        assert not (
            "v37.attempt_succeeded" in event_types
            and "v37.attempt_interrupted" in event_types
        )
        if "v37.attempt_succeeded" in event_types:
            assert "v37.attempt_started" in event_types
            assert len(rows) == 2
        else:
            assert event_types.count("v37.attempt_started") == 2
            assert event_types.count("v37.attempt_interrupted") == 1
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await mapped_engine.dispose()
        await engine.dispose()
