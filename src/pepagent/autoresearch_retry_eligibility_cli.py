from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy import select
from temporalio.client import Client

from pepagent.autoresearch_retry_eligibility import (
    RetryEligibilityObservation,
    decide_retry_eligibility,
)
from pepagent.db.models import ExperimentRun
from pepagent.db.session import ObserverSessionFactory
from pepagent.settings import get_settings


async def inspect_retry_eligibility(
    *,
    predecessor_run_id: UUID,
    source_revision: str,
    execution_policy: str = "freeze_only",
    new_gpu_tasks_allowed: bool = False,
) -> dict[str, object]:
    """Read PostgreSQL and the exact Temporal handle without mutating either system."""

    async with ObserverSessionFactory() as session:
        predecessor = await session.get(ExperimentRun, predecessor_run_id)
        if predecessor is None:
            raise ValueError("AutoResearch retry predecessor does not exist")
        if not predecessor.temporal_workflow_id or not predecessor.temporal_run_id:
            raise ValueError("AutoResearch retry predecessor lacks a Temporal binding")
        successors = list(
            await session.scalars(
                select(ExperimentRun)
                .where(ExperimentRun.parent_run_id == predecessor.id)
                .order_by(ExperimentRun.created_at, ExperimentRun.id)
            )
        )
        target_key = str(
            predecessor.spec_json.get("branch_key")
            or predecessor.spec_json.get("target_key")
            or ""
        ).lower()
        if not target_key:
            raise ValueError("AutoResearch retry predecessor target is missing")
        database_status = str(predecessor.status)
        workflow_id = predecessor.temporal_workflow_id
        temporal_run_id = predecessor.temporal_run_id

    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    handle = client.get_workflow_handle(workflow_id, run_id=temporal_run_id)
    description = await handle.describe()
    observation = RetryEligibilityObservation(
        predecessor_run_id=str(predecessor_run_id),
        target_key=target_key,
        database_status=database_status,
        temporal_workflow_id=workflow_id,
        temporal_run_id=temporal_run_id,
        observed_temporal_workflow_id=description.id,
        observed_temporal_run_id=description.run_id,
        temporal_status=description.status.name,
        successor_run_ids=tuple(str(item.id) for item in successors),
        source_revision=source_revision,
        execution_policy=execution_policy,
        new_gpu_tasks_allowed=new_gpu_tasks_allowed,
    )
    decision = decide_retry_eligibility(observation)
    return {
        **decision.model_dump(mode="json"),
        "eligibility_sha256": decision.eligibility_sha256,
        "read_only": True,
        "workflow_submitted": False,
        "run_reserved": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect exact-once AutoResearch successor eligibility without submitting"
    )
    parser.add_argument("--predecessor-run-id", required=True, type=UUID)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument(
        "--execution-policy",
        choices=("freeze_only", "submit_allowed"),
        default="freeze_only",
    )
    parser.add_argument("--new-gpu-tasks-allowed", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = asyncio.run(
        inspect_retry_eligibility(
            predecessor_run_id=args.predecessor_run_id,
            source_revision=args.source_revision,
            execution_policy=args.execution_policy,
            new_gpu_tasks_allowed=args.new_gpu_tasks_allowed,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
