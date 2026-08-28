from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from pepagent.autoresearch_closed_loop import (
        ContinuationPolicy,
        MultiFrontArchivePolicy,
    )
    from pepagent.autoresearch_score_ingest import safe_relative_score_bundle_path
    from pepagent.provenance.hashing import sha256_text
    from pepagent.v38_science_execution import V38SequenceExecutionContract


def _validate_request(request: dict[str, Any]) -> None:
    if request.get("schema_version") != "ampgent.autoresearch-workflow-request.1":
        raise ValueError("AutoResearch request schema is not frozen")
    contract = V38SequenceExecutionContract.model_validate(request["execution_contract"])
    if len(contract.required_sequence_metrics) != 12:
        raise ValueError("AutoResearch requires the frozen 12-metric score-all contract")
    plugin_names = tuple(contract.metric_plugins)
    plugin_registry = request.get("metric_plugins_by_name") or {}
    if set(plugin_registry) != set(plugin_names):
        raise ValueError("AutoResearch metric plugin registry differs from the contract")
    queues = request.get("task_queues") or {}
    required_queues = {"workflow_and_control", "action_execution", "sequence_metrics"}
    if not required_queues <= set(queues):
        raise ValueError("AutoResearch task queues are incomplete")
    if any(not str(queues[name]).strip() for name in required_queues):
        raise ValueError("AutoResearch task queues must be non-empty")
    provider = request.get("planner_provider") or {}
    if (
        not str(provider.get("activity_name") or "").strip()
        or not str(provider.get("task_queue") or "").strip()
    ):
        raise ValueError("AutoResearch requires a durable Agent planner provider")
    if str(provider["activity_name"]) == "plan_autoresearch_actions" and str(
        provider["task_queue"]
    ) != str(queues["workflow_and_control"]):
        raise ValueError("built-in AutoResearch planner must use the control task queue")
    executor = request.get("action_executor") or {}
    executor_environment = str(executor.get("operator_environment_sha256") or "")
    if len(executor_environment) != 64 or set(executor_environment) - set("0123456789abcdef"):
        raise ValueError("AutoResearch action executor identity is invalid")
    if str(provider["activity_name"]) == "plan_autoresearch_actions":
        target_sequence = "".join(str(executor.get("target_sequence") or "").split()).upper()
        if not target_sequence or sha256_text(target_sequence) != str(
            executor.get("target_sequence_sha256") or ""
        ):
            raise ValueError("built-in planner requires a frozen target sequence identity")
    if request.get("initial_action_plan") is not None:
        plan = request["initial_action_plan"]
        if not isinstance(plan, dict) or not plan.get("actions"):
            raise ValueError("initial AutoResearch action plan is empty")
    MultiFrontArchivePolicy.model_validate(request["archive_policy"])
    continuation = ContinuationPolicy.model_validate(request["continuation_policy"])
    if continuation.minimum_high_quality_candidates < 50:
        raise ValueError("AutoResearch requires at least 50 gold candidates per target")
    environment_sha256 = str(request["control_environment_sha256"])
    if len(environment_sha256) != 64 or set(environment_sha256) - set("0123456789abcdef"):
        raise ValueError("AutoResearch control environment identity is invalid")
    if int(request.get("start_iteration_no", 0)) < 0:
        raise ValueError("AutoResearch start iteration must be non-negative")
    if int(request.get("maximum_iterations_per_workflow_execution", 25)) < 1:
        raise ValueError("AutoResearch continue-as-new interval must be positive")
    seed_import = request.get("seed_score_bundle_import")
    if seed_import is not None:
        if not isinstance(seed_import, dict):
            raise ValueError("AutoResearch seed score bundle import must be an object")
        required_import_fields = {
            "bundle_cache_root",
            "bundle_receipt_path",
            "bundle_receipt_sha256",
            "source_map_receipt_path",
            "source_map_receipt_sha256",
            "source_map_storage_uri",
            "target_key",
        }
        if not required_import_fields <= set(seed_import):
            raise ValueError("AutoResearch seed score bundle import is incomplete")
        if any(
            not str(seed_import[name]).strip()
            for name in required_import_fields
            - {"bundle_receipt_sha256", "source_map_receipt_sha256"}
        ):
            raise ValueError("AutoResearch seed score bundle import paths are empty")
        receipt_sha256 = str(seed_import["bundle_receipt_sha256"])
        if len(receipt_sha256) != 64 or set(receipt_sha256) - set("0123456789abcdef"):
            raise ValueError("AutoResearch seed score bundle receipt identity is invalid")
        source_map_sha256 = str(seed_import["source_map_receipt_sha256"])
        if len(source_map_sha256) != 64 or set(source_map_sha256) - set("0123456789abcdef"):
            raise ValueError("AutoResearch score source-map identity is invalid")
        source_map_uri = str(seed_import["source_map_storage_uri"])
        if not source_map_uri.startswith("ssh://") or (
            f"/{source_map_sha256}/" not in source_map_uri
        ):
            raise ValueError("AutoResearch score source-map URI is not remote CAS")
        safe_relative_score_bundle_path(str(seed_import["bundle_receipt_path"]))
        safe_relative_score_bundle_path(str(seed_import["source_map_receipt_path"]))
        if str(seed_import["target_key"]) != str(request["branch_key"]):
            raise ValueError("AutoResearch seed score bundle target differs from its branch")


