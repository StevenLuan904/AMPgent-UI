from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
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
            control_queue = str(
                frozen_round.request["task_queues"]["workflow_and_control"]
            )
            try:
                child_result = await workflow.execute_child_workflow(
                    "V38SequenceFirstAgentWorkflow",
                    frozen_round.request,
                    id=frozen_round.workflow_id,
                    task_queue=control_queue,
                )
            except Exception as exc:
                await workflow.execute_activity(
                    "mark_run_failed",
                    {
                        "run_id": str(schedule.controller_run_id),
                        "error_type": type(exc).__name__,
                        "error": (
                            f"v39 round {round_ordinal} child failed: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                    },
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=retry,
                )
                raise
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
        structure_task_count = 0
        structure_evidence_count = 0
        final_portfolio = None
        if cross_round_admission["structure_dispatch_allowed"]:
            controller_run_id = str(schedule.controller_run_id)
            queues = final_request["task_queues"]
            structure_plan = await workflow.execute_activity(
                "plan_v38_multitarget_structure",
                {
                    "run_id": controller_run_id,
                    "admission_reference": cross_round_admission,
                    "multitarget_plan_template": final_request[
                        "multitarget_plan_template"
                    ],
                    "boltz_seeds": final_request["boltz_seeds"],
                },
                task_queue=str(queues["workflow_and_control"]),
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=retry,
            )
            tasks = list(structure_plan["tasks"])
            candidates_by_id = {
                str(item["id"]): item for item in structure_plan["candidates"]
            }
            branches_by_key = {
                str(item["target_key"]): item
                for item in final_request["multitarget_plan_template"][
                    "target_branches"
                ]
            }
            structure_limit = int(final_request["structure_concurrency"])
            boltz_slots = asyncio.Semaphore(structure_limit)
            rosetta_slots = asyncio.Semaphore(structure_limit)

            async def execute_structure_task(task: dict[str, Any]) -> dict[str, Any]:
                target_key = str(task["target_key"])
                candidate = candidates_by_id[str(task["candidate_id"])]
                runtime = final_request["structure_runtime_by_target_key"][target_key]
                async with boltz_slots:
                    raw_boltz = await workflow.execute_activity(
                        "predict_v38_multitarget_structure",
                        {
                            "run_id": controller_run_id,
                            "candidate": candidate,
                            "structure_task": task,
                            "target_branch": branches_by_key[target_key],
                            "target_sequence": runtime["target_sequence"],
                            "pocket_definition_sha256": task["pocket_sha256"],
                            "pocket_residues": runtime["pocket_residues_by_lane"][
                                task["control_lane"]
                            ],
                            "structure_spec": runtime["structure_spec"],
                            "seed": task["boltz_seed"],
                        },
                        task_queue=str(queues["structure_boltz"]),
                        start_to_close_timeout=timedelta(hours=12),
                        heartbeat_timeout=timedelta(minutes=5),
                        retry_policy=retry,
                    )
                boltz = await workflow.execute_activity(
                    "persist_v38_multitarget_boltz",
                    {"run_id": controller_run_id, "structure_result": raw_boltz},
                    task_queue=str(queues["workflow_and_control"]),
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )
                async with rosetta_slots:
                    raw_rosetta = await workflow.execute_activity(
                        "score_v38_multitarget_rosetta",
                        {
                            "run_id": controller_run_id,
                            "candidate": candidate,
                            "structure_task": task,
                            "target_branch": branches_by_key[target_key],
                            "target_sequence": runtime["target_sequence"],
                            "pocket_definition_sha256": task["pocket_sha256"],
                            "pocket_residues": runtime["pocket_residues_by_lane"][
                                task["control_lane"]
                            ],
                            "structure_spec": runtime["structure_spec"],
                            "structure": boltz["structure"],
                        },
                        task_queue=str(queues["structure_rosetta"]),
                        start_to_close_timeout=timedelta(hours=12),
                        heartbeat_timeout=timedelta(minutes=5),
                        retry_policy=retry,
                    )
                return await workflow.execute_activity(
                    "persist_v38_multitarget_rosetta",
                    {
                        "run_id": controller_run_id,
                        "rosetta_result": raw_rosetta,
                        "boltz_evidence": boltz["boltz_evidence"],
                    },
                    task_queue=str(queues["workflow_and_control"]),
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )

            structure_results = list(
                await asyncio.gather(
                    *(execute_structure_task(task) for task in tasks)
                )
            )
            structure_task_count = len(tasks)
            structure_evidence_count = sum(
                1 + int(item["persistence_receipt"]["rosetta_decoy_count"])
                for item in structure_results
            )
            decoys_per_pose = int(
                final_request["multitarget_plan_template"]["target_branches"][0][
                    "rosetta_decoys_per_pose"
                ]
            )
            if structure_evidence_count != structure_task_count * (
                1 + decoys_per_pose
            ):
                raise ValueError("v39 structure evidence cardinality drifted")
            final_portfolio = await workflow.execute_activity(
                "persist_v38_final_portfolio_replay",
                {
                    "run_id": controller_run_id,
                    "admission_reference": cross_round_admission,
                    "boltz_seeds": final_request["boltz_seeds"],
                    "rosetta_decoys_per_pose": decoys_per_pose,
                    "environment_sha256": final_request[
                        "control_environment_sha256"
                    ],
                    "worker_source_revision": final_request[
                        "worker_source_revision"
                    ],
                },
                task_queue=str(queues["workflow_and_control"]),
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=retry,
            )
            if not final_portfolio["replay_verified"]:
                raise ValueError("v39 final portfolio replay was not verified")
        return {
            "schema_version": "ampgent.sequence-space-exploration-result.1",
            "status": (
                "multitarget_final_portfolio_replay_complete"
                if final_portfolio is not None
                else "sequence_evidence_concluded_without_structure"
            ),
            "controller_run_id": str(schedule.controller_run_id),
            "schedule_sha256": schedule.sha256(),
            "rounds": round_receipts,
            "observations": [item.model_dump(mode="json") for item in observations],
            "cross_round_admission": cross_round_admission,
            "structure_task_count": structure_task_count,
            "structure_evidence_count": structure_evidence_count,
            "final_portfolio": final_portfolio,
            "formal_structure_workflow_complete": final_portfolio is not None,
        }
