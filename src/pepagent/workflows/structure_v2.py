from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from pepagent.provenance.hashing import sha256_json, sha256_text

STRUCTURE_V2_WORKFLOW_QUEUE = "pepagent-structure-v2-workflow"
STRUCTURE_V2_ROSETTA_QUEUE = "pepagent-structure-v2-rosetta"
STRUCTURE_V2_PERSIST_QUEUE = "pepagent-structure-v2-persist"
CONTROL_QUEUE = "pepagent-control"
BOLTZ_QUEUE = "pepagent-gpu-boltz2"
STRUCTURE_V2_WORKFLOW_TASK_TIMEOUT = timedelta(seconds=60)
STRUCTURE_V2_RECEIPT_GOAL = 50
STRUCTURE_V2_DG_THRESHOLD_REU = -50.0
STRUCTURE_V2_RECEIPT_CONTRACT_SCHEMA = "ampgent.structure-target-receipt-contract.2"
STRUCTURE_V2_ELIGIBILITY_SCHEMA = "ampgent.structure-v2-candidate-eligibility.1"
STRUCTURE_V2_PG_BINDING_SCHEMA = "ampgent.structure-v2-pg-binding.1"
_HEX = frozenset("0123456789abcdef")
_CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")


def structure_v2_receipt_contract() -> dict[str, Any]:
    return {
        "schema_version": STRUCTURE_V2_RECEIPT_CONTRACT_SCHEMA,
        "required_candidate_receipts": STRUCTURE_V2_RECEIPT_GOAL,
        "required_distinct_families": STRUCTURE_V2_RECEIPT_GOAL,
        "required_rosetta_receipts_per_candidate": 1,
        "complete_dG_required": True,
        "wetlab_stop_condition_metric": ("complete_dG_distinct_family_candidate_receipts"),
        "wetlab_stop_condition_operator": "==",
        "wetlab_stop_condition_count": STRUCTURE_V2_RECEIPT_GOAL,
        "structure_support_metric": "structure_support",
        "structure_support_interpretation": "independent_from_dG_threshold",
        "structure_support_is_stop_condition": False,
        "dG_threshold_metric": "rosetta_dG_separated_reu",
        "dG_threshold_operator": "<=",
        "dG_threshold_reu": STRUCTURE_V2_DG_THRESHOLD_REU,
        "dG_threshold_is_stop_condition": False,
    }


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and not (set(text) - _HEX)


def _validate_candidate_eligibility(
    candidate: Mapping[str, Any],
    *,
    target_key: str,
) -> str:
    try:
        uuid.UUID(str(candidate.get("id", "")))
    except (TypeError, ValueError) as error:
        raise ValueError("structure v2 candidate ID must be a UUID") from error
    sequence = "".join(str(candidate.get("sequence", "")).split()).upper()
    if (
        not sequence
        or sequence != candidate.get("sequence")
        or set(sequence) - _CANONICAL_AMINO_ACIDS
    ):
        raise ValueError("structure v2 candidate sequence is not normalized")
    if sha256_text(sequence) != candidate.get("sequence_sha256"):
        raise ValueError("structure v2 candidate sequence identity differs")
    if candidate.get("target_key") != target_key:
        raise ValueError("structure v2 candidate crossed target branches")
    family = str(candidate.get("family_key_80_80", ""))
    if not family:
        raise ValueError("structure v2 candidate lacks its 80/80 family")
    eligibility = candidate.get("eligibility")
    if not isinstance(eligibility, Mapping):
        raise ValueError("structure v2 candidate lacks its PG eligibility binding")
    if (
        eligibility.get("schema_version") != STRUCTURE_V2_ELIGIBILITY_SCHEMA
        or eligibility.get("target_key") != target_key
        or eligibility.get("sequence_sha256") != candidate.get("sequence_sha256")
        or eligibility.get("family_key_80_80") != family
        or eligibility.get("strict_display_eligible") is not True
    ):
        raise ValueError("structure v2 candidate eligibility identity differs")
    toxicity = str(eligibility.get("toxinpred3_label", "")).strip().lower().replace("_", "-")
    if toxicity not in {"non-toxin", "nontoxin"}:
        raise ValueError("structure v2 candidate fails the ToxinPred3 literal gate")
    if str(eligibility.get("macrel_hemolysis_label", "")).strip().lower() != "low":
        raise ValueError("structure v2 candidate fails the MACREL literal gate")
    instability = eligibility.get("guruprasad_instability_index")
    if (
        isinstance(instability, bool)
        or not isinstance(instability, (int, float))
        or not math.isfinite(float(instability))
        or float(instability) >= 50.0
        or eligibility.get("guruprasad_instability_ood") is not False
    ):
        raise ValueError("structure v2 candidate fails the Guruprasad gate")
    support = eligibility.get("activity_model_support_count")
    if isinstance(support, bool) or not isinstance(support, int) or not 2 <= support <= 3:
        raise ValueError("structure v2 candidate lacks activity-model support >=2")
    source = eligibility.get("source_evidence")
    if not isinstance(source, Mapping) or source.get("source_kind") != (
        "postgresql_frozen_strict_library_snapshot"
    ):
        raise ValueError("structure v2 candidate lacks authoritative PG source evidence")
    if source.get("pg_candidate_id") != candidate.get("id") or any(
        not _is_sha256(source.get(field))
        for field in (
            "cohort_sha256",
            "strict_library_sha256",
            "strict_library_row_sha256",
            "source_result_sha256",
            "pg_import_tool_output_sha256",
            "pg_candidate_generated_payload_sha256",
            "pg_structure_queued_payload_sha256",
        )
    ):
        raise ValueError("structure v2 candidate PG source evidence identity differs")
    for field in (
        "pg_import_tool_call_id",
        "pg_candidate_generated_event_id",
        "pg_structure_queued_event_id",
    ):
        try:
            uuid.UUID(str(source.get(field, "")))
        except (TypeError, ValueError) as error:
            raise ValueError(f"structure v2 candidate lacks its PG {field}") from error
    expected_sha256 = sha256_json(dict(eligibility))
    if candidate.get("eligibility_sha256") != expected_sha256:
        raise ValueError("structure v2 candidate eligibility digest differs")
    return expected_sha256


