from __future__ import annotations

import asyncio
import os
import platform

from temporalio.client import Client
from temporalio.worker import Worker

from pepagent.settings import get_settings
from pepagent.workers.activities import (
    mark_run_cancelled,
    mark_run_failed,
    mark_run_started,
    mark_run_succeeded,
    persist_seven_branch_target_sequence,
    score_seven_branch_target_sequence,
)
from pepagent.workers.autoresearch_activities import (
    execute_autoresearch_action_batch,
    finalize_autoresearch_iteration,
    persist_autoresearch_action_plan,
    persist_autoresearch_children,
    plan_autoresearch_actions,
)
from pepagent.workers.v37_activities import (
    evaluate_v38_sequence_metric,
    generate_v38_sequence_cell,
)
from pepagent.workers.v38_activities import (
    evaluate_v38_sequence_admission,
    load_seven_branch_target_score_cohort,
    persist_seven_branch_cumulative_selection,
    persist_seven_branch_round_progress,
    persist_v38_external_activity_lifecycle,
    persist_v38_final_portfolio_replay,
    persist_v38_multitarget_boltz,
    persist_v38_multitarget_rosetta,
    persist_v38_refinement_children,
    persist_v38_score_all_generation,
    persist_v38_sequence_admission,
    persist_v38_sequence_metric,
    persist_v39_cross_round_admission,
    persist_v39_exploration_controller_action,
    persist_v39_exploration_round_yield,
    plan_v38_multitarget_structure,
    predict_v38_multitarget_structure,
    score_v38_multitarget_rosetta,
)
from pepagent.workers.v38_observer_interceptor import V38WorkflowObserverInterceptor
from pepagent.workflows.autoresearch import AutoResearchClosedLoopWorkflow
from pepagent.workflows.seven_branch_design import SevenBranchPeptideDesignWorkflow
from pepagent.workflows.seven_branch_top_up import SevenBranchPeptideTopUpWorkflow
from pepagent.workflows.v38_sequence_first import V38SequenceFirstAgentWorkflow
from pepagent.workflows.v39_sequence_space import V39SequenceSpaceExplorationWorkflow

V38_ROLE_CONFIG = {
    "v38-control": (
        "pepagent-control-v38",
        [
            mark_run_started,
            mark_run_cancelled,
            mark_run_failed,
            mark_run_succeeded,
            plan_v38_multitarget_structure,
            evaluate_v38_sequence_admission,
            persist_v38_external_activity_lifecycle,
            persist_v38_refinement_children,
            persist_v38_score_all_generation,
            persist_v38_sequence_admission,
            persist_v38_sequence_metric,
            persist_v38_multitarget_boltz,
            persist_v38_multitarget_rosetta,
            persist_v38_final_portfolio_replay,
            persist_v39_exploration_round_yield,
            persist_v39_exploration_controller_action,
            persist_v39_cross_round_admission,
            persist_seven_branch_target_sequence,
            load_seven_branch_target_score_cohort,
            persist_seven_branch_cumulative_selection,
            persist_seven_branch_round_progress,
            persist_autoresearch_action_plan,
            plan_autoresearch_actions,
            persist_autoresearch_children,
            finalize_autoresearch_iteration,
        ],
        [
            V38SequenceFirstAgentWorkflow,
            V39SequenceSpaceExplorationWorkflow,
            SevenBranchPeptideDesignWorkflow,
            SevenBranchPeptideTopUpWorkflow,
            AutoResearchClosedLoopWorkflow,
        ],
    ),
    "v38-generator": (
        "pepagent-generator-v38",
        [generate_v38_sequence_cell, execute_autoresearch_action_batch],
        [],
    ),
    "v38-metrics": (
        "pepagent-cpu-metrics-v38",
        [evaluate_v38_sequence_metric],
        [],
    ),
    "v39-target-sequence": (
        "pepagent-gpu-target-sequence-v39",
        [score_seven_branch_target_sequence],
        [],
    ),
    "v38-boltz": (
        "pepagent-gpu-boltz2-v38",
        [predict_v38_multitarget_structure],
        [],
    ),
    "v38-rosetta": (
        "pepagent-cpu-rosetta-v38",
        [score_v38_multitarget_rosetta],
        [],
    ),
}


async def run_worker() -> None:
    settings = get_settings()
    if settings.worker_role not in V38_ROLE_CONFIG:
        raise ValueError(f"unknown v38 worker role: {settings.worker_role}")
    task_queue, activities, workflows = V38_ROLE_CONFIG[settings.worker_role]
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
        interceptors=[V38WorkflowObserverInterceptor(settings.worker_role)],
        identity=identity,
        build_id=settings.worker_source_revision,
    )
    await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
