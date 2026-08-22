from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from pepagent.sequence_space_exploration import (
    ExplorationBatchObservation,
    V39ExplorationSchedule,
    next_exploration_action,
)


@workflow.defn(name="V39SequenceSpaceExplorationWorkflow")
class V39SequenceSpaceExplorationWorkflow:
    """Durable outer loop over independently frozen score-all science runs."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        schedule = V39ExplorationSchedule.model_validate(request)
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(minutes=10),
            maximum_attempts=2,
            non_retryable_error_types=["ValueError", "KeyError", "TypeError"],
        )
        observations: list[ExplorationBatchObservation] = []
        round_receipts: list[dict[str, Any]] = []

        for frozen_round in schedule.rounds:
            binding = frozen_round.request["exploration_round"]
            round_ordinal = int(binding["round_ordinal"])
            child_result = await workflow.execute_child_workflow(
                "V38SequenceFirstAgentWorkflow",
                frozen_round.request,
                id=frozen_round.workflow_id,
                task_queue=str(
                    frozen_round.request["task_queues"]["workflow_and_control"]
                ),
            )
            observation_payload = await workflow.execute_activity(
                "persist_v39_exploration_round_yield",
                {
                    "controller_run_id": str(schedule.controller_run_id),
                    "round_run_id": str(frozen_round.run_id),
                    "round_ordinal": round_ordinal,
                    "prior_round_run_ids": [
                        str(item.run_id) for item in schedule.rounds[:round_ordinal]
                    ],
                    "child_result": child_result,
                    "exploration_contract_sha256": (
                        schedule.exploration_contract.sha256()
                    ),
                    "schedule_sha256": schedule.sha256(),
                },
                task_queue=str(
                    frozen_round.request["task_queues"]["workflow_and_control"]
                ),
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry,
            )
            observation = ExplorationBatchObservation.model_validate(
                observation_payload["observation"]
            )
            observations.append(observation)
            action = next_exploration_action(
                tuple(observations),
                maximum_batches=schedule.exploration_contract.maximum_rounds,
            )
            checkpoint = await workflow.execute_activity(
                "persist_v39_exploration_controller_action",
                {
                    "controller_run_id": str(schedule.controller_run_id),
                    "round_run_id": str(frozen_round.run_id),
                    "round_ordinal": round_ordinal,
                    "action": action,
                    "observation": observation.model_dump(mode="json"),
                    "schedule_sha256": schedule.sha256(),
                },
                task_queue=str(
                    frozen_round.request["task_queues"]["workflow_and_control"]
                ),
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry,
            )
            round_receipts.append(
                {
                    "round_run_id": str(frozen_round.run_id),
                    "round_ordinal": round_ordinal,
                    "child_status": child_result["status"],
                    "controller_action": action,
                    "yield_receipt_sha256": observation_payload["receipt_sha256"],
                    "checkpoint_receipt_sha256": checkpoint["receipt_sha256"],
                }
            )

        final_request = schedule.rounds[-1].request
        cross_round_admission = await workflow.execute_activity(
            "persist_v39_cross_round_admission",
            {
                "controller_run_id": str(schedule.controller_run_id),
                "round_run_ids": [str(item.run_id) for item in schedule.rounds],
                "knowledge_context_pack_sha256": final_request[
                    "knowledge_context_pack_sha256"
                ],
                "worker_source_revision": final_request["worker_source_revision"],
                "exploration_contract_sha256": (
                    schedule.exploration_contract.sha256()
                ),
                "schedule_sha256": schedule.sha256(),
            },
            task_queue=str(
                final_request["task_queues"]["workflow_and_control"]
            ),
            start_to_close_timeout=timedelta(hours=1),
            retry_policy=retry,
        )
        return {
            "schema_version": "ampgent.sequence-space-exploration-result.1",
            "status": "sequence_space_complete_structure_portfolio_pending",
            "controller_run_id": str(schedule.controller_run_id),
            "schedule_sha256": schedule.sha256(),
            "rounds": round_receipts,
            "observations": [item.model_dump(mode="json") for item in observations],
            "cross_round_admission": cross_round_admission,
            "structure_dispatch_allowed": False,
            "structure_dispatch_blocker": (
                "cross_round_admission_and_portfolio_plan_not_persisted"
            ),
        }
