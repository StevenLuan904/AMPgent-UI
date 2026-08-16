from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


def _compact_v37_pose(structure: dict[str, Any]) -> dict[str, Any]:
    """Project a persisted pose to the exact fields needed after Rosetta."""
    coordinates = [
        item
        for item in structure["provenance"]["engine_artifacts"]
        if str(item["path"]).lower().endswith((".cif", ".pdb"))
    ]
    preferred = [item for item in coordinates if "model_0" in str(item["path"])]
    if not (preferred or coordinates):
        raise ValueError("v37 compact pose requires a persisted coordinate artifact")
    coordinate = sorted(preferred or coordinates, key=lambda item: str(item["path"]))[0]
    seed = int(structure["input"]["seed"])
    pose_id = str(structure["tool_call_id"])
    audit = next(
        item
        for item in structure["interface_audit"]["sample_audits"]
        if str(item["tool_call_id"]) == pose_id and int(item["seed"]) == seed
    )
    candidate = structure["candidate"]
    return {
        "candidate": {
            "id": candidate["id"],
            "sequence": candidate["sequence"],
            "sequence_sha256": candidate["sequence_sha256"],
        },
        "input": {"seed": seed},
        "tool_call_id": pose_id,
        "boltz2": {"pair_iptm": structure["boltz2"]["pair_iptm"]},
        "coordinate_artifact": coordinate,
        "interface_audit_sample": audit,
        "interface_audit_tool_call_id": structure["interface_audit_tool_call_id"],
    }


