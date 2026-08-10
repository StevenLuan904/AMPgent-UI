from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn(name="MultiobjectivePortfolioWorkflow")
class MultiobjectivePortfolioWorkflow:
    """Single-entry v32 generation, metric, portfolio, and DB replay workflow."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=10),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(minutes=10),
            maximum_attempts=3,
        )
        run_id = request["run_id"]
        manifest = request["manifest"]
        try:
            await workflow.execute_activity(
                "mark_run_started",
                {"run_id": run_id, "workflow_id": workflow.info().workflow_id},
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry,
            )
            all_candidates: list[dict[str, Any]] = []
            for seed in manifest["seeds"]:
                generated = await workflow.execute_activity(
                    "generate_amp_designer_v32",
                    {"run_id": run_id, "seed": seed, "manifest": manifest},
                    task_queue="pepagent-cpu-portfolio",
                    versioning_intent=workflow.VersioningIntent.DEFAULT,
                    start_to_close_timeout=timedelta(hours=8),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                persisted = await workflow.execute_activity(
                    "persist_v32_generation_batch",
                    {"run_id": run_id, "manifest": manifest, "generated": generated},
                    task_queue="pepagent-control",
                    start_to_close_timeout=timedelta(minutes=30),
                    retry_policy=retry,
                )
                all_candidates.extend(persisted["candidates"])
            for plugin in manifest["metric_plugins"]:
                metric_result = await workflow.execute_activity(
                    "evaluate_optional_sequence_metric",
                    {
                        "run_id": run_id,
                        "generation": 0,
                        "stage": "v32_formal",
                        "plugin": plugin,
                        "candidates": all_candidates,
                    },
                    task_queue="pepagent-cpu-metrics",
                    versioning_intent=workflow.VersioningIntent.DEFAULT,
                    start_to_close_timeout=timedelta(hours=12),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                await workflow.execute_activity(
                    "persist_optional_sequence_metric",
                    {
                        "run_id": run_id,
                        "generation": 0,
                        "plugin": plugin,
                        "candidates": all_candidates,
                        "metric_result": metric_result,
                    },
                    task_queue="pepagent-control",
                    start_to_close_timeout=timedelta(hours=1),
                    retry_policy=retry,
                )
            decision = await workflow.execute_activity(
                "persist_v32_portfolio_decision",
                {"run_id": run_id, "manifest": manifest},
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=retry,
            )
            replay = await workflow.execute_activity(
                "persist_v32_replay_bundle",
                {
                    "run_id": run_id,
                    "manifest": manifest,
                    "portfolio": decision["portfolio"],
                    "selection_tool_call_id": decision["selection_tool_call_id"],
                },
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(hours=1),
                retry_policy=retry,
            )
            await workflow.execute_activity(
                "finalize_run",
                {
                    "run_id": run_id,
                    "structures": [],
                    "generation_count": 1,
                    "agent_decision_count": 1,
                    "bulk_rosetta_count": 0,
                    "bulk_csv": None,
                },
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            return {
                "run_id": run_id,
                "candidate_count": len(all_candidates),
                "portfolio_sha256": decision["portfolio_sha256"],
                "replay_bundle_sha256": replay["replay_bundle_sha256"],
                "exact_replay": replay["exact_replay"],
            }
        except Exception as error:
            await workflow.execute_activity(
                "mark_run_failed",
                {
                    "run_id": run_id,
                    "error_type": type(error).__name__,
                    "error": str(error),
                },
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            raise
