import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn(name="PeptideDesignWorkflow")
class PeptideDesignWorkflow:
    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(minutes=10),
            maximum_attempts=5,
        )
        try:
            await workflow.execute_activity(
                "mark_run_started",
                {"run_id": request["run_id"], "workflow_id": workflow.info().workflow_id},
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry,
            )
            spec = request["spec"]
            autoresearch = bool(spec.get("autoresearch_enabled", False))
            diagnostic_fast = spec.get("structure_protocol") == "diagnostic_fast"
            progressive = spec.get("evaluation_ladder_mode") == "lightweight_first"
            generation_count = int(spec["generations"]) if autoresearch else 1
            parents: list[dict[str, Any]] = []
            decision_id: str | None = None
            all_structures: list[dict[str, Any]] = []
            all_rosetta_results: list[dict[str, Any]] = []
            bulk_results: list[dict[str, Any]] = []
            bulk_csv: dict[str, Any] | None = None
            agent_decision_count = 0
            for generation in range(generation_count):
                generated = await workflow.execute_activity(
                    "generate_with_pepmlm",
                    {
                        **request,
                        "generation": generation,
                        "parents": parents,
                        "decision_id": decision_id,
                    },
                    task_queue="pepagent-gpu-pepmlm",
                    start_to_close_timeout=timedelta(hours=4),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                persisted_batch = await workflow.execute_activity(
                    "persist_and_select_candidates",
                    {
                        "run_id": request["run_id"],
                        "generated": generated,
                        "spec": spec,
                        "generation": generation,
                        "parents": parents,
                        "decision_id": decision_id,
                    },
                    task_queue="pepagent-control",
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=retry,
                )
                # Historical activity results were a plain selected-candidate list.
                # Accept that shape so archived/in-flight pre-metric workflow histories
                # remain replayable after this worker revision is deployed.
                if isinstance(persisted_batch, list):
                    selected = persisted_batch
                    all_candidates = persisted_batch
                else:
                    selected = persisted_batch["structure_candidates"]
                    all_candidates = persisted_batch["all_candidates"]
                metric_stage = (
                    "final" if generation == generation_count - 1 else "research"
                )
                for plugin in spec.get("optional_metrics", []):
                    if not plugin.get("enabled", True) or metric_stage not in plugin["stages"]:
                        continue
                    try:
                        metric_result = await workflow.execute_activity(
                            "evaluate_optional_sequence_metric",
                            {
                                "run_id": request["run_id"],
                                "generation": generation,
                                "stage": metric_stage,
                                "plugin": plugin,
                                "candidates": all_candidates,
                            },
                            task_queue="pepagent-cpu-metrics",
                            start_to_close_timeout=timedelta(hours=6),
                            heartbeat_timeout=timedelta(minutes=5),
                            retry_policy=retry,
                        )
                        await workflow.execute_activity(
                            "persist_optional_sequence_metric",
                            {
                                "run_id": request["run_id"],
                                "generation": generation,
                                "plugin": plugin,
                                "candidates": all_candidates,
                                "metric_result": metric_result,
                            },
                            task_queue="pepagent-control",
                            start_to_close_timeout=timedelta(minutes=30),
                            retry_policy=retry,
                        )
                    except Exception as error:
                        if plugin.get("failure_policy", "record_unavailable") == "fail_run":
                            raise
                        await workflow.execute_activity(
                            "persist_optional_metric_failure",
                            {
                                "run_id": request["run_id"],
                                "generation": generation,
                                "plugin": plugin,
                                "candidates": all_candidates,
                                "error_type": type(error).__name__,
                                "error": str(error),
                            },
                            task_queue="pepagent-control",
                            start_to_close_timeout=timedelta(minutes=10),
                            retry_policy=retry,
                        )
                evaluation_plan: dict[str, Any] | None = None
                if autoresearch and progressive:
                    decision = await workflow.execute_activity(
                        "select_next_generation",
                        {
                            "run_id": request["run_id"],
                            "generation": generation,
                            "spec": spec,
                            "final_generation": generation == generation_count - 1,
                        },
                        task_queue="pepagent-control",
                        start_to_close_timeout=timedelta(minutes=15),
                        retry_policy=retry,
                    )
                    parents = decision["parents"]
                    decision_id = decision["decision_id"]
                    evaluation_plan = decision["evaluation_plan"]
                    agent_decision_count += 1
                    selected = parents if evaluation_plan["run_structure"] else []
                round_structures: list[dict[str, Any]] = []
                ensembles: list[dict[str, Any]] = []
                seed_count = 1 if diagnostic_fast else int(spec.get("boltz_seeds_per_candidate", 1))
                for candidate_index, candidate in enumerate(selected):
                    candidate_structures: list[dict[str, Any]] = []
                    for seed_index in range(seed_count):
                        structure_seed = (
                            int(spec["seed"])
                            + generation * 1_000_000
                            + candidate_index * 10_000
                            + seed_index
                        )
                        try:
                            structure = await workflow.execute_activity(
                                "predict_boltz2_complex",
                                {
                                    "run_id": request["run_id"],
                                    "spec": spec,
                                    "candidate": candidate,
                                    "seed": structure_seed,
                                },
                                task_queue="pepagent-gpu-boltz2",
                                start_to_close_timeout=timedelta(hours=6),
                                heartbeat_timeout=timedelta(minutes=5),
                                retry_policy=retry,
                            )
                        except Exception as error:
                            if not diagnostic_fast:
                                raise
                            await workflow.execute_activity(
                                "persist_structure_unavailable",
                                {
                                    "run_id": request["run_id"],
                                    "candidate": candidate,
                                    "reason": "boltz_prediction_or_coordinate_failure",
                                    "error_type": type(error).__name__,
                                    "error": str(error),
                                },
                                task_queue="pepagent-control",
                                start_to_close_timeout=timedelta(minutes=5),
                                retry_policy=retry,
                            )
                            continue
                        structure = await workflow.execute_activity(
                            "persist_boltz2_evidence",
                            {"run_id": request["run_id"], "structure": structure},
                            task_queue="pepagent-control",
                            start_to_close_timeout=timedelta(minutes=15),
                            retry_policy=retry,
                        )
                        candidate_structures.append(structure)
                        round_structures.append(structure)
                    if autoresearch and candidate_structures:
                        audit = await workflow.execute_activity(
                            "audit_structure_ensemble",
                            {
                                "run_id": request["run_id"],
                                "spec": spec,
                                "generation": generation,
                                "structures": candidate_structures,
                            },
                            task_queue="pepagent-control",
                            start_to_close_timeout=timedelta(minutes=30),
                            retry_policy=retry,
                        )
                        audit = await workflow.execute_activity(
                            "persist_interface_audit",
                            {"run_id": request["run_id"], "audit_result": audit},
                            task_queue="pepagent-control",
                            start_to_close_timeout=timedelta(minutes=15),
                            retry_policy=retry,
                        )
                        ensembles.append(audit)
                all_structures.extend(round_structures)
                round_rosetta_results: list[dict[str, Any]] = []
                run_rosetta = (
                    bool(evaluation_plan["run_rosetta"])
                    if progressive and evaluation_plan is not None
                    else spec.get("rosetta_enabled", False)
                    and (not diagnostic_fast or generation == generation_count - 1)
                )
                if run_rosetta:
                    selection_request = (
                        {
                            "ensembles": ensembles,
                            "top_k": spec.get("rosetta_top_k", 1),
                            "exploratory_slots": spec.get("exploratory_rosetta_slots", 0),
                            "mode": "diagnostic_shadow" if diagnostic_fast else "legacy_gate",
                        }
                        if autoresearch
                        else {
                            "structures": round_structures,
                            "pair_iptm_min": spec.get("rosetta_pair_iptm_min", 0.5),
                            "top_k": spec.get("rosetta_top_k", 1),
                        }
                    )
                    rosetta_inputs = await workflow.execute_activity(
                        "select_rosetta_inputs",
                        selection_request,
                        task_queue="pepagent-control",
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=retry,
                    )
                    for rosetta_index, structure in enumerate(rosetta_inputs):
                        rosetta_result = await workflow.execute_activity(
                            "score_rosetta_complex",
                            {
                                "run_id": request["run_id"],
                                "spec": spec,
                                "structure": structure,
                                "seed": int(spec["seed"]) + generation * 1_000_000 + rosetta_index,
                            },
                            task_queue="pepagent-cpu-rosetta",
                            start_to_close_timeout=timedelta(hours=72),
                            heartbeat_timeout=timedelta(minutes=5),
                            retry_policy=retry,
                        )
                        rosetta_result = await workflow.execute_activity(
                            "persist_rosetta_evidence",
                            {
                                "run_id": request["run_id"],
                                "rosetta_result": rosetta_result,
                            },
                            task_queue="pepagent-control",
                            start_to_close_timeout=timedelta(hours=2),
                            retry_policy=retry,
                        )
                        round_rosetta_results.append(rosetta_result)
                    all_rosetta_results.extend(round_rosetta_results)
                if autoresearch and not progressive:
                    decision = await workflow.execute_activity(
                        "select_next_generation",
                        {
                            "run_id": request["run_id"],
                            "generation": generation,
                            "spec": spec,
                            "final_generation": generation == generation_count - 1,
                        },
                        task_queue="pepagent-control",
                        start_to_close_timeout=timedelta(minutes=15),
                        retry_policy=retry,
                    )
                    parents = decision["parents"]
                    decision_id = decision["decision_id"]
                    agent_decision_count += 1
            bulk_enabled = bool(spec.get("bulk_rosetta_all_qualified", False))
            bulk_candidate_limit = int(spec.get("bulk_rosetta_candidate_limit", 250))
            if bulk_enabled:
                cohort = await workflow.execute_activity(
                    "select_bulk_evaluation_candidates",
                    {"run_id": request["run_id"], "spec": spec},
                    task_queue="pepagent-control",
                    start_to_close_timeout=timedelta(minutes=30),
                    retry_policy=retry,
                )
                batch_size = int(spec.get("bulk_evaluation_concurrency", 4))
                candidates = cohort["candidates"]
                for batch_start in range(0, len(candidates), batch_size):
                    batch = candidates[batch_start : batch_start + batch_size]
                    batch_results = await asyncio.gather(
                        *(
                            workflow.execute_child_workflow(
                                "BulkCandidateEvaluationWorkflow",
                                {
                                    "run_id": request["run_id"],
                                    "spec": spec,
                                    "candidate": candidate,
                                    "seed": int(spec["seed"])
                                    + 100_000_000
                                    + (batch_start + offset) * 10_000,
                                },
                                id=(
                                    f"pepagent-bulk-{request['run_id']}-"
                                    f"{candidate['id']}"
                                ),
                                task_queue="pepagent-control",
                            )
                            for offset, candidate in enumerate(batch)
                        )
                    )
                    bulk_results.extend(batch_results)
                bulk_csv = await workflow.execute_activity(
                    "export_bulk_rosetta_csv",
                    {
                        "run_id": request["run_id"],
                        "candidates": candidates,
                        "results": bulk_results,
                    },
                    task_queue="pepagent-control",
                    start_to_close_timeout=timedelta(hours=2),
                    retry_policy=retry,
                )
            return await workflow.execute_activity(
                "finalize_run",
                {
                    "run_id": request["run_id"],
                    "structures": all_structures,
                    "rosetta_results": all_rosetta_results,
                    "generation_count": generation_count,
                    "agent_decision_count": agent_decision_count,
                    "bulk_rosetta_count": sum(
                        item["status"] == "succeeded" for item in bulk_results
                    ),
                    "bulk_rosetta_candidate_limit": bulk_candidate_limit,
                    "bulk_csv_report_threshold": int(
                        spec.get("bulk_csv_report_threshold", 200)
                    ),
                    "bulk_csv": bulk_csv,
                },
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                workflow.execute_activity(
                    "mark_run_cancelled",
                    {
                        "run_id": request["run_id"],
                        "reason": workflow.cancellation_reason() or "workflow_cancelled",
                    },
                    task_queue="pepagent-control",
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
            )
            raise
        except Exception as error:
            await workflow.execute_activity(
                "mark_run_failed",
                {
                    "run_id": request["run_id"],
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            raise


@workflow.defn(name="BulkCandidateEvaluationWorkflow")
class BulkCandidateEvaluationWorkflow:
    """Durably evaluate one bulk candidate from Boltz through shadow Rosetta."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(minutes=10),
            maximum_attempts=5,
        )
        candidate = request["candidate"]

        async def preserve_failure(stage: str, error: Exception) -> dict[str, Any]:
            await workflow.execute_activity(
                "persist_bulk_evaluation_failure",
                {
                    "run_id": request["run_id"],
                    "candidate": candidate,
                    "stage": stage,
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            return {
                "candidate_id": candidate["id"],
                "status": "failed",
                "stage": stage,
            }

        try:
            structure = await workflow.execute_activity(
                "predict_boltz2_complex",
                {
                    "run_id": request["run_id"],
                    "spec": request["spec"],
                    "candidate": candidate,
                    "seed": request["seed"],
                },
                task_queue="pepagent-gpu-boltz2",
                start_to_close_timeout=timedelta(hours=6),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            structure = await workflow.execute_activity(
                "persist_boltz2_evidence",
                {"run_id": request["run_id"], "structure": structure},
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=retry,
            )
        except Exception as error:
            return await preserve_failure("boltz2", error)

        try:
            audit = await workflow.execute_activity(
                "audit_structure_ensemble",
                {
                    "run_id": request["run_id"],
                    "spec": request["spec"],
                    "generation": candidate["generation"],
                    "structures": [structure],
                },
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=retry,
            )
            audit = await workflow.execute_activity(
                "persist_interface_audit",
                {"run_id": request["run_id"], "audit_result": audit},
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=retry,
            )
            rosetta_inputs = await workflow.execute_activity(
                "select_rosetta_inputs",
                {
                    "ensembles": [audit],
                    "top_k": 1,
                    "exploratory_slots": 0,
                    "mode": "diagnostic_shadow",
                },
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            if not rosetta_inputs:
                raise RuntimeError("bulk candidate produced no usable coordinate structure")
        except Exception as error:
            return await preserve_failure("coordinate_audit", error)

        try:
            rosetta_result = await workflow.execute_activity(
                "score_rosetta_complex",
                {
                    "run_id": request["run_id"],
                    "spec": request["spec"],
                    "structure": rosetta_inputs[0],
                    "seed": int(request["seed"]) + 1,
                },
                task_queue="pepagent-cpu-rosetta",
                start_to_close_timeout=timedelta(hours=72),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            rosetta_result = await workflow.execute_activity(
                "persist_rosetta_evidence",
                {"run_id": request["run_id"], "rosetta_result": rosetta_result},
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(hours=2),
                retry_policy=retry,
            )
        except Exception as error:
            return await preserve_failure("rosetta", error)
        return {
            "candidate_id": candidate["id"],
            "status": "succeeded",
            "rosetta_tool_call_id": rosetta_result["tool_call_id"],
        }


@workflow.defn(name="CandidateStructureValidationWorkflow")
class CandidateStructureValidationWorkflow:
    """Validate an explicitly imported candidate cohort against one target."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(minutes=10),
            maximum_attempts=5,
        )
        try:
            await workflow.execute_activity(
                "mark_run_started",
                {"run_id": request["run_id"], "workflow_id": workflow.info().workflow_id},
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry,
            )
            results: list[dict[str, Any]] = []
            candidates = request["candidates"]
            concurrency = int(request["spec"].get("bulk_evaluation_concurrency", 4))
            for batch_start in range(0, len(candidates), concurrency):
                batch = candidates[batch_start : batch_start + concurrency]
                results.extend(
                    await asyncio.gather(
                        *(
                            workflow.execute_child_workflow(
                                "BulkCandidateEvaluationWorkflow",
                                {
                                    "run_id": request["run_id"],
                                    "spec": request["spec"],
                                    "candidate": candidate,
                                    "seed": int(request["spec"]["seed"])
                                    + (batch_start + offset) * 10_000,
                                },
                                id=(
                                    f"pepagent-structure-validation-{request['run_id']}-"
                                    f"{candidate['id']}"
                                ),
                                task_queue="pepagent-control",
                            )
                            for offset, candidate in enumerate(batch)
                        )
                    )
                )
            await workflow.execute_activity(
                "finalize_run",
                {
                    "run_id": request["run_id"],
                    "structures": [],
                    "rosetta_results": [],
                    "generation_count": 0,
                    "agent_decision_count": 0,
                    "bulk_rosetta_count": sum(
                        item.get("status") == "succeeded" for item in results
                    ),
                    "bulk_rosetta_candidate_limit": len(candidates),
                    "bulk_csv_report_threshold": int(
                        request["spec"].get("bulk_csv_report_threshold", 200)
                    ),
                },
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            return {"run_id": request["run_id"], "results": results}
        except asyncio.CancelledError:
            await asyncio.shield(
                workflow.execute_activity(
                    "mark_run_cancelled",
                    {"run_id": request["run_id"], "reason": "workflow_cancelled"},
                    task_queue="pepagent-control",
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
            )
            raise
        except Exception as error:
            await workflow.execute_activity(
                "mark_run_failed",
                {
                    "run_id": request["run_id"],
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            raise


@workflow.defn(name="RosettaValidationWorkflow")
class RosettaValidationWorkflow:
    """Durable public-complex validation without invoking the generation lane."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(minutes=10),
            maximum_attempts=5,
        )
        try:
            await workflow.execute_activity(
                "mark_run_started",
                {"run_id": request["run_id"], "workflow_id": workflow.info().workflow_id},
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry,
            )
            result = await workflow.execute_activity(
                "score_rosetta_complex",
                request,
                task_queue="pepagent-cpu-rosetta",
                start_to_close_timeout=timedelta(hours=72),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            result = await workflow.execute_activity(
                "persist_rosetta_evidence",
                {"run_id": request["run_id"], "rosetta_result": result},
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(hours=2),
                retry_policy=retry,
            )
            return await workflow.execute_activity(
                "finalize_run",
                {
                    "run_id": request["run_id"],
                    "structures": [],
                    "rosetta_results": [result],
                },
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
        except Exception as error:
            await workflow.execute_activity(
                "mark_run_failed",
                {
                    "run_id": request["run_id"],
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            raise
