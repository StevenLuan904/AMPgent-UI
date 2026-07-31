import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from pepagent.settings import get_settings
from pepagent.workers.activities import (
    finalize_run,
    generate_with_pepmlm,
    mark_run_failed,
    mark_run_started,
    persist_and_select_candidates,
    persist_boltz2_evidence,
    persist_rosetta_evidence,
    predict_boltz2_complex,
    score_rosetta_complex,
    select_rosetta_inputs,
)
from pepagent.workflows.design import PeptideDesignWorkflow

ROLE_CONFIG = {
    "control": (
        "pepagent-control",
        [
            mark_run_started,
            mark_run_failed,
            persist_and_select_candidates,
            persist_boltz2_evidence,
            select_rosetta_inputs,
            persist_rosetta_evidence,
            finalize_run,
        ],
        [PeptideDesignWorkflow],
    ),
    "pepmlm": ("pepagent-gpu-pepmlm", [generate_with_pepmlm], []),
    "boltz2": ("pepagent-gpu-boltz2", [predict_boltz2_complex], []),
    "rosetta": ("pepagent-cpu-rosetta", [score_rosetta_complex], []),
}


async def run_worker() -> None:
    settings = get_settings()
    if settings.worker_role not in ROLE_CONFIG:
        raise ValueError(f"unknown worker role: {settings.worker_role}")
    task_queue, activities, workflows = ROLE_CONFIG[settings.worker_role]
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    worker = Worker(
        client,
        task_queue=task_queue,
        activities=activities,
        workflows=workflows,
        max_concurrent_activities=settings.worker_max_concurrent_activities,
    )
    await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
