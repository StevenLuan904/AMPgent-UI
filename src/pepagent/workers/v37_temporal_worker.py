from __future__ import annotations

import asyncio
import os
import platform

from temporalio.client import Client
from temporalio.worker import Worker

from pepagent.settings import get_settings
from pepagent.workers.activities import (
    audit_structure_ensemble,
    mark_run_failed,
    mark_run_started,
    persist_boltz2_evidence,
    persist_interface_audit,
    persist_rosetta_evidence,
)
from pepagent.workers.v37_activities import (
    finalize_v37_run,
    generate_v37_batch,
    generate_v38_sequence_cell,
    persist_v37_final_portfolio_and_replay,
    persist_v37_generation_batch,
    persist_v37_knowledge_projection,
    persist_v37_sequence_metric,
    persist_v37_stage1_shortlist,
    persist_v37_structure_stage_summaries,
    run_and_persist_v37_knowledge,
    run_and_persist_v37_pepshot,
)
from pepagent.workers.v38_activities import (
    evaluate_v38_sequence_admission,
    persist_v38_score_all_generation,
    persist_v38_sequence_admission,
    persist_v38_sequence_metric,
)
from pepagent.workflows.v37_champion import RapidChampionGenerationV37Workflow

V37_ROLE_CONFIG = {
    "v37-control": (
        "pepagent-control-v37",
        [
            mark_run_started,
            mark_run_failed,
            evaluate_v38_sequence_admission,
            persist_v38_score_all_generation,
            persist_v38_sequence_admission,
            persist_v38_sequence_metric,
            persist_v37_generation_batch,
            persist_v37_sequence_metric,
            persist_v37_knowledge_projection,
            persist_v37_stage1_shortlist,
            persist_v37_structure_stage_summaries,
            persist_boltz2_evidence,
            audit_structure_ensemble,
            persist_interface_audit,
            persist_rosetta_evidence,
            persist_v37_final_portfolio_and_replay,
            finalize_v37_run,
        ],
        [RapidChampionGenerationV37Workflow],
    ),
    "v37-generator": (
        "pepagent-generator-v37",
        [generate_v37_batch, generate_v38_sequence_cell],
        [],
    ),
    "v37-provider": (
        "pepagent-provider-v37",
        [run_and_persist_v37_knowledge, run_and_persist_v37_pepshot],
        [],
    ),
}


async def run_worker() -> None:
    settings = get_settings()
    if settings.worker_role not in V37_ROLE_CONFIG:
        raise ValueError(f"unknown v37 worker role: {settings.worker_role}")
    task_queue, activities, workflows = V37_ROLE_CONFIG[settings.worker_role]
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    identity = (
        f"pepagent:{settings.worker_role}:{os.getpid()}@{platform.node()}:"
        f"{settings.worker_source_revision}"
    )
    worker = Worker(
        client,
        task_queue=task_queue,
        activities=activities,
        workflows=workflows,
        max_concurrent_activities=settings.worker_max_concurrent_activities,
        identity=identity,
        build_id=settings.worker_source_revision,
    )
    await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