def validate_structure_v2_target_request(request: Mapping[str, Any]) -> None:
    if request.get("receipt_contract") != structure_v2_receipt_contract():
        raise ValueError("structure v2 target receipt contract is missing or drifted")
    candidates = request.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != STRUCTURE_V2_RECEIPT_GOAL:
        raise ValueError("structure v2 requires exactly 50 candidate receipts per target")
    if any(not isinstance(item, Mapping) for item in candidates):
        raise ValueError("structure v2 candidate list must contain objects")
    target_key = str(request.get("target_key", ""))
    if not target_key or target_key != target_key.lower():
        raise ValueError("structure v2 target key must be normalized lowercase")
    spec = request.get("spec")
    if not isinstance(spec, Mapping) or spec.get("target_key") != target_key:
        raise ValueError("structure v2 target key differs from its workflow spec")
    if spec.get("rosetta_all_boltz_samples") is not False or int(spec.get("rosetta_top_k", 0)) != 1:
        raise ValueError("structure v2 requires one Rosetta receipt per candidate")
    candidate_ids = [str(item.get("id", "")) for item in candidates]
    families = [str(item.get("family_key_80_80", "")) for item in candidates]
    if any(not value for value in candidate_ids) or len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("structure v2 target candidates must have distinct identities")
    if any(not value for value in families) or len(set(families)) != STRUCTURE_V2_RECEIPT_GOAL:
        raise ValueError("structure v2 target requires 50 distinct 80/80 families")
    eligibility_sha256s = [
        _validate_candidate_eligibility(item, target_key=target_key) for item in candidates
    ]
    binding = request.get("pg_eligibility_binding")
    if not isinstance(binding, Mapping):
        raise ValueError("structure v2 request lacks its PG eligibility binding")
    try:
        uuid.UUID(str(binding.get("target_id", "")))
    except (TypeError, ValueError) as error:
        raise ValueError("structure v2 PG target ID must be a UUID") from error
    if (
        binding.get("schema_version") != STRUCTURE_V2_PG_BINDING_SCHEMA
        or binding.get("source_database") != "postgresql"
        or binding.get("run_id") != request.get("run_id")
        or binding.get("target_key") != target_key
        or not _is_sha256(binding.get("target_sequence_sha256"))
        or binding.get("candidate_count") != STRUCTURE_V2_RECEIPT_GOAL
        or binding.get("distinct_family_count") != STRUCTURE_V2_RECEIPT_GOAL
        or int(binding.get("fresh_eligible_family_count", 0)) < STRUCTURE_V2_RECEIPT_GOAL
        or binding.get("candidate_eligibility_sha256s") != eligibility_sha256s
        or not _is_sha256(binding.get("legacy_exclusion_snapshot_sha256"))
    ):
        raise ValueError("structure v2 PG eligibility binding differs")
    unsigned_binding = dict(binding)
    observed_binding_sha256 = unsigned_binding.pop("binding_sha256", None)
    if observed_binding_sha256 != sha256_json(unsigned_binding):
        raise ValueError("structure v2 PG eligibility binding digest differs")


