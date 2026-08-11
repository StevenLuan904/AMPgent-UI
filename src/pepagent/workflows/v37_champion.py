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
    """Execute deterministic bounded batches and preserve the frozen ordinal."""
    if limit < 1:
        raise ValueError("v37 concurrency limit must be positive")
    results: list[Any] = []
    for start in range(0, len(items), limit):
        batch = items[start : start + limit]
        observed = await asyncio.gather(*(operation(item) for item in batch))
        results.extend(observed)
    return results


@workflow.defn(name="RapidChampionGenerationV37Workflow")
class RapidChampionGenerationV37Workflow:
    """Durable v37 single-arm generation through exact database replay."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(minutes=10),
            maximum_attempts=2,
            non_retryable_error_types=["ValueError", "KeyError", "TypeError"],
        )
        run_id = request["run_id"]
        manifest = request["manifest"]
        queues = manifest["execution"]["task_queues"]
        preflight = request["submission_preflight"]
        if preflight.get("status") != "ready_to_submit_unique_run":
            raise ValueError("v37 workflow requires a passed submission preflight")
        try:
            await workflow.execute_activity(
                "mark_run_started",
                {"run_id": run_id, "workflow_id": workflow.info().workflow_id},
                task_queue=queues["workflow_and_control"],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry,
            )
            knowledge = await workflow.execute_activity(
                "run_and_persist_v37_knowledge",
                {
                    "run_id": run_id,
                    "runtime": request["knowledge_runtime"],
                    "query": request["knowledge_query"],
                },
                task_queue=queues["provider"],
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            generation_cells = []
            for engine in manifest["generators"]["engines"]:
                runtime = request["generator_runtimes"][engine["generator_id"]]
                for seed in engine["seeds"]:
                    generation_cells.append(
                        {
                            "ordinal": len(generation_cells),
                            "engine": engine,
                            "runtime": runtime,
                            "seed": seed,
                        }
                    )

            async def generate_and_persist(cell: dict[str, Any]) -> dict[str, Any]:
                generated = await workflow.execute_activity(
                    "generate_v37_batch",
                    {
                        "run_id": run_id,
                        "engine": cell["engine"],
                        "runtime": cell["runtime"],
                        "seed": cell["seed"],
                    },
                    task_queue=queues["generator"],
                    start_to_close_timeout=timedelta(hours=12),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                persisted = await workflow.execute_activity(
                    "persist_v37_generation_batch",
                    {"run_id": run_id, "manifest": manifest, "generated": generated},
                    task_queue=queues["workflow_and_control"],
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )
                return {"ordinal": cell["ordinal"], **persisted}

            generation_results = await _bounded_ordered_map(
                generation_cells,
                limit=int(manifest["execution"]["generation_concurrency"]),
                operation=generate_and_persist,
            )
            generation_results.sort(key=lambda item: item["ordinal"])
            all_candidates = [
                candidate
                for result in generation_results
                for candidate in result["candidates"]
            ]

            metric_plugins = manifest["stage_1_sequence_evaluation"]["metric_plugins"]

            async def evaluate_and_persist_metric(
                item: dict[str, Any],
            ) -> dict[str, Any]:
                plugin = request["metric_plugins_by_name"][item["name"]]
                result = await workflow.execute_activity(
                    "evaluate_optional_sequence_metric",
                    {
                        "run_id": run_id,
                        "generation": 0,
                        "stage": "v37_stage1",
                        "plugin": plugin,
                        "candidates": all_candidates,
                    },
                    task_queue=queues["sequence_metrics"],
                    start_to_close_timeout=timedelta(hours=12),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                persisted = await workflow.execute_activity(
                    "persist_optional_sequence_metric",
                    {
                        "run_id": run_id,
                        "generation": 0,
                        "plugin": plugin,
                        "candidates": all_candidates,
                        "metric_result": result,
                        "v37_logical_id": f"v37:metric:{item['name']}",
                    },
                    task_queue=queues["workflow_and_control"],
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )
                return {"ordinal": item["ordinal"], **persisted}

            await _bounded_ordered_map(
                [
                    {"ordinal": ordinal, **plugin}
                    for ordinal, plugin in enumerate(metric_plugins)
                ],
                limit=int(manifest["execution"]["metric_concurrency"]),
                operation=evaluate_and_persist_metric,
            )
            shortlist = await workflow.execute_activity(
                "persist_v37_stage1_shortlist",
                {"run_id": run_id, "manifest": manifest, "knowledge": knowledge},
                task_queue=queues["workflow_and_control"],
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=retry,
            )
            structure_items = [
                {
                    "ordinal": candidate_ordinal * 3 + seed_ordinal,
                    "candidate_ordinal": candidate_ordinal,
                    "candidate": candidate,
                    "seed": seed,
                }
                for candidate_ordinal, candidate in enumerate(shortlist["candidates"])
                for seed_ordinal, seed in enumerate(
                    manifest["stage_2_structure_confirmation"]["boltz_seeds"]
                )
            ]

            async def predict_and_persist_pose(item: dict[str, Any]) -> dict[str, Any]:
                structure = await workflow.execute_activity(
                    "predict_boltz2_complex",
                    {
                        "run_id": run_id,
                        "spec": request["experiment_spec"],
                        "candidate": item["candidate"],
                        "seed": item["seed"],
                    },
                    task_queue=queues["boltz"],
                    start_to_close_timeout=timedelta(hours=8),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                persisted = await workflow.execute_activity(
                    "persist_boltz2_evidence",
                    {"run_id": run_id, "structure": structure},
                    task_queue=queues["workflow_and_control"],
                    start_to_close_timeout=timedelta(minutes=30),
                    retry_policy=retry,
                )
                return {**item, "structure": persisted}

            pose_results = await _bounded_ordered_map(
                structure_items,
                limit=int(manifest["execution"]["boltz_concurrency"]),
                operation=predict_and_persist_pose,
            )
            pose_results.sort(key=lambda item: item["ordinal"])
            pose_groups = [
                {
                    "ordinal": candidate_ordinal,
                    "candidate": candidate,
                    "poses": [
                        item["structure"]
                        for item in pose_results
                        if item["candidate_ordinal"] == candidate_ordinal
                    ],
                }
                for candidate_ordinal, candidate in enumerate(shortlist["candidates"])
            ]

            async def audit_pose_group(item: dict[str, Any]) -> dict[str, Any]:
                audit = await workflow.execute_activity(
                    "audit_structure_ensemble",
                    {
                        "run_id": run_id,
                        "spec": request["experiment_spec"],
                        "generation": 0,
                        "structures": item["poses"],
                    },
                    task_queue=queues["workflow_and_control"],
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )
                persisted_audit = await workflow.execute_activity(
                    "persist_interface_audit",
                    {"run_id": run_id, "audit_result": audit},
                    task_queue=queues["workflow_and_control"],
                    start_to_close_timeout=timedelta(minutes=30),
                    retry_policy=retry,
                )
                poses = []
                for pose in item["poses"]:
                    enriched = dict(pose)
                    enriched["interface_audit"] = persisted_audit["audit"]
                    enriched["interface_audit_tool_call_id"] = persisted_audit[
                        "tool_call_id"
                    ]
                    poses.append(enriched)
                return {**item, "poses": poses, "audit": persisted_audit}

            audited_groups = await _bounded_ordered_map(
                pose_groups,
                limit=int(manifest["execution"]["boltz_concurrency"]),
                operation=audit_pose_group,
            )
            audited_groups.sort(key=lambda item: item["ordinal"])
            audited_by_candidate = {
                item["candidate"]["id"]: item for item in audited_groups
            }
            rosetta_items = [
                {
                    "ordinal": candidate_ordinal * 3 + pose_ordinal,
                    "structure": pose,
                }
                for candidate_ordinal, item in enumerate(audited_groups)
                for pose_ordinal, pose in enumerate(item["poses"])
            ]

            async def score_and_persist_pose(item: dict[str, Any]) -> dict[str, Any]:
                rosetta = await workflow.execute_activity(
                    "score_rosetta_complex",
                    {
                        "run_id": run_id,
                        "spec": request["experiment_spec"],
                        "structure": item["structure"],
                        "seed": int(
                            manifest["stage_2_structure_confirmation"][
                                "rosetta_seed_base"
                            ]
                        )
                        + int(item["ordinal"]),
                    },
                    task_queue=queues["rosetta"],
                    start_to_close_timeout=timedelta(hours=72),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                persisted = await workflow.execute_activity(
                    "persist_rosetta_evidence",
                    {"run_id": run_id, "rosetta_result": rosetta},
                    task_queue=queues["workflow_and_control"],
                    start_to_close_timeout=timedelta(hours=2),
                    retry_policy=retry,
                )
                return {**item, "rosetta": persisted}

            rosetta_results_with_ordinals = await _bounded_ordered_map(
                rosetta_items,
                limit=int(manifest["execution"]["rosetta_concurrency"]),
                operation=score_and_persist_pose,
            )
            rosetta_results_with_ordinals.sort(key=lambda item: item["ordinal"])
            structures = [item["structure"] for item in rosetta_items]
            rosetta_results = [item["rosetta"] for item in rosetta_results_with_ordinals]
            await workflow.execute_activity(
                "persist_v37_structure_stage_summaries",
                {
                    "run_id": run_id,
                    "manifest": manifest,
                    "candidate_ids": [item["id"] for item in shortlist["candidates"]],
                    "structures_by_candidate": {
                        candidate_id: item["poses"]
                        for candidate_id, item in audited_by_candidate.items()
                    },
                    "rosetta_results": rosetta_results,
                },
                task_queue=queues["workflow_and_control"],
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=retry,
            )
            pepshot = await workflow.execute_activity(
                "run_and_persist_v37_pepshot",
                {
                    "run_id": run_id,
                    "runtime": request["pepshot_runtime"],
                    "provider_contract": manifest["verified_auxiliaries"][
                        "pepshot"
                    ],
                    "candidates": shortlist["candidates"],
                    "experiment_spec": request["experiment_spec"],
                    "structures_by_candidate": {
                        candidate_id: item["poses"]
                        for candidate_id, item in audited_by_candidate.items()
                    },
                },
                task_queue=queues["provider"],
                start_to_close_timeout=timedelta(hours=12),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            structurally_eligible = []
            structurally_eligible.extend(
                item["candidate_id"]
                for item in pepshot["inspections"]
                if item["disposition"] == "retain"
            )
            final = await workflow.execute_activity(
                "persist_v37_final_portfolio_and_replay",
                {
                    "run_id": run_id,
                    "manifest": manifest,
                    "structurally_eligible_candidate_ids": structurally_eligible,
                },
                task_queue=queues["workflow_and_control"],
                start_to_close_timeout=timedelta(hours=2),
                retry_policy=retry,
            )
            await workflow.execute_activity(
                "finalize_run",
                {
                    "run_id": run_id,
                    "structures": structures,
                    "rosetta_results": rosetta_results,
                    "generation_count": 1,
                    "agent_decision_count": 2,
                    "bulk_rosetta_count": len(rosetta_results),
                    "bulk_csv": None,
                },
                task_queue=queues["workflow_and_control"],
                start_to_close_timeout=timedelta(minutes=10),
                retry_policy=retry,
            )
            return {"run_id": run_id, **final}
        except Exception as error:
            await workflow.execute_activity(
                "mark_run_failed",
                {"run_id": run_id, "error_type": type(error).__name__, "error": str(error)},
                task_queue=queues["workflow_and_control"],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            raise