@workflow.defn(name="AutoResearchClosedLoopWorkflow")
class AutoResearchClosedLoopWorkflow:
    """Durable action, generation, score-all, archive, and replay loop."""

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
        queues = request["task_queues"]
        control_queue = str(queues["workflow_and_control"])
        action_queue = str(queues["action_execution"])
        metrics_queue = str(queues["sequence_metrics"])
        contract = request["execution_contract"]
        plugin_names = list(contract["metric_plugins"])
        iteration_no = int(request.get("start_iteration_no", 0))
        stagnant_generations = int(request.get("prior_consecutive_stagnant_generations", 0))
        previous_checkpoint = request.get("previous_checkpoint")
        completed_in_this_execution = 0
        latest_checkpoint: dict[str, Any] | None = None

        try:
            if not bool(request.get("workflow_chain_started", False)):
                await workflow.execute_activity(
                    "mark_run_started",
                    {"run_id": run_id, "workflow_id": workflow.info().workflow_id},
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(minutes=15),
                    retry_policy=retry,
                )
                seed_import = request.get("seed_score_bundle_import")
                if seed_import is not None:
                    await workflow.execute_activity(
                        "persist_autoresearch_score_all_bundle",
                        {
                            **seed_import,
                            "run_id": run_id,
                            "control_environment_sha256": request["control_environment_sha256"],
                        },
                        task_queue=control_queue,
                        start_to_close_timeout=timedelta(hours=2),
                        heartbeat_timeout=timedelta(minutes=15),
                        retry_policy=retry,
                    )

            while completed_in_this_execution < int(
                request.get("maximum_iterations_per_workflow_execution", 25)
            ):
                if (
                    completed_in_this_execution == 0
                    and request.get("initial_action_plan") is not None
                ):
                    proposed = request["initial_action_plan"]
                else:
                    provider = request["planner_provider"]
                    proposed = await workflow.execute_activity(
                        str(provider["activity_name"]),
                        {
                            "schema_version": "ampgent.autoresearch-planner-request.1",
                            "run_id": run_id,
                            "branch_key": str(request["branch_key"]),
                            "iteration_no": iteration_no,
                            "previous_checkpoint": previous_checkpoint,
                            "archive_policy": request["archive_policy"],
                            "continuation_policy": request["continuation_policy"],
                            "planner_contract": provider.get("planner_contract") or {},
                            "execution_contract": contract,
                            "operator_release_sha256": str(
                                (request.get("action_executor") or {}).get(
                                    "operator_release_sha256"
                                )
                                or (request.get("action_executor") or {})[
                                    "operator_environment_sha256"
                                ]
                            ),
                            "control_environment_sha256": request["control_environment_sha256"],
                            "target_sequence_sha256": request["action_executor"][
                                "target_sequence_sha256"
                            ],
                        },
                        task_queue=str(provider["task_queue"]),
                        start_to_close_timeout=timedelta(hours=1),
                        heartbeat_timeout=timedelta(minutes=5),
                        retry_policy=retry,
                    )

                action_plan = await workflow.execute_activity(
                    "persist_autoresearch_action_plan",
                    {
                        "run_id": run_id,
                        "branch_key": str(request["branch_key"]),
                        "iteration_no": iteration_no,
                        "agent_decision": proposed["agent_decision"],
                        "actions": proposed["actions"],
                        "planner_receipt": proposed.get("planner_receipt"),
                    },
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(minutes=20),
                    retry_policy=retry,
                )
                generated = await workflow.execute_activity(
                    "execute_autoresearch_action_batch",
                    {
                        "action_plan": action_plan,
                        "executor": request.get("action_executor") or {},
                    },
                    task_queue=action_queue,
                    start_to_close_timeout=timedelta(hours=12),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                children = await workflow.execute_activity(
                    "persist_autoresearch_children",
                    {"action_plan": action_plan, "generated": generated},
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )
                if bool(children.get("iteration_noop")):
                    rejected_duplicate_count = int(
                        children.get("rejected_duplicate_count", 0)
                    )
                    if (
                        int(children.get("candidate_count", -1)) != 0
                        or rejected_duplicate_count
                        != int(children.get("proposed_child_count", -1))
                        or not str(children.get("stop_reason") or "").strip()
                    ):
                        raise ValueError("AutoResearch duplicate no-op receipt is incomplete")
                    result = {
                        "schema_version": "ampgent.autoresearch-workflow-result.1",
                        "run_id": run_id,
                        "status": "iteration_noop",
                        "stop_reason": str(children["stop_reason"]),
                        "completed_iteration_no": iteration_no,
                        "checkpoint": None,
                        "duplicate_rejections": children.get("rejected_duplicates") or [],
                    }
                    await workflow.execute_activity(
                        "mark_run_succeeded",
                        {
                            "run_id": run_id,
                            "result_status": result["status"],
                            "durable_counts": {
                                "action_count": int(children["proposed_child_count"]),
                                "candidate_count": 0,
                                "rejected_duplicate_count": rejected_duplicate_count,
                                "evaluation_count": 0,
                                "metric_delta_count": 0,
                                "archive_version_count": 0,
                                "checkpoint_count": 0,
                                "replay_count": 0,
                            },
                        },
                        task_queue=control_queue,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry,
                    )
                    return result
                if int(children.get("candidate_count", 0)) == 0:
                    raise ValueError("AutoResearch empty child cohort lacks an explicit no-op")
                cohort = children.get("score_all_candidates")
                if cohort is None:
                    cohort = children["candidates"]
                if not cohort:
                    raise ValueError("AutoResearch unique child score-all cohort is empty")
                metric_receipts: list[dict[str, Any]] = []
                for plugin_name in plugin_names:
                    reference = await workflow.execute_activity(
                        "evaluate_v38_sequence_metric",
                        {
                            "run_id": run_id,
                            "generation": iteration_no + 1,
                            "stage": "autoresearch_score_all",
                            "plugin": request["metric_plugins_by_name"][plugin_name],
                            "candidates": cohort,
                        },
                        task_queue=metrics_queue,
                        start_to_close_timeout=timedelta(hours=12),
                        heartbeat_timeout=timedelta(minutes=5),
                        retry_policy=retry,
                    )
                    receipt = await workflow.execute_activity(
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
                    metric_receipts.append(receipt)
                evaluation_count = sum(int(item["evaluation_count"]) for item in metric_receipts)
                expected_count = len(cohort) * 12
                if evaluation_count != expected_count:
                    raise ValueError("AutoResearch score-all evaluation count drifted")

                latest_checkpoint = await workflow.execute_activity(
                    "finalize_autoresearch_iteration",
                    {
                        "run_id": run_id,
                        "branch_key": str(request["branch_key"]),
                        "iteration_no": iteration_no,
                        "action_plan": action_plan,
                        "children": children,
                        "execution_contract": contract,
                        "metric_tool_call_ids": [item["tool_call_id"] for item in metric_receipts],
                        "archive_policy": request["archive_policy"],
                        "continuation_policy": request["continuation_policy"],
                        "prior_consecutive_stagnant_generations": stagnant_generations,
                        "control_environment_sha256": request["control_environment_sha256"],
                    },
                    task_queue=control_queue,
                    start_to_close_timeout=timedelta(hours=2),
                    retry_policy=retry,
                )
                completed_in_this_execution += 1
                previous_checkpoint = latest_checkpoint
                continuation = latest_checkpoint["continuation"]
                stagnant_generations = int(continuation["consecutive_stagnant_generations"])
                if (
                    not bool(continuation["continue_required"])
                    or continuation["next_action"] == "freeze_successor_run"
                ):
                    result = {
                        "schema_version": "ampgent.autoresearch-workflow-result.1",
                        "run_id": run_id,
                        "status": (
                            "quality_goal_met"
                            if not bool(continuation["continue_required"])
                            else "successor_run_required"
                        ),
                        "completed_iteration_no": iteration_no,
                        "checkpoint": latest_checkpoint,
                    }
                    await workflow.execute_activity(
                        "mark_run_succeeded",
                        {
                            "run_id": run_id,
                            "result_status": result["status"],
                            "durable_counts": latest_checkpoint["durable_counts"],
                        },
                        task_queue=control_queue,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=retry,
                    )
                    return result
                iteration_no += 1

            continued = dict(request)
            continued.update(
                {
                    "start_iteration_no": iteration_no,
                    "initial_action_plan": None,
                    "previous_checkpoint": latest_checkpoint,
                    "prior_consecutive_stagnant_generations": stagnant_generations,
                    "workflow_chain_started": True,
                }
            )
            workflow.continue_as_new(continued)
            raise AssertionError("continue_as_new unexpectedly returned")
        except Exception as exc:
            await workflow.execute_activity(
                "mark_run_failed",
                {
                    "run_id": run_id,
                    "error_type": type(exc).__name__,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                task_queue=control_queue,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry,
            )
            raise


__all__ = ["AutoResearchClosedLoopWorkflow", "_validate_request"]