def _retry_policy() -> RetryPolicy:
    return RetryPolicy(
        initial_interval=timedelta(seconds=10),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(minutes=10),
        maximum_attempts=5,
    )


@workflow.defn(name="BulkCandidateEvaluationWorkflowV2")
class BulkCandidateEvaluationWorkflowV2:
    """Evaluate one candidate while keeping the Rosetta payload out of history."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        retry = _retry_policy()
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
                task_queue=CONTROL_QUEUE,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            return {
                "candidate_id": candidate["id"],
                "status": "failed",
                "stage": stage,
            }

        structures: list[dict[str, Any]] = []
        seed_count = int(request["spec"].get("boltz_seeds_per_candidate", 1))
        explicit_seeds = request["spec"].get("boltz_seed_values", [])
        structure_seeds = (
            [int(seed) for seed in explicit_seeds]
            if explicit_seeds
            else [int(request["seed"]) + index for index in range(seed_count)]
        )
        if len(structure_seeds) != seed_count or len(set(structure_seeds)) != seed_count:
            raise ValueError("Boltz structure seed contract is incomplete or non-unique")
        try:
            for structure_seed in structure_seeds:
                structure = await workflow.execute_activity(
                    "predict_boltz2_complex",
                    {
                        "run_id": request["run_id"],
                        "spec": request["spec"],
                        "candidate": candidate,
                        "seed": structure_seed,
                    },
                    task_queue=BOLTZ_QUEUE,
                    versioning_intent=workflow.VersioningIntent.DEFAULT,
                    start_to_close_timeout=timedelta(hours=6),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                structure = await workflow.execute_activity(
                    "persist_boltz2_evidence",
                    {"run_id": request["run_id"], "structure": structure},
                    task_queue=CONTROL_QUEUE,
                    start_to_close_timeout=timedelta(minutes=15),
                    retry_policy=retry,
                )
                structures.append(structure)
        except Exception as error:
            return await preserve_failure("boltz2", error)

        try:
            audit = await workflow.execute_activity(
                "audit_structure_ensemble",
                {
                    "run_id": request["run_id"],
                    "spec": request["spec"],
                    "generation": candidate["generation"],
                    "structures": structures,
                },
                task_queue=CONTROL_QUEUE,
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=retry,
            )
            audit = await workflow.execute_activity(
                "persist_interface_audit",
                {"run_id": request["run_id"], "audit_result": audit},
                task_queue=CONTROL_QUEUE,
                start_to_close_timeout=timedelta(minutes=15),
                retry_policy=retry,
            )
            all_samples = bool(request["spec"].get("rosetta_all_boltz_samples", False))
            if all_samples:
                rosetta_inputs = await workflow.execute_activity(
                    "select_rosetta_inputs",
                    {
                        "structures": structures,
                        "pair_iptm_min": 0.0,
                        "top_k": len(structures),
                    },
                    task_queue=CONTROL_QUEUE,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                rosetta_inputs = [
                    {
                        **item,
                        "interface_audit": audit["audit"],
                        "interface_audit_tool_call_id": audit["tool_call_id"],
                        "rosetta_selection_mode": "all_preregistered_boltz_samples",
                    }
                    for item in rosetta_inputs
                ]
            else:
                rosetta_inputs = await workflow.execute_activity(
                    "select_rosetta_inputs",
                    {
                        "ensembles": [audit],
                        "top_k": 1,
                        "exploratory_slots": 0,
                        "mode": "diagnostic_shadow",
                    },
                    task_queue=CONTROL_QUEUE,
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
            if not rosetta_inputs:
                raise RuntimeError("bulk candidate produced no usable coordinate structure")
        except Exception as error:
            return await preserve_failure("coordinate_audit", error)

        try:
            receipts: list[dict[str, Any]] = []
            for rosetta_input in rosetta_inputs:
                score_reference = await workflow.execute_activity(
                    "score_rosetta_complex_v2",
                    {
                        "run_id": request["run_id"],
                        "spec": request["spec"],
                        "structure": rosetta_input,
                        "seed": int(rosetta_input["input"]["seed"]) + 100_000_000,
                    },
                    task_queue=STRUCTURE_V2_ROSETTA_QUEUE,
                    versioning_intent=workflow.VersioningIntent.DEFAULT,
                    start_to_close_timeout=timedelta(hours=72),
                    heartbeat_timeout=timedelta(minutes=5),
                    retry_policy=retry,
                )
                receipt = await workflow.execute_activity(
                    "persist_rosetta_evidence_v2",
                    {
                        "run_id": request["run_id"],
                        "score_reference": score_reference,
                    },
                    task_queue=STRUCTURE_V2_PERSIST_QUEUE,
                    start_to_close_timeout=timedelta(minutes=5),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=retry,
                )
                receipts.append(receipt)
        except Exception as error:
            return await preserve_failure("rosetta", error)
        return {
            "candidate_id": candidate["id"],
            "family_key_80_80": candidate["family_key_80_80"],
            "status": "succeeded",
            "structure_receipt_count": len(structures),
            "rosetta_tool_call_ids": [item["tool_call_id"] for item in receipts],
            "rosetta_result_sha256": [item["result_sha256"] for item in receipts],
            "rosetta_receipts": receipts,
        }


@workflow.defn(name="CandidateStructureValidationWorkflowV2")
class CandidateStructureValidationWorkflowV2:
    """Run new structure children with an explicit 60-second WFT timeout."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        validate_structure_v2_target_request(request)
        retry = _retry_policy()
        try:
            pg_binding = await workflow.execute_activity(
                "preflight_structure_v2_target_request_v2",
                request,
                task_queue=STRUCTURE_V2_PERSIST_QUEUE,
                start_to_close_timeout=timedelta(minutes=2),
                heartbeat_timeout=timedelta(seconds=30),
                retry_policy=retry,
            )
            if pg_binding != request["pg_eligibility_binding"]:
                raise RuntimeError("structure v2 runtime PG binding differs from submission")
            await workflow.execute_activity(
                "mark_run_started",
                {"run_id": request["run_id"], "workflow_id": workflow.info().workflow_id},
                task_queue=CONTROL_QUEUE,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry,
            )
            results: list[dict[str, Any]] = []
            candidates = request["candidates"]
            concurrency = int(request["spec"].get("bulk_evaluation_concurrency", 1))
            for batch_start in range(0, len(candidates), concurrency):
                batch = candidates[batch_start : batch_start + concurrency]
                results.extend(
                    await asyncio.gather(
                        *(
                            workflow.execute_child_workflow(
                                "BulkCandidateEvaluationWorkflowV2",
                                {
                                    "run_id": request["run_id"],
                                    "spec": request["spec"],
                                    "candidate": candidate,
                                    "seed": int(request["spec"]["seed"])
                                    + (batch_start + offset) * 10_000,
                                },
                                id=(
                                    f"pepagent-structure-validation-v2-{request['run_id']}-"
                                    f"{candidate['id']}"
                                ),
                                task_queue=STRUCTURE_V2_WORKFLOW_QUEUE,
                                task_timeout=STRUCTURE_V2_WORKFLOW_TASK_TIMEOUT,
                            )
                            for offset, candidate in enumerate(batch)
                        )
                    )
                )
            if len(results) != STRUCTURE_V2_RECEIPT_GOAL or any(
                item.get("status") != "succeeded" for item in results
            ):
                raise RuntimeError(
                    "structure v2 target is incomplete before its 50 candidate receipts"
                )
            if any(
                not isinstance(result.get("rosetta_receipts"), list)
                or len(result["rosetta_receipts"]) != 1
                for result in results
            ):
                raise RuntimeError("structure v2 target lacks exactly one Rosetta receipt")
            receipts = [
                receipt for result in results for receipt in result.get("rosetta_receipts", [])
            ]
            if len({item["candidate_id"] for item in results}) != STRUCTURE_V2_RECEIPT_GOAL:
                raise RuntimeError("structure v2 target candidate receipts are not distinct")
            if len({item["family_key_80_80"] for item in results}) != STRUCTURE_V2_RECEIPT_GOAL:
                raise RuntimeError("structure v2 target family receipts are not distinct")
            for result in results:
                for receipt in result["rosetta_receipts"]:
                    primary_dg = receipt.get("primary_dG_separated_reu")
                    support = receipt.get("structure_support")
                    if (
                        receipt.get("candidate_id") != result["candidate_id"]
                        or not isinstance(receipt.get("tool_call_id"), str)
                        or not receipt["tool_call_id"]
                        or not isinstance(receipt.get("result_sha256"), str)
                        or len(receipt["result_sha256"]) != 64
                        or receipt.get("candidate_status") != "rosetta_scored"
                        or int(receipt.get("evaluation_count", 0)) < 8
                        or int(receipt.get("artifact_edge_count", 0)) < 3
                        or not isinstance(primary_dg, (int, float))
                        or not math.isfinite(float(primary_dg))
                        or not isinstance(support, str)
                        or not support
                        or not isinstance(receipt.get("dG_le_minus_50"), bool)
                        or receipt["dG_le_minus_50"]
                        != (float(primary_dg) <= STRUCTURE_V2_DG_THRESHOLD_REU)
                    ):
                        raise RuntimeError("structure v2 target lacks complete dG/support receipts")
            support_counts: dict[str, int] = {}
            for receipt in receipts:
                label = str(receipt["structure_support"])
                support_counts[label] = support_counts.get(label, 0) + 1
            receipt_summary = {
                **structure_v2_receipt_contract(),
                "complete_dG_candidate_receipts": len(results),
                "complete_rosetta_receipts": len(receipts),
                "distinct_family_receipts": len({item["family_key_80_80"] for item in results}),
                "structure_support_counts": support_counts,
                "dG_le_minus_50_count": sum(
                    bool(receipt["dG_le_minus_50"]) for receipt in receipts
                ),
                "wetlab_stop_condition_met": len(results) == STRUCTURE_V2_RECEIPT_GOAL,
            }
            await workflow.execute_activity(
                "finalize_run",
                {
                    "run_id": request["run_id"],
                    "structures": [],
                    "rosetta_results": [],
                    "persisted_structure_count": sum(
                        int(item.get("structure_receipt_count", 0)) for item in results
                    ),
                    "persisted_rosetta_receipt_count": len(receipts),
                    "rosetta_receipt_summary": receipt_summary,
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
                task_queue=CONTROL_QUEUE,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            return {
                "run_id": request["run_id"],
                "results": results,
                "receipt_contract": receipt_summary,
            }
        except asyncio.CancelledError:
            await asyncio.shield(
                workflow.execute_activity(
                    "mark_run_cancelled",
                    {"run_id": request["run_id"], "reason": "workflow_cancelled"},
                    task_queue=CONTROL_QUEUE,
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
                task_queue=CONTROL_QUEUE,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry,
            )
            raise


@workflow.defn(name="StructureEvidenceRepairWorkflowV2")
class StructureEvidenceRepairWorkflowV2:
    """Reference predecessor evidence without rewriting its workflow history."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict[str, Any]:
        predecessor = request.get("predecessor")
        if not isinstance(predecessor, dict) or not all(
            predecessor.get(field) for field in ("workflow_id", "run_id", "activity_id", "reason")
        ):
            raise ValueError("repair successor requires its exact predecessor and reason")
        receipt = await workflow.execute_activity(
            "persist_rosetta_evidence_v2",
            {
                "run_id": request["run_id"],
                "score_reference": request["score_reference"],
                "predecessor": predecessor,
            },
            task_queue=STRUCTURE_V2_PERSIST_QUEUE,
            start_to_close_timeout=timedelta(minutes=5),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=_retry_policy(),
        )
        return {"receipt": receipt, "predecessor": predecessor}


__all__ = [
    "BulkCandidateEvaluationWorkflowV2",
    "CandidateStructureValidationWorkflowV2",
    "StructureEvidenceRepairWorkflowV2",
    "STRUCTURE_V2_PERSIST_QUEUE",
    "STRUCTURE_V2_RECEIPT_CONTRACT_SCHEMA",
    "STRUCTURE_V2_RECEIPT_GOAL",
    "STRUCTURE_V2_ROSETTA_QUEUE",
    "STRUCTURE_V2_WORKFLOW_QUEUE",
    "STRUCTURE_V2_WORKFLOW_TASK_TIMEOUT",
    "structure_v2_receipt_contract",
    "validate_structure_v2_target_request",
]
