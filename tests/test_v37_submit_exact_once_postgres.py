from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pepagent.db.models import ExperimentRun, LifecycleEvent, Target
from pepagent.domain.schemas import ExperimentSpec
from pepagent.v37_submit_cli import (
    _reserve_v37_formal_run,
    build_v37_formal_submission_key,
    build_v37_workflow_id,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
async def test_two_postgres_sessions_reserve_one_v37_formal_run() -> None:
    database_url = os.getenv("PEPAGENT_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("set PEPAGENT_TEST_POSTGRES_URL for PostgreSQL concurrency coverage")
    schema = f"test_v37_exact_once_{uuid.uuid4().hex}"
    engine = create_async_engine(database_url, pool_size=2, max_overflow=0)
    mapped_engine = engine.execution_options(schema_translate_map={None: schema})
    sessions = async_sessionmaker(mapped_engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with mapped_engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Target.__table__.create(sync_connection)
            )
            await connection.run_sync(
                lambda sync_connection: ExperimentRun.__table__.create(sync_connection)
            )
            await connection.run_sync(
                lambda sync_connection: LifecycleEvent.__table__.create(sync_connection)
            )

        spec = ExperimentSpec.model_validate(
            yaml.safe_load(
                (
                    ROOT / "config/experiments/acea_v37_rapid_champion_structure.yaml"
                ).read_text(encoding="utf-8")
            )
        )
        manifest_sha256 = "1" * 64
        formal_key = build_v37_formal_submission_key(
            benchmark_id="amp_rapid_champion_generation_v37",
            benchmark_version="v37.0.0-preregistered",
            manifest_sha256=manifest_sha256,
        )
        workflow_id = build_v37_workflow_id(formal_key)
        raw_spec = {
            **spec.model_dump(mode="json"),
            "benchmark_id": "amp_rapid_champion_generation_v37",
            "benchmark_version": "v37.0.0-preregistered",
            "manifest_sha256": manifest_sha256,
            "submission_preflight_sha256": "2" * 64,
            "execution_bundle_sha256": "3" * 64,
            "experiment_spec_sha256": "4" * 64,
            "formal_submission_key": formal_key,
        }

        async def reserve() -> uuid.UUID:
            async with sessions() as session, session.begin():
                run = await _reserve_v37_formal_run(
                    session,
                    spec=spec,
                    raw_spec=raw_spec,
                    formal_submission_key=formal_key,
                    workflow_id=workflow_id,
                )
                return run.id

        first, second = await asyncio.gather(reserve(), reserve())
        assert first == second
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(ExperimentRun)) == 1
            assert await session.scalar(select(func.count()).select_from(LifecycleEvent)) == 2
            run = await session.scalar(select(ExperimentRun))
            assert run is not None
            assert run.formal_submission_key == formal_key
            assert run.temporal_workflow_id == workflow_id
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await mapped_engine.dispose()
        await engine.dispose()
