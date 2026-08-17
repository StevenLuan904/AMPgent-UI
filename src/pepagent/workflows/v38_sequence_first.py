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

            async def evaluate_and_persist_metric(item: dict[str, Any]) -> dict[str, Any]:
                plugin_name = str(item["plugin_name"])
                reference = await workflow.execute_activity(
                    "evaluate_v38_sequence_metric",
                    {
                        "run_id": run_id,
                        "generation": 0,
                        "stage": "v38_sequence_score_all",
                        "plugin": request["metric_plugins_by_name"][plugin_name],
                        "candidates": candidates,
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
                        "candidates": candidates,
                        "metric_result": reference,
                    },
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )

            metric_results = await _bounded_ordered_map(
                [
                    {"ordinal": ordinal, "plugin_name": plugin_name}
                    for ordinal, plugin_name in enumerate(contract["metric_plugins"])
                ],
                limit=int(request["metric_concurrency"]),
                operation=evaluate_and_persist_metric,
            )
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
            status = (
                "sequence_admitted_for_multitarget_structure"
                if admission["structure_dispatch_allowed"]
                else "sequence_refinement_required"
            )
            return {
                "schema_version": "v38.sequence-first-prefix-result.1",
                "status": status,
                "raw_occurrence_count": generation["score_all_cohort"][
                    "raw_occurrence_count"
                ],
                "candidate_count": len(candidates),
                "evaluation_count": sum(
                    int(item["evaluation_count"]) for item in metric_results
                ),
                "admission": admission,
                "admission_reference": admission_reference,
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
