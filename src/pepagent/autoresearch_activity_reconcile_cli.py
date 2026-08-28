from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from google.protobuf.json_format import MessageToDict
from sqlalchemy import select
from temporalio.client import Client

from pepagent.autoresearch_activity_reconcile import (
    extract_temporal_activity_boundaries,
    reconcile_temporal_activity_boundaries,
)
from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    persist_operational_call,
)
from pepagent.db.models import ExperimentRun
from pepagent.db.session import ObserverSessionFactory
from pepagent.settings import get_settings

DATABASE_WRITE_TIMEOUT_SECONDS = 10.0


def _load_run_ids(config_path: Path) -> tuple[UUID, ...]:
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    branches = config.get("branches") if isinstance(config, dict) else None
    if not isinstance(branches, list) or not branches:
        raise ValueError("AutoResearch reconciliation config has no branches")
    run_ids = tuple(UUID(str(item["run_id"])) for item in branches)
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("AutoResearch reconciliation run IDs are duplicated")
    return run_ids


async def _load_binding(run_id: UUID) -> dict[str, str]:
    async with ObserverSessionFactory() as session:
        run = await session.scalar(select(ExperimentRun).where(ExperimentRun.id == run_id))
        if run is None:
            raise ValueError(f"AutoResearch reconciliation run is missing: {run_id}")
        if not run.temporal_workflow_id or not run.temporal_run_id:
            raise ValueError(f"AutoResearch run has no Temporal binding: {run_id}")
        return {
            "run_id": str(run.id),
            "workflow_id": run.temporal_workflow_id,
            "temporal_run_id": run.temporal_run_id,
            "target_key": str(
                run.spec_json.get("branch_key") or run.spec_json.get("target_key") or ""
            ).lower(),
        }


async def _history(client: Client, binding: dict[str, str]) -> tuple[str, list[dict[str, Any]]]:
    handle = client.get_workflow_handle(
        binding["workflow_id"], run_id=binding["temporal_run_id"]
    )
    description = await handle.describe()
    if description.run_id != binding["temporal_run_id"]:
        raise ValueError("Temporal activity reconciliation run identity drifted")
    events = [
        MessageToDict(
            event,
            preserving_proto_field_name=True,
            use_integers_for_enums=False,
        )
        async for event in handle.fetch_history_events()
    ]
    return description.status.name, events


async def _reconcile_one(
    *,
    client: Client,
    run_id: UUID,
    execute: bool,
    invocation_key: str,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    binding = await _load_binding(run_id)
    if not binding["target_key"]:
        raise ValueError("AutoResearch reconciliation target key is missing")
    temporal_status, history = await _history(client, binding)
    boundaries = extract_temporal_activity_boundaries(history)

    async def persist() -> tuple[Any, dict[str, str] | None]:
        async with ObserverSessionFactory() as session, session.begin():
            reconciliation = await reconcile_temporal_activity_boundaries(
                session,
                run_id=run_id,
                workflow_id=binding["workflow_id"],
                temporal_run_id=binding["temporal_run_id"],
                temporal_status=temporal_status,
                boundaries=boundaries,
                execute=execute,
            )
            if not execute:
                return reconciliation, None
            record = OperationalCallRecord(
                operation_key=f"{invocation_key}:{binding['target_key']}",
                target_key=binding["target_key"],
                purpose="audit_reconciliation",
                tool_name="temporal-activity-pg-reconciler",
                tool_version="1",
                status="succeeded",
                input_payload={
                    "formal_run_id": str(run_id),
                    "workflow_id": binding["workflow_id"],
                    "temporal_run_id": binding["temporal_run_id"],
                    "temporal_status": temporal_status,
                    "history_event_count": len(history),
                },
                parameters={"write_mode": "append_only_missing_lifecycle_events"},
                execution_context={
                    "host": platform.node(),
                    "process_id": os.getpid(),
                    "interface": "pepagent.autoresearch_activity_reconcile_cli",
                },
                output_payload={
                    "extracted_boundary_count": reconciliation.extracted_count,
                    "appended_count": len(reconciliation.appended_event_ids),
                    "appended_event_ids": [
                        str(item) for item in reconciliation.appended_event_ids
                    ],
                    "skipped_existing_count": len(
                        reconciliation.skipped_semantic_keys
                    ),
                },
                started_at=started_at,
                finished_at=datetime.now(UTC),
                actor="temporal-activity-history-reconciler",
            )
            operational_run, call = await persist_operational_call(session, record)
            return reconciliation, {
                "operational_run_id": str(operational_run.id),
                "tool_call_id": str(call.id),
                "operation_key": record.operation_key,
            }

    reconciliation, operational_call = await asyncio.wait_for(
        persist(), timeout=DATABASE_WRITE_TIMEOUT_SECONDS
    )
    return {
        "run_id": str(run_id),
        "workflow_id": binding["workflow_id"],
        "temporal_run_id": binding["temporal_run_id"],
        "temporal_status": temporal_status,
        "history_event_count": len(history),
        "extracted_boundary_count": reconciliation.extracted_count,
        "appended_event_ids": [str(item) for item in reconciliation.appended_event_ids],
        "appended_count": len(reconciliation.appended_event_ids),
        "skipped_existing_count": len(reconciliation.skipped_semantic_keys),
        "missing_count": len(reconciliation.missing_semantic_keys),
        "missing_semantic_keys": [
            [activity_id, attempt, status]
            for activity_id, attempt, status in reconciliation.missing_semantic_keys
        ],
        "operational_call": operational_call,
    }


async def _execute(args: argparse.Namespace) -> dict[str, Any]:
    run_ids = _load_run_ids(args.config.resolve())
    invocation_key = args.operation_key or (
        "temporal-activity-pg-reconcile:"
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}:{uuid.uuid4().hex}"
    )
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    results = []
    for run_id in run_ids:
        results.append(
            await _reconcile_one(
                client=client,
                run_id=run_id,
                execute=args.execute,
                invocation_key=invocation_key,
            )
        )
    return {
        "schema_version": "ampgent.temporal-activity-pg-reconciliation.1",
        "executed": bool(args.execute),
        "inert": not args.execute,
        "invocation_key": invocation_key,
        "run_count": len(results),
        "appended_count": sum(int(item["appended_count"]) for item in results),
        "runs": results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile immutable Temporal activity attempt boundaries into "
            "append-only PostgreSQL lifecycle events"
        )
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--operation-key",
        help="stable invocation key for exact retry; generated when omitted",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="append only missing PostgreSQL activity lifecycle events",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    result = asyncio.run(_execute(_parser().parse_args(argv)))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
