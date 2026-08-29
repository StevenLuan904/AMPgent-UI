from __future__ import annotations

import asyncio
import os
import platform
from dataclasses import dataclass
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker

from pepagent.settings import get_settings
from pepagent.workers.structure_v2_activities import (
    persist_rosetta_evidence_v2,
    preflight_structure_v2_target_request_v2,
    score_rosetta_complex_v2,
    structure_v2_shared_rosetta_root,
)
from pepagent.workflows.structure_v2 import (
    STRUCTURE_V2_PERSIST_QUEUE,
    STRUCTURE_V2_ROSETTA_QUEUE,
    STRUCTURE_V2_WORKFLOW_QUEUE,
    BulkCandidateEvaluationWorkflowV2,
    CandidateStructureValidationWorkflowV2,
    StructureEvidenceRepairWorkflowV2,
)


@dataclass(frozen=True)
class StructureV2WorkerRole:
    task_queue: str
    activities: tuple[Any, ...]
    workflows: tuple[type, ...]


ROLE_CONFIG = {
    "structure_v2_workflow": StructureV2WorkerRole(
        task_queue=STRUCTURE_V2_WORKFLOW_QUEUE,
        activities=(),
        workflows=(
            BulkCandidateEvaluationWorkflowV2,
            CandidateStructureValidationWorkflowV2,
            StructureEvidenceRepairWorkflowV2,
        ),
    ),
    "structure_v2_rosetta": StructureV2WorkerRole(
        task_queue=STRUCTURE_V2_ROSETTA_QUEUE,
        activities=(score_rosetta_complex_v2,),
        workflows=(),
    ),
    "structure_v2_persist": StructureV2WorkerRole(
        task_queue=STRUCTURE_V2_PERSIST_QUEUE,
        activities=(
            persist_rosetta_evidence_v2,
            preflight_structure_v2_target_request_v2,
        ),
        workflows=(),
    ),
}


def worker_options(role_name: str) -> dict[str, Any]:
    try:
        role = ROLE_CONFIG[role_name]
    except KeyError as error:
        raise ValueError(f"unknown structure v2 worker role: {role_name}") from error
    return {
        "task_queue": role.task_queue,
        "activities": list(role.activities),
        "workflows": list(role.workflows),
        "max_concurrent_activities": 1,
        "max_concurrent_workflow_tasks": 1,
        "max_concurrent_activity_task_polls": 1,
        "max_concurrent_workflow_task_polls": 1,
        # Temporal SDK requires at least two workflow-task pollers when sticky
        # workflow caching is enabled.  These deliberately serialized v2
        # roles use one poller, so disable the cache explicitly.
        "max_cached_workflows": 0,
        "disable_eager_activity_execution": True,
    }


async def run_worker() -> None:
    settings = get_settings()
    options = worker_options(settings.worker_role)
    if settings.worker_role == "structure_v2_rosetta":
        structure_v2_shared_rosetta_root()
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    identity = (
        f"pepagent:{settings.worker_role}:{os.getpid()}@{platform.node()}:"
        f"{settings.worker_source_revision}"
    )
    worker = Worker(
        client,
        **options,
        identity=identity,
        build_id=settings.worker_source_revision,
    )
    await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()


__all__ = ["ROLE_CONFIG", "StructureV2WorkerRole", "main", "worker_options"]
