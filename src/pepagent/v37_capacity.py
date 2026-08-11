from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, model_validator

from pepagent.provenance.hashing import sha256_bytes, sha256_json

V37_PIPELINE_STAGES = ("proposal", "evaluation", "boltz", "rosetta")


class CapacityFormalRun(BaseModel):
    execution_authorized: bool
    submitted: bool


class V37CapacityContract(BaseModel):
    capacity_contract_id: Literal["acea_v37_rapid_champion_capacity"]
    version: Literal["v37-capacity-v1"]
    execution_status: Literal["preregistered_not_authorized"]
    scope: dict[str, Any]
    resource_capacity: dict[str, Any]
    pipeline_contract: dict[str, Any]
    retry_contract: dict[str, Any]
    database_evidence: dict[str, Any]
    formal_run: CapacityFormalRun

    @model_validator(mode="after")
    def validate_contract(self) -> V37CapacityContract:
        scope = self.scope
        required_scope_guards = (
            "capacity_contract_does_not_set_scientific_budget",
            "adaptive_budget_growth_forbidden",
            "fixed_budget_completion_required_for_final_champion",
            "interim_progress_is_not_final_champion_evidence",
        )
        if scope.get("track") != "single_arm_rapid_champion_generation":
            raise ValueError("v37 capacity track drifted")
        if scope.get("scientific_budget_source") != ("separately_frozen_v37_benchmark_required"):
            raise ValueError("v37 capacity contract is missing its science budget boundary")
        if not all(scope.get(key) is True for key in required_scope_guards):
            raise ValueError("v37 capacity scope guards are incomplete")

        resources = self.resource_capacity
        gpu = resources.get("gpu", {})
        expected_eligible = {
            ("192.168.99.19", 5),
            ("synth", 5),
            ("synth", 6),
        }
        eligible = {
            (str(item.get("host")), item.get("gpu_index"))
            for item in gpu.get("eligible_placements", [])
        }
        if eligible != expected_eligible:
            raise ValueError("v37 eligible GPU placement set drifted")
        prohibited = {
            (str(item.get("host")), str(item.get("gpu_index")))
            for item in gpu.get("prohibited_placements", [])
        }
        if ("192.168.99.32", "any") not in prohibited or (
            "192.168.99.19",
            "4",
        ) not in prohibited:
            raise ValueError("v37 prohibited GPU placement set is incomplete")
        if gpu.get("maximum_concurrent_workers") != 3 or gpu.get("activity_slots_per_worker") != 1:
            raise ValueError("v37 Boltz capacity drifted")
        required_gpu_guards = (
            "one_worker_process_per_physical_gpu",
            "gpu_oversubscription_forbidden",
            "topology_change_during_run_forbidden",
        )
        if not all(gpu.get(key) is True for key in required_gpu_guards):
            raise ValueError("v37 GPU guards are incomplete")
        required_placement_gates = {
            "physical_host_gpu_pid_role_identity_exact",
            "active_release_and_source_revision_exact",
            "task_queue_environment_and_model_weights_exact",
            "no_foreign_or_unowned_process_on_device",
            "no_active_workflow_during_worker_topology_change",
        }
        if set(gpu.get("placement_gates", [])) != required_placement_gates:
            raise ValueError("v37 GPU placement gates drifted")

        cpu = resources.get("cpu", {})
        expected_cpu = {
            "proposal_activity_slots": 8,
            "cheap_evaluation_activity_slots": 16,
            "rosetta_activity_slots": 16,
            "rosetta_threads_per_activity": 1,
            "require_verified_free_logical_cores_at_least": 16,
            "OMP_NUM_THREADS": 1,
            "MKL_NUM_THREADS": 1,
        }
        if any(cpu.get(key) != value for key, value in expected_cpu.items()):
            raise ValueError("v37 CPU capacity drifted")
        if cpu.get("topology_change_during_run_forbidden") is not True:
            raise ValueError("v37 CPU topology may not change during a run")

        pipeline = self.pipeline_contract
        if tuple(pipeline.get("order", [])) != V37_PIPELINE_STAGES:
            raise ValueError("v37 pipeline stage order drifted")
        if pipeline.get("scheduling_key") != "proposal_ordinal":
            raise ValueError("v37 pipeline scheduling key drifted")
        if pipeline.get("scheduling_policy") != "deterministic_fifo_single_arm":
            raise ValueError("v37 pipeline scheduling policy drifted")
        if pipeline.get("arm_or_provider_stratification_used") is not False:
            raise ValueError("v37 single-arm pipeline cannot stratify by arm")
        expected_concurrency = {
            "proposal": 8,
            "evaluation": 16,
            "boltz": 3,
            "rosetta": 16,
        }
        expected_queues = {
            "proposal": 32,
            "evaluation": 64,
            "boltz": 12,
            "rosetta": 64,
        }
        stages = pipeline.get("stages", {})
        if set(stages) != set(V37_PIPELINE_STAGES):
            raise ValueError("v37 pipeline stage set drifted")
        for stage in V37_PIPELINE_STAGES:
            if stages[stage].get("concurrent_activity_slots") != expected_concurrency[stage]:
                raise ValueError(f"v37 {stage} concurrency drifted")
            if stages[stage].get("maximum_queued_items") != expected_queues[stage]:
                raise ValueError(f"v37 {stage} queue bound drifted")
        required_pipeline_guards = (
            "bounded_backpressure_required",
            "stage_handoff_requires_committed_database_manifest",
            "downstream_may_not_read_local_uncommitted_output",
            "candidate_or_occurrence_substitution_forbidden",
            "adaptive_early_stop_from_interim_scores_forbidden",
        )
        if not all(pipeline.get(key) is True for key in required_pipeline_guards):
            raise ValueError("v37 pipeline guards are incomplete")
        if pipeline.get("queue_overflow_policy") != (
            "pause_upstream_dispatch_without_dropping_or_reordering"
        ):
            raise ValueError("v37 pipeline queue overflow policy drifted")

        retry = self.retry_contract
        if retry.get("maximum_attempts_per_logical_stage_call") != 2:
            raise ValueError("v37 retry budget drifted")
        if retry.get("recovery_order") != [
            "database_idempotency_lookup",
            "object_store_content_hash_lookup",
            "retry_same_logical_stage_call_only_if_no_committed_success_exists",
        ]:
            raise ValueError("v37 idempotent recovery order drifted")
        required_retry_guards = (
            "same_input_seed_protocol_and_idempotency_key_required",
            "reseeding_forbidden",
            "refill_or_substitution_forbidden",
            "retry_does_not_create_additional_scientific_observation",
        )
        if not all(retry.get(key) is True for key in required_retry_guards):
            raise ValueError("v37 retry guards are incomplete")
        if retry.get("exhausted_retry_policy") != (
            "mark_item_failed_without_replacement_and_fail_final_completion_gate"
        ):
            raise ValueError("v37 exhausted retries must fail final completion")

        evidence = self.database_evidence
        required_evidence = (
            "PostgreSQL_is_authoritative",
            "object_store_is_content_addressed",
            "capacity_contract_artifact_required",
            "worker_placement_snapshot_artifact_required",
            "pipeline_manifest_artifact_required",
            "pipeline_queue_and_transition_ledger_artifact_required",
            "attempt_ledger_artifact_required",
            "persist_every_attempt_as_typed_lifecycle_event",
            "persist_worker_host_gpu_pid_role_release_environment_and_weights",
            "persist_every_stage_input_output_manifest_and_dependency",
            "persist_queue_position_dispatch_start_finish_outcome_and_backpressure",
            "persist_retry_classification_and_idempotency_recovery",
            "persist_interim_exports_as_nondecision_artifacts",
            "database_object_store_only_replay_required",
            "local_logs_csv_and_markdown_are_exports_only",
        )
        if not all(evidence.get(key) is True for key in required_evidence):
            raise ValueError("v37 database evidence contract is incomplete")
        if self.formal_run.execution_authorized or self.formal_run.submitted:
            raise ValueError("v37 capacity contract cannot authorize execution")
        return self


