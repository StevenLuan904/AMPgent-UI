from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pepagent.seven_branch_design import (
        BranchProgress,
        SevenBranchDesignSchedule,
        SevenBranchRoundBinding,
        next_controller_branch,
    )


@workflow.defn(name="SevenBranchPeptideDesignWorkflow")
class SevenBranchPeptideDesignWorkflow:
    """Run the initial seven independent generation/score-all/target-score branches."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        schedule = SevenBranchDesignSchedule.model_validate(request)
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(minutes=10),
            maximum_attempts=2,
            non_retryable_error_types=["ValueError", "KeyError", "TypeError"],
        )
        first_request = schedule.rounds[0].request
        control_queue = str(first_request["task_queues"]["workflow_and_control"])
        await workflow.execute_activity(
            "mark_run_started",
            {
                "run_id": str(schedule.controller_run_id),
                "workflow_id": workflow.info().workflow_id,
            },
            task_queue=control_queue,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=retry,
        )
        receipts: list[dict[str, Any]] = []
        progress_by_branch: dict[str, BranchProgress] = {}
        current_round_run_id: str | None = None
        branch_by_key = {
            item.branch_key: item for item in schedule.design_contract.branches
        }
        try:
            for observation_no, frozen_round in enumerate(schedule.rounds):
                current_round_run_id = str(frozen_round.run_id)
                child_request = frozen_round.request
                binding = SevenBranchRoundBinding.model_validate(
                    child_request["seven_branch_round"]
                )
                child_control_queue = str(
                    child_request["task_queues"]["workflow_and_control"]
                )
                child_result = await workflow.execute_child_workflow(
                    "V38SequenceFirstAgentWorkflow",
                    child_request,
                    id=frozen_round.workflow_id,
                    task_queue=child_control_queue,
                )
                target_score_receipt = None
                if binding.branch_kind == "target_specific":
                    cohort = await workflow.execute_activity(
                        "load_seven_branch_target_score_cohort",
                        {
                            "run_id": str(frozen_round.run_id),
                            "seven_branch_round": binding.model_dump(mode="json"),
                        },
                        task_queue=child_control_queue,
                        start_to_close_timeout=timedelta(minutes=10),
                        retry_policy=retry,
                    )
                    target = schedule.target_runtime_by_key[str(binding.target_key)]
                    scored = await workflow.execute_activity(
                        "score_seven_branch_target_sequence",
                        {
                            "run_id": str(frozen_round.run_id),
                            "branch_key": binding.branch_key,
                            "target": target.model_dump(mode="json"),
                            "peptides": cohort["peptides"],
                        },
                        task_queue=str(child_request["task_queues"]["target_sequence"]),
                        start_to_close_timeout=timedelta(hours=12),
                        heartbeat_timeout=timedelta(minutes=5),
                        retry_policy=retry,
                    )
                    target_score_receipt = await workflow.execute_activity(
                        "persist_seven_branch_target_sequence",
                        {"run_id": str(frozen_round.run_id), "scored": scored},
                        task_queue=child_control_queue,
                        start_to_close_timeout=timedelta(hours=1),
                        retry_policy=retry,
                    )
                progress_receipt = await workflow.execute_activity(
                    "persist_seven_branch_round_progress",
                    {
                        "controller_run_id": str(schedule.controller_run_id),
                        "round_run_id": str(frozen_round.run_id),
                        "seven_branch_round": binding.model_dump(mode="json"),
                        "branch": branch_by_key[binding.branch_key].model_dump(
                            mode="json"
                        ),
                        "child_result": child_result,
                        "observation_no": observation_no,
                        "schedule_sha256": schedule.sha256(),
                    },
                    task_queue=child_control_queue,
                    start_to_close_timeout=timedelta(minutes=20),
                    retry_policy=retry,
                )
                progress = BranchProgress.model_validate(
                    progress_receipt["progress"]
                )
                progress_by_branch[binding.branch_key] = progress
                await workflow.execute_activity(
                    "mark_run_succeeded",
                    {
                        "run_id": str(frozen_round.run_id),
                        "result_status": "branch_sequence_and_target_evidence_complete",
                        "durable_counts": {
                            "raw_occurrence_count": progress.raw_count,
                            "candidate_count": progress.valid_unique_count,
                            "evaluation_count": (
                                progress.fully_scored_count * 12
                                + progress.target_sequence_scored_count * 2
                            ),
                        },
                    },
                    task_queue=child_control_queue,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=retry,
                )
                current_round_run_id = None
                receipts.append(
                    {
                        "branch_key": binding.branch_key,
                        "round_run_id": str(frozen_round.run_id),
                        "child_status": child_result["status"],
                        "target_score_receipt": target_score_receipt,
                        "progress": progress.model_dump(mode="json"),
                        "controller_action": progress_receipt["controller_action"],
                        "checkpoint_receipt_sha256": progress_receipt[
                            "receipt_sha256"
                        ],
                    }
                )
            next_action = next_controller_branch(
                schedule.design_contract, progress_by_branch
            )
            result = {
                "schema_version": "ampgent.seven-branch-initial-result.1",
                "status": (
                    "all_quotas_ready_for_delivery"
                    if next_action is None
                    else "successor_top_up_or_qualification_required"
                ),
                "controller_run_id": str(schedule.controller_run_id),
                "schedule_sha256": schedule.sha256(),
                "branches": receipts,
                "next_controller_action": next_action,
            }
            await workflow.execute_activity(
                "mark_run_succeeded",
                {
                    "run_id": str(schedule.controller_run_id),
                    "result_status": result["status"],
                    "durable_counts": {
                        "branch_round_count": len(receipts),
                        "raw_occurrence_count": sum(
                            item.raw_count for item in progress_by_branch.values()
                        ),
                        "candidate_count": sum(
                            item.valid_unique_count
                            for item in progress_by_branch.values()
                        ),
                        "evaluation_count": sum(
                            item.fully_scored_count * 12
                            + item.target_sequence_scored_count * 2
                            for item in progress_by_branch.values()
                        ),
                    },
                },
                task_queue=control_queue,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry,
            )
            return result
        except asyncio.CancelledError:
            if current_round_run_id is not None:
                await asyncio.shield(
                    workflow.execute_activity(
                        "mark_run_cancelled",
                        {
                            "run_id": current_round_run_id,
                            "reason": "seven_branch_controller_cancelled",
                        },
                        task_queue=control_queue,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry,
                    )
                )
            await asyncio.shield(
                workflow.execute_activity(
                    "mark_run_cancelled",
                    {
                        "run_id": str(schedule.controller_run_id),
                        "reason": "workflow_cancelled",
                    },
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=retry,
                )
            )
            raise
        except Exception as exc:
            if current_round_run_id is not None:
                await workflow.execute_activity(
                    "mark_run_failed",
                    {
                        "run_id": current_round_run_id,
                        "error_type": type(exc).__name__,
                        "error": f"seven-branch post-prefix failure: {exc}",
                    },
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=retry,
                )
            await workflow.execute_activity(
                "mark_run_failed",
                {
                    "run_id": str(schedule.controller_run_id),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                task_queue=control_queue,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry,
            )
            raise
