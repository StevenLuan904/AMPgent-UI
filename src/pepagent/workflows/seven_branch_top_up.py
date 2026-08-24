from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pepagent.seven_branch_design import (
        BranchProgress,
        SevenBranchRoundBinding,
        SevenBranchTopUpSchedule,
    )


@workflow.defn(name="SevenBranchPeptideTopUpWorkflow")
class SevenBranchPeptideTopUpWorkflow:
    """Execute one frozen top-up epoch and recompute cumulative branch delivery."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        schedule = SevenBranchTopUpSchedule.model_validate(request)
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(minutes=10),
            maximum_attempts=2,
            non_retryable_error_types=["ValueError", "KeyError", "TypeError"],
        )
        first_request = schedule.branches[0].frozen_round.request
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
        branch_by_key = {
            item.branch_key: item for item in schedule.design_contract.branches
        }
        receipts: list[dict[str, Any]] = []
        current_round_run_id: str | None = None
        try:
            for epoch_branch in schedule.branches:
                frozen_round = epoch_branch.frozen_round
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
                cumulative = await workflow.execute_activity(
                    "persist_seven_branch_cumulative_selection",
                    {
                        "controller_run_id": str(schedule.controller_run_id),
                        "source_run_ids": [
                            *(str(item) for item in epoch_branch.prior_source_run_ids),
                            str(frozen_round.run_id),
                        ],
                        "branch": branch_by_key[binding.branch_key].model_dump(
                            mode="json"
                        ),
                        "design_contract_sha256": schedule.design_contract.sha256(),
                        "knowledge_context_pack_sha256": child_request[
                            "knowledge_context_pack_sha256"
                        ],
                        "worker_source_revision": child_request[
                            "worker_source_revision"
                        ],
                    },
                    task_queue=child_control_queue,
                    start_to_close_timeout=timedelta(hours=2),
                    retry_policy=retry,
                )
                progress = BranchProgress.model_validate(cumulative["progress"])
                await workflow.execute_activity(
                    "mark_run_succeeded",
                    {
                        "run_id": str(frozen_round.run_id),
                        "result_status": "branch_top_up_sequence_and_target_complete",
                        "durable_counts": {
                            "raw_occurrence_count": int(
                                child_result["raw_occurrence_count"]
                            ),
                            "candidate_count": int(child_result["candidate_count"]),
                            "evaluation_count": int(child_result["evaluation_count"])
                            + progress.target_sequence_scored_count * 2,
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
                        "target_score_receipt": target_score_receipt,
                        "cumulative": cumulative,
                    }
                )
            successor_required = any(
                item["cumulative"]["top_up_plan"]["action"]
                == "freeze_successor_round"
                for item in receipts
            )
            result = {
                "schema_version": "ampgent.seven-branch-top-up-result.1",
                "status": (
                    "successor_top_up_required"
                    if successor_required
                    else "epoch_branch_quotas_complete"
                ),
                "controller_run_id": str(schedule.controller_run_id),
                "parent_controller_run_id": str(schedule.parent_controller_run_id),
                "epoch_ordinal": schedule.epoch_ordinal,
                "schedule_sha256": schedule.sha256(),
                "branches": receipts,
            }
            await workflow.execute_activity(
                "mark_run_succeeded",
                {
                    "run_id": str(schedule.controller_run_id),
                    "result_status": result["status"],
                    "durable_counts": {
                        "branch_round_count": len(receipts),
                        "cumulative_raw_occurrence_count": sum(
                            item["cumulative"]["progress"]["raw_count"]
                            for item in receipts
                        ),
                        "cumulative_candidate_count": sum(
                            item["cumulative"]["progress"]["valid_unique_count"]
                            for item in receipts
                        ),
                        "delivered_count": sum(
                            item["cumulative"]["progress"]["delivered_count"]
                            for item in receipts
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
                            "reason": "seven_branch_top_up_controller_cancelled",
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
                        "error": f"seven-branch top-up failure: {exc}",
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