def _compact_v37_rosetta(result: dict[str, Any]) -> dict[str, Any]:
    """Project one persisted Rosetta result without changing any decoy evidence."""
    return {
        "tool_call_id": result["tool_call_id"],
        "provenance": {
            "parent_tool_call_id": result["provenance"]["parent_tool_call_id"]
        },
        "rosetta": {"decoys": result["rosetta"]["decoys"]},
    }


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
                launch_binding = request["generator_launch_bindings"][
                    engine["generator_id"]
                ]
                for seed in engine["seeds"]:
                    generation_cells.append(
                        {
                            "ordinal": len(generation_cells),
                            "engine": engine,
                            "runtime": runtime,
                            "launch_binding": launch_binding,
                            "seed": seed,
                        }
                    )

            async def generate(cell: dict[str, Any]) -> dict[str, Any]:
                generated = await workflow.execute_activity(
                    "generate_v37_batch",
                    {
                        "run_id": run_id,
                        "engine": cell["engine"],
                        "runtime": cell["runtime"],
                        "launch_binding": cell["launch_binding"],
                        "seed": cell["seed"],
                    },
                    task_queue=queues["generator"],
                    start_to_close_timeout=timedelta(hours=12),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                return {"ordinal": cell["ordinal"], "generated": generated}

            generated_cells = await _bounded_ordered_map(
                generation_cells,
                limit=int(manifest["execution"]["generation_concurrency"]),
                operation=generate,
            )
            generated_cells.sort(key=lambda item: item["ordinal"])
            generation_results = []
            for cell in generated_cells:
                persisted = await workflow.execute_activity(
                    "persist_v37_generation_batch",
                    {
                        "run_id": run_id,
                        "manifest": manifest,
                        "generated": cell["generated"],
                    },
                    task_queue=queues["workflow_and_control"],
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )
                generation_results.append(
                    {
                        "ordinal": cell["ordinal"],
                        "activity_transition_receipt": cell["generated"][
                            "activity_transition_receipt"
                        ],
                        **persisted,
                    }
                )
            all_candidates = [
                candidate
                for result in generation_results
                for candidate in result["candidates"]
            ]
            pipeline_occurrences = [
                {
                    "proposal_ordinal": ordinal,
                    "occurrence_id": candidate["id"],
                }
                for ordinal, candidate in enumerate(all_candidates, start=1)
            ]

            metric_plugins = manifest["stage_1_sequence_evaluation"]["metric_plugins"]

            async def evaluate_and_persist_metric(
                item: dict[str, Any],
            ) -> dict[str, Any]:
                plugin = request["metric_plugins_by_name"][item["name"]]
                result = await workflow.execute_activity(
                    "evaluate_v37_sequence_metric",
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
                    "persist_v37_sequence_metric",
                    {
                        "run_id": run_id,
                        "manifest": manifest,
                        "candidates": all_candidates,
                        "metric_result": result,
                    },
                    task_queue=queues["workflow_and_control"],
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )
                return {
                    "ordinal": item["ordinal"],
                    "activity_transition_receipt": result[
                        "activity_transition_receipt"
                    ],
                    **persisted,
                }

            metric_results = await _bounded_ordered_map(
                [
                    {"ordinal": ordinal, **plugin}
                    for ordinal, plugin in enumerate(metric_plugins)
                ],
                limit=int(manifest["execution"]["metric_concurrency"]),
                operation=evaluate_and_persist_metric,
            )
            await workflow.execute_activity(
                "persist_v37_knowledge_projection",
                {
                    "run_id": run_id,
                    "manifest": manifest,
                    "query": request["knowledge_query"],
                    "knowledge": knowledge,
                    "candidates": all_candidates,
                },
                task_queue=queues["workflow_and_control"],
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=retry,
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
                        "predict_v37_boltz2_complex",
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
                return {
                    **item,
                    "structure": persisted,
                    "activity_transition_receipt": structure[
                        "activity_transition_receipt"
                    ],
                }

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
                    "score_v37_rosetta_complex",
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
                return {
                    **item,
                    "rosetta": persisted,
                    "activity_transition_receipt": rosetta[
                        "activity_transition_receipt"
                    ],
                }

            rosetta_results_with_ordinals = await _bounded_ordered_map(
                rosetta_items,
                limit=int(manifest["execution"]["rosetta_concurrency"]),
                operation=score_and_persist_pose,
            )
            rosetta_results_with_ordinals.sort(key=lambda item: item["ordinal"])
            compact_structures_by_candidate = {
                candidate_id: [_compact_v37_pose(pose) for pose in item["poses"]]
                for candidate_id, item in audited_by_candidate.items()
            }
            compact_rosetta_results = [
                _compact_v37_rosetta(item["rosetta"])
                for item in rosetta_results_with_ordinals
            ]
            structure_summary = await workflow.execute_activity(
                "persist_v37_structure_stage_summaries",
                {
                    "run_id": run_id,
                    "manifest": manifest,
                    "candidate_ids": [item["id"] for item in shortlist["candidates"]],
                    "structures_by_candidate": compact_structures_by_candidate,
                    "rosetta_results": compact_rosetta_results,
                },
                task_queue=queues["workflow_and_control"],
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=retry,
            )
            pepshot = await workflow.execute_activity(
                "run_and_persist_v37_pepshot",
                {
                    "run_id": run_id,
                    "manifest": manifest,
                    "runtime": request["pepshot_runtime"],
                    "provider_contract": manifest["verified_auxiliaries"][
                        "pepshot"
                    ],
                    "candidates": shortlist["candidates"],
                    "experiment_spec": request["experiment_spec"],
                    "structures_by_candidate": compact_structures_by_candidate,
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
            shortlisted_ids = {str(item["id"]) for item in shortlist["candidates"]}
            proposal_receipts = {
                str(candidate["id"]): result["activity_transition_receipt"]
                for result in generation_results
                for candidate in result["candidates"]
            }
            evaluation_receipts = [
                item["activity_transition_receipt"] for item in metric_results
            ]
            boltz_receipts: dict[str, list[dict[str, Any]]] = {}
            for pose in pose_results:
                candidate_id = str(pose["candidate"]["id"])
                boltz_receipts.setdefault(candidate_id, []).append(
                    pose["activity_transition_receipt"]
                )
            rosetta_receipts: dict[str, list[dict[str, Any]]] = {}
            for scored in rosetta_results_with_ordinals:
                candidate_id = str(scored["rosetta"]["candidate"]["id"])
                rosetta_receipts.setdefault(candidate_id, []).append(
                    scored["activity_transition_receipt"]
                )
            final = await workflow.execute_activity(
                "persist_v37_final_portfolio_and_replay",
                {
                    "run_id": run_id,
                    "manifest": manifest,
                    "structurally_eligible_candidate_ids": structurally_eligible,
                    "structure_summary": structure_summary,
                    "pepshot": pepshot,
                    "worker_placement_snapshot": request["worker_placement_snapshot"],
                    "pipeline_occurrences": pipeline_occurrences,
                    "pipeline_transition_receipts": {
                        "shortlisted_ids": sorted(shortlisted_ids),
                        "proposal": proposal_receipts,
                        "evaluation": evaluation_receipts,
                        "boltz": boltz_receipts,
                        "rosetta": rosetta_receipts,
                    },
                },
                task_queue=queues["workflow_and_control"],
                start_to_close_timeout=timedelta(hours=2),
                retry_policy=retry,
            )
            await workflow.execute_activity(
                "finalize_v37_run",
                {
                    "run_id": run_id,
                    "manifest": manifest,
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
