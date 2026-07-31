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
            return await workflow.execute_activity(
                "finalize_run",
                {"run_id": request["run_id"], "structures": structures},
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
