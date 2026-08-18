from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


async def _bounded_ordered_map(
    items: list[dict[str, Any]],
    *,
    limit: int,
    operation: Any,
) -> list[Any]:
    if limit < 1:
        raise ValueError("v38 concurrency limit must be positive")
    results: list[Any] = []
    for start in range(0, len(items), limit):
        batch = items[start : start + limit]
        results.extend(await asyncio.gather(*(operation(item) for item in batch)))
    return results


def _validate_request(request: dict[str, Any]) -> None:
    preflight = request.get("submission_preflight")
    if not isinstance(preflight, dict) or preflight.get("status") != (
        "ready_to_submit_unique_run"
    ):
        raise ValueError("v38 workflow requires a passed unique-run preflight")
    contract = request.get("execution_contract")
    if not isinstance(contract, dict):
        raise ValueError("v38 workflow requires an execution contract")
    cells = contract.get("cells")
    if not isinstance(cells, list) or len(cells) != 9:
        raise ValueError("v38 workflow requires exactly nine generator cells")
    if sum(int(item.get("requested_proposals", 0)) for item in cells) != 900:
        raise ValueError("v38 workflow raw occurrence budget must equal 900")
    if contract.get("score_all_valid_unique_proposals") is not True:
        raise ValueError("v38 workflow requires score-all sequence evaluation")
    if contract.get("first_k_retention_forbidden") is not True:
        raise ValueError("v38 workflow forbids first-K sequence truncation")
    plugin_names = contract.get("metric_plugins")
    if not isinstance(plugin_names, list) or len(plugin_names) != 5:
        raise ValueError("v38 workflow requires all five sequence metric plugins")
    if len(plugin_names) != len(set(plugin_names)):
        raise ValueError("v38 workflow metric plugins are duplicated")
    provider = request.get("refinement_provider")
    if not isinstance(provider, dict) or set(provider) != {
        "activity_name",
        "task_queue",
        "provider_task_id",
        "release_revision",
        "runtime_manifest_sha256",
    }:
        raise ValueError("v38 workflow requires one frozen refinement provider")
    if not all(
        isinstance(provider.get(key), str) and provider[key]
        for key in ("activity_name", "task_queue", "provider_task_id", "release_revision")
    ):
        raise ValueError("v38 refinement provider identity is incomplete")
    runtime_sha = provider["runtime_manifest_sha256"]
    if not isinstance(runtime_sha, str) or len(runtime_sha) != 64:
        raise ValueError("v38 refinement provider runtime identity is invalid")
    context_sha = request.get("knowledge_context_pack_sha256")
    if not isinstance(context_sha, str) or len(context_sha) != 64:
        raise ValueError("v38 knowledge context-pack identity is invalid")
    plan_template = request.get("multitarget_plan_template")
    if not isinstance(plan_template, dict) or set(plan_template) != {
        "harness_release_id",
        "history_snapshot_sha256",
        "target_branches",
        "max_parallel_targets",
    }:
        raise ValueError("v38 workflow requires one frozen multitarget plan template")
    branches = plan_template["target_branches"]
    if not isinstance(branches, list) or not 2 <= len(branches) <= 6:
        raise ValueError("v38 workflow requires two to six target branches")
    target_keys = [str(item.get("target_key", "")) for item in branches]
    if not all(target_keys) or len(target_keys) != len(set(target_keys)):
        raise ValueError("v38 workflow target branch keys are invalid")
    runtimes = request.get("structure_runtime_by_target_key")
    if not isinstance(runtimes, dict) or set(runtimes) != set(target_keys):
        raise ValueError("v38 workflow structure runtimes do not cover target branches")
    for target_key, runtime in runtimes.items():
        if not isinstance(runtime, dict) or set(runtime) != {
            "target_sequence",
            "pocket_residues_by_lane",
            "structure_spec",
        }:
            raise ValueError(f"v38 structure runtime is invalid: {target_key}")
        lanes = runtime["pocket_residues_by_lane"]
        if not isinstance(lanes, dict) or set(lanes) != {"native", "wrong_pocket"}:
            raise ValueError(f"v38 structure control lanes are invalid: {target_key}")
    seeds = request.get("boltz_seeds")
    if (
        not isinstance(seeds, list)
        or len(seeds) != 3
        or len({int(item) for item in seeds}) != 3
    ):
        raise ValueError("v38 workflow requires three distinct Boltz seeds")
    queues = request.get("task_queues")
    required_queues = {
        "workflow_and_control",
        "generator",
        "sequence_metrics",
        "structure_boltz",
        "structure_rosetta",
    }
    if not isinstance(queues, dict) or not required_queues <= set(queues):
        raise ValueError("v38 workflow task queues are incomplete")
    for key in ("generation_concurrency", "metric_concurrency", "structure_concurrency"):
        if not isinstance(request.get(key), int) or int(request[key]) < 1:
            raise ValueError(f"v38 workflow concurrency is invalid: {key}")


