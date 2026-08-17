import asyncio
import os
import platform

from temporalio.client import Client
from temporalio.worker import Worker

from pepagent.settings import get_settings
from pepagent.workers.activities import (
    audit_structure_ensemble,
    evaluate_optional_sequence_metric,
    export_bulk_rosetta_csv,
    finalize_run,
    generate_with_pepmlm,
    mark_run_cancelled,
    mark_run_failed,
    mark_run_started,
    persist_and_select_candidates,
    persist_boltz2_evidence,
    persist_bulk_evaluation_failure,
    persist_interface_audit,
    persist_optional_metric_failure,
    persist_optional_sequence_metric,
    persist_rosetta_evidence,
    persist_structure_unavailable,
    persist_target_specific_pepmlm_proxy,
    predict_boltz2_complex,
    score_rosetta_complex,
    score_target_specific_pepmlm_proxy,
    select_bulk_evaluation_candidates,
    select_next_generation,
    select_rosetta_inputs,
)
from pepagent.workers.portfolio_activities import (
    generate_amp_designer_v32,
    persist_v32_generation_batch,
    persist_v32_portfolio_decision,
    persist_v32_replay_bundle,
)
from pepagent.workers.v37_activities import (
    evaluate_v37_sequence_metric,
    evaluate_v38_sequence_metric,
    predict_v37_boltz2_complex,
    score_v37_rosetta_complex,
)
from pepagent.workflows.design import (
    BulkCandidateEvaluationWorkflow,
    CandidateStructureValidationWorkflow,
    PeptideDesignWorkflow,
    RosettaValidationWorkflow,
    SequenceBindingProxyCalibrationWorkflow,
)
from pepagent.workflows.portfolio import MultiobjectivePortfolioWorkflow

ROLE_CONFIG = {
    "control": (
        "pepagent-control",
        [
            mark_run_started,
            mark_run_failed,
            mark_run_cancelled,
            persist_and_select_candidates,
            persist_optional_sequence_metric,
            persist_optional_metric_failure,
            persist_boltz2_evidence,
            audit_structure_ensemble,
            persist_interface_audit,
            persist_structure_unavailable,
            persist_bulk_evaluation_failure,
            select_bulk_evaluation_candidates,
            export_bulk_rosetta_csv,
            select_rosetta_inputs,
            persist_rosetta_evidence,
            persist_target_specific_pepmlm_proxy,
            select_next_generation,
            finalize_run,
            persist_v32_generation_batch,
            persist_v32_portfolio_decision,
            persist_v32_replay_bundle,
        ],
        [
            PeptideDesignWorkflow,
            BulkCandidateEvaluationWorkflow,
            CandidateStructureValidationWorkflow,
            RosettaValidationWorkflow,
            SequenceBindingProxyCalibrationWorkflow,
            MultiobjectivePortfolioWorkflow,
        ],
    ),
    "pepmlm": (
        "pepagent-gpu-pepmlm",
        [generate_with_pepmlm, score_target_specific_pepmlm_proxy],
        [],
    ),
    "boltz2": (
        "pepagent-gpu-boltz2",
        [predict_boltz2_complex, predict_v37_boltz2_complex],
        [],
    ),
    "rosetta": (
        "pepagent-cpu-rosetta",
        [score_rosetta_complex, score_v37_rosetta_complex],
        [],
    ),
    "metrics": (
        "pepagent-cpu-metrics",
        [
            evaluate_optional_sequence_metric,
            evaluate_v37_sequence_metric,
            evaluate_v38_sequence_metric,
        ],
        [],
    ),
    "portfolio": ("pepagent-cpu-portfolio", [generate_amp_designer_v32], []),
}


async def run_worker() -> None:
    settings = get_settings()
    if settings.worker_role not in ROLE_CONFIG:
        raise ValueError(f"unknown worker role: {settings.worker_role}")
    task_queue, activities, workflows = ROLE_CONFIG[settings.worker_role]
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
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