def load_v37_capacity_contract(path: Path) -> V37CapacityContract:
    return V37CapacityContract.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def build_v37_pipeline_manifest(
    proposal_occurrences: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Freeze a deterministic single-arm stage graph without executing any stage."""
    if not proposal_occurrences:
        raise ValueError("v37 pipeline requires a non-empty frozen proposal occurrence list")
    ordinals = [int(item["proposal_ordinal"]) for item in proposal_occurrences]
    if ordinals != list(range(1, len(ordinals) + 1)):
        raise ValueError("v37 proposal ordinals must be contiguous and pre-sorted")
    occurrence_ids = [str(item["occurrence_id"]) for item in proposal_occurrences]
    if len(occurrence_ids) != len(set(occurrence_ids)):
        raise ValueError("v37 proposal occurrence identities must be unique")

    items = []
    dependencies = []
    for occurrence in proposal_occurrences:
        ordinal = int(occurrence["proposal_ordinal"])
        occurrence_id = str(occurrence["occurrence_id"])
        stage_ids = {stage: f"v37:{occurrence_id}:{stage}" for stage in V37_PIPELINE_STAGES}
        items.append(
            {
                "proposal_ordinal": ordinal,
                "occurrence_id": occurrence_id,
                "stage_logical_ids": stage_ids,
            }
        )
        dependencies.extend(
            [stage_ids[parent], stage_ids[child]]
            for parent, child in zip(V37_PIPELINE_STAGES, V37_PIPELINE_STAGES[1:], strict=False)
        )
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "track": "single_arm_rapid_champion_generation",
        "stage_order": list(V37_PIPELINE_STAGES),
        "scheduling_key": "proposal_ordinal",
        "items": items,
        "dependencies": dependencies,
    }
    manifest["pipeline_manifest_sha256"] = sha256_json(manifest)
    return manifest


def build_v37_static_capacity_preflight(*, contract_path: Path) -> dict[str, Any]:
    """Build a no-host-touch capacity record that cannot authorize execution."""
    contract = load_v37_capacity_contract(contract_path)
    pipeline = contract.pipeline_contract
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "capacity_contract_sha256": sha256_bytes(contract_path.read_bytes()),
        "pipeline_stage_order": list(V37_PIPELINE_STAGES),
        "stage_concurrency": {
            stage: pipeline["stages"][stage]["concurrent_activity_slots"]
            for stage in V37_PIPELINE_STAGES
        },
        "maximum_boltz_workers": contract.resource_capacity["gpu"]["maximum_concurrent_workers"],
        "host_or_process_observation_performed": False,
        "remote_process_started_or_stopped": False,
        "formal_run_authorized": False,
        "formal_run_submitted": False,
        "remaining_dynamic_gates": list(contract.resource_capacity["gpu"]["placement_gates"]),
    }
    result["static_preflight_sha256"] = sha256_json(result)
    return result
