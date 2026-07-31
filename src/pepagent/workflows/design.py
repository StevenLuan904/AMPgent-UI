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
            generated = await workflow.execute_activity(
                "generate_with_pepmlm",
                request,
                task_queue="pepagent-gpu-pepmlm",
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            selected = await workflow.execute_activity(
                "persist_and_select_candidates",
                {"run_id": request["run_id"], "generated": generated, "spec": request["spec"]},
                task_queue="pepagent-control",
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            structures: list[dict[str, Any]] = []
            for candidate in selected:
                structure = await workflow.execute_activity(
                    "predict_boltz2_complex",
                    {"run_id": request["run_id"], "spec": request["spec"], "candidate": candidate},
                    task_queue="pepagent-gpu-boltz2",
                    start_to_close_timeout=timedelta(hours=4),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                structure = await workflow.execute_activity(
                    "persist_boltz2_evidence",
                    {"run_id": request["run_id"], "structure": structure},
                    task_queue="pepagent-control",
                    start_to_close_timeout=timedelta(minutes=10),
                    retry_policy=retry,
                )
                structures.append(structure)
            rosetta_results: list[dict[str, Any]] = []
            if request["spec"].get("rosetta_enabled", False):
                rosetta_inputs = await workflow.execute_activity(
                    "select_rosetta_inputs",
                    {
                        "structures": structures,
                        "pair_iptm_min": request["spec"].get(
                            "rosetta_pair_iptm_min", 0.5
                        ),
                        "top_k": request["spec"].get("rosetta_top_k", 1),
                    },
                    task_queue="pepagent-control",
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                for structure in rosetta_inputs:
                    rosetta_result = await workflow.execute_activity(
                        "score_rosetta_complex",
                        {
                            "run_id": request["run_id"],
                            "spec": request["spec"],
                            "structure": structure,
                        },
                        task_queue="pepagent-cpu-rosetta",
                        start_to_close_timeout=timedelta(hours=48),
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
                    rosetta_results.append(rosetta_result)
            return await workflow.execute_activity(
                "finalize_run",
                {
                    "run_id": request["run_id"],
                    "structures": structures,
                    "rosetta_results": rosetta_results,
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