@workflow.defn(name="V38SequenceFirstAgentWorkflow")
class V38SequenceFirstAgentWorkflow:
    """Durably execute the v38 generation, score-all, and admission prefix."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        _validate_request(request)
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(minutes=10),
            maximum_attempts=2,
            non_retryable_error_types=["ValueError", "KeyError", "TypeError"],
        )
        run_id = str(request["run_id"])
        contract = request["execution_contract"]
        queues = request["task_queues"]
        control_queue = str(queues["workflow_and_control"])
        try:
            await workflow.execute_activity(
                "mark_run_started",
                {"run_id": run_id, "workflow_id": workflow.info().workflow_id},
                task_queue=control_queue,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry,
            )

            async def generate(cell: dict[str, Any]) -> dict[str, Any]:
                generator_id = str(cell["generator_id"])
                return await workflow.execute_activity(
                    "generate_v38_sequence_cell",
                    {
                        "run_id": run_id,
                        "cell": cell,
                        "engine": request["generator_engines_by_name"][generator_id],
                        "runtime": request["generator_runtimes_by_name"][generator_id],
                        "launch_binding": request["generator_launch_bindings_by_name"][
                            generator_id
                        ],
                        "seed": int(cell["seed"]),
                    },
                    task_queue=str(queues["generator"]),
                    start_to_close_timeout=timedelta(hours=12),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )

            generated_cells = await _bounded_ordered_map(
                list(contract["cells"]),
                limit=int(request["generation_concurrency"]),
                operation=generate,
            )
            generation = await workflow.execute_activity(
                "persist_v38_score_all_generation",
                {
                    "run_id": run_id,
                    "execution_contract": contract,
                    "generated_cells": generated_cells,
                },
                task_queue=control_queue,
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=retry,
            )
            candidates = generation["candidates"]
            if len(candidates) != int(generation["candidate_count"]):
                raise ValueError("v38 workflow candidate projection count drifted")

            async def evaluate_and_persist_metric(
                item: dict[str, Any],
                cohort: list[dict[str, Any]],
                *,
                generation: int,
            ) -> dict[str, Any]:
                plugin_name = str(item["plugin_name"])
                reference = await workflow.execute_activity(
                    "evaluate_v38_sequence_metric",
                    {
                        "run_id": run_id,
                        "generation": generation,
                        "stage": "v38_sequence_score_all",
                        "plugin": request["metric_plugins_by_name"][plugin_name],
                        "candidates": cohort,
                    },
                    task_queue=str(queues["sequence_metrics"]),
                    start_to_close_timeout=timedelta(hours=12),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                return await workflow.execute_activity(
                    "persist_v38_sequence_metric",
                    {
                        "run_id": run_id,
                        "execution_contract": contract,
                        "candidates": cohort,
                        "metric_result": reference,
                    },
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )

            metric_items = [
                {"ordinal": ordinal, "plugin_name": plugin_name}
                for ordinal, plugin_name in enumerate(contract["metric_plugins"])
            ]

            async def score_cohort(
                cohort: list[dict[str, Any]], *, generation: int
            ) -> list[dict[str, Any]]:
                async def operation(item: dict[str, Any]) -> dict[str, Any]:
                    return await evaluate_and_persist_metric(
                        item, cohort, generation=generation
                    )

                return await _bounded_ordered_map(
                    metric_items,
                    limit=int(request["metric_concurrency"]),
                    operation=operation,
                )

            metric_results = await score_cohort(candidates, generation=0)
            if sum(int(item["evaluation_count"]) for item in metric_results) != (
                len(candidates) * len(contract["required_sequence_metrics"])
            ):
                raise ValueError("v38 workflow sequence evaluation count drifted")

            admission_reference = await workflow.execute_activity(
                "evaluate_v38_sequence_admission",
                {
                    "run_id": run_id,
                    "refinement_round": 0,
                    "knowledge_context_pack_sha256": request[
                        "knowledge_context_pack_sha256"
                    ],
                },
                task_queue=control_queue,
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=retry,
            )
            admission = await workflow.execute_activity(
                "persist_v38_sequence_admission",
                {
                    "run_id": run_id,
                    "admission_reference": admission_reference,
                    "knowledge_context_pack_sha256": request[
                        "knowledge_context_pack_sha256"
                    ],
                    "environment_sha256": request["control_environment_sha256"],
                    "worker_source_revision": request["worker_source_revision"],
                },
                task_queue=control_queue,
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=retry,
            )
            refinement_rounds_completed = 0
            refinement_occurrence_count = 0
            promoted_refinement_count = 0
            provider = request["refinement_provider"]
            while admission["refinement_required"]:
                plan = admission_reference.get("refinement_plan")
                if not isinstance(plan, dict):
                    raise ValueError("v38 blocked admission lacks a refinement plan")
                provider_result = await workflow.execute_activity(
                    str(provider["activity_name"]),
                    {
                        "schema_version": "v38.refinement-provider-request.1",
                        "run_id": run_id,
                        "refinement_plan": plan,
                        "provider_task_id": provider["provider_task_id"],
                        "knowledge_context_pack_sha256": request[
                            "knowledge_context_pack_sha256"
                        ],
                        "provider_release_revision": provider["release_revision"],
                        "provider_runtime_manifest_sha256": provider[
                            "runtime_manifest_sha256"
                        ],
                    },
                    task_queue=str(provider["task_queue"]),
                    start_to_close_timeout=timedelta(hours=12),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                persisted = await workflow.execute_activity(
                    "persist_v38_refinement_children",
                    {
                        "run_id": run_id,
                        "refinement_plan": plan,
                        "refinement_result": provider_result,
                    },
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )
                refinement_rounds_completed += 1
                refinement_occurrence_count += int(
                    persisted["raw_child_occurrence_count"]
                )
                promoted = list(persisted["candidates"])
                promoted_refinement_count += len(promoted)
                if not promoted:
                    break
                child_metric_results = await score_cohort(
                    promoted, generation=int(plan["refinement_round"])
                )
                if sum(
                    int(item["evaluation_count"]) for item in child_metric_results
                ) != len(promoted) * len(contract["required_sequence_metrics"]):
                    raise ValueError("v38 refinement child evaluation count drifted")
                metric_results.extend(child_metric_results)
                admission_reference = await workflow.execute_activity(
                    "evaluate_v38_sequence_admission",
                    {
                        "run_id": run_id,
                        "refinement_round": int(plan["refinement_round"]),
                        "knowledge_context_pack_sha256": request[
                            "knowledge_context_pack_sha256"
                        ],
                    },
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )
                admission = await workflow.execute_activity(
                    "persist_v38_sequence_admission",
                    {
                        "run_id": run_id,
                        "admission_reference": admission_reference,
                        "knowledge_context_pack_sha256": request[
                            "knowledge_context_pack_sha256"
                        ],
                        "environment_sha256": request[
                            "control_environment_sha256"
                        ],
                        "worker_source_revision": request["worker_source_revision"],
                    },
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )
            structure_task_count = 0
            structure_evidence_count = 0
            if admission["structure_dispatch_allowed"]:
                structure_plan = await workflow.execute_activity(
                    "plan_v38_multitarget_structure",
                    {
                        "run_id": run_id,
                        "admission_reference": admission_reference,
                        "multitarget_plan_template": request[
                            "multitarget_plan_template"
                        ],
                        "boltz_seeds": request["boltz_seeds"],
                    },
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )
                tasks = list(structure_plan["tasks"])
                candidates_by_id = {
                    str(item["id"]): item for item in structure_plan["candidates"]
                }
                branches_by_key = {
                    str(item["target_key"]): item
                    for item in request["multitarget_plan_template"][
                        "target_branches"
                    ]
                }

                async def execute_structure_task(
                    task: dict[str, Any],
                ) -> dict[str, Any]:
                    target_key = str(task["target_key"])
                    candidate = candidates_by_id[str(task["candidate_id"])]
                    runtime = request["structure_runtime_by_target_key"][target_key]
                    raw_boltz = await workflow.execute_activity(
                        "predict_v38_multitarget_structure",
                        {
                            "run_id": run_id,
                            "candidate": candidate,
                            "structure_task": task,
                            "target_branch": branches_by_key[target_key],
                            "target_sequence": runtime["target_sequence"],
                            "pocket_definition_sha256": task["pocket_sha256"],
                            "pocket_residues": runtime[
                                "pocket_residues_by_lane"
                            ][task["control_lane"]],
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
                        {"run_id": run_id, "structure_result": raw_boltz},
                        task_queue=control_queue,
                        start_to_close_timeout=timedelta(hours=1),
                        retry_policy=retry,
                    )
                    raw_rosetta = await workflow.execute_activity(
                        "score_v38_multitarget_rosetta",
                        {
                            "run_id": run_id,
                            "candidate": candidate,
                            "structure_task": task,
                            "target_branch": branches_by_key[target_key],
                            "target_sequence": runtime["target_sequence"],
                            "pocket_definition_sha256": task["pocket_sha256"],
                            "pocket_residues": runtime[
                                "pocket_residues_by_lane"
                            ][task["control_lane"]],
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
                            "run_id": run_id,
                            "rosetta_result": raw_rosetta,
                            "boltz_evidence": boltz["boltz_evidence"],
                        },
                        task_queue=control_queue,
                        start_to_close_timeout=timedelta(hours=1),
                        retry_policy=retry,
                    )

                structure_results = await _bounded_ordered_map(
                    tasks,
                    limit=int(request["structure_concurrency"]),
                    operation=execute_structure_task,
                )
                structure_task_count = len(tasks)
                structure_evidence_count = sum(
                    1
                    + int(item["persistence_receipt"]["rosetta_decoy_count"])
                    for item in structure_results
                )
                expected_evidence_count = structure_task_count * (
                    1
                    + int(
                        request["multitarget_plan_template"]["target_branches"][0][
                            "rosetta_decoys_per_pose"
                        ]
                    )
                )
                if structure_evidence_count != expected_evidence_count:
                    raise ValueError("v38 structure evidence cardinality drifted")
                status = "multitarget_structure_evidence_complete_pending_pareto_replay"
            else:
                status = "sequence_evidence_concluded_without_structure"
            return {
                "schema_version": "v38.sequence-first-prefix-result.1",
                "status": status,
                "raw_occurrence_count": generation["score_all_cohort"][
                    "raw_occurrence_count"
                ],
                "candidate_count": len(candidates) + promoted_refinement_count,
                "evaluation_count": sum(
                    int(item["evaluation_count"]) for item in metric_results
                ),
                "refinement_rounds_completed": refinement_rounds_completed,
                "refinement_raw_occurrence_count": refinement_occurrence_count,
                "refinement_promoted_unique_count": promoted_refinement_count,
                "admission": admission,
                "admission_reference": admission_reference,
                "structure_task_count": structure_task_count,
                "structure_evidence_count": structure_evidence_count,
                "formal_structure_workflow_complete": False,
            }
        except Exception as exc:
            await workflow.execute_activity(
                "mark_run_failed",
                {"run_id": run_id, "error": f"{type(exc).__name__}: {exc}"},
                task_queue=control_queue,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry,
            )
            raise
