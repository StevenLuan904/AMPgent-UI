from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, model_validator

from pepagent.provenance.hashing import sha256_bytes, sha256_json


class CapacityFormalRun(BaseModel):
    execution_authorized: bool
    submitted: bool


class V34CapacityContract(BaseModel):
    capacity_contract_id: Literal["acea_v34_execution_capacity"]
    version: Literal["v34-capacity-v1"]
    execution_status: Literal["preregistered_not_authorized"]
    scope: dict[str, Any]
    gpu_capacity: dict[str, Any]
    cpu_capacity: dict[str, Any]
    fair_scheduler: dict[str, Any]
    retry_contract: dict[str, Any]
    database_evidence: dict[str, Any]
    formal_run: CapacityFormalRun

    @model_validator(mode="after")
    def validate_contract(self) -> V34CapacityContract:
        scope = self.scope
        expected_counts = {
            "expected_parent_count": 24,
            "expected_arm_count": 4,
            "expected_episode_count": 96,
            "maximum_retained_proposals_per_episode": 4,
            "structure_poses_per_retained_proposal": 1,
            "rosetta_decoys_per_pose": 8,
            "maximum_boltz_poses": 384,
            "maximum_rosetta_decoys": 3072,
        }
        if scope.get("benchmark_id") != "amp_knowledge_pepshot_ablation_v34":
            raise ValueError("v34 capacity benchmark identity drifted")
        if any(scope.get(key) != value for key, value in expected_counts.items()):
            raise ValueError("v34 capacity budget drifted")

        gpu = self.gpu_capacity
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
            raise ValueError("v34 eligible GPU placement set drifted")
        prohibited = {
            (str(item.get("host")), str(item.get("gpu_index")))
            for item in gpu.get("prohibited_placements", [])
        }
        if ("192.168.99.32", "any") not in prohibited or (
            "192.168.99.19",
            "4",
        ) not in prohibited:
            raise ValueError("v34 prohibited GPU placement set is incomplete")
        required_gpu_guards = (
            "one_worker_process_per_physical_gpu",
            "gpu_oversubscription_forbidden",
            "dynamic_worker_addition_during_formal_run_forbidden",
        )
        if gpu.get("maximum_concurrent_workers") != 3 or gpu.get(
            "activity_slots_per_worker"
        ) != 1:
            raise ValueError("v34 Boltz concurrency drifted")
        if not all(gpu.get(key) is True for key in required_gpu_guards):
            raise ValueError("v34 GPU capacity guards are incomplete")
        required_placement_gates = {
            "physical_host_gpu_pid_role_identity_exact",
            "active_release_and_source_revision_exact",
            "task_queue_and_environment_exact",
            "no_foreign_or_unowned_process_on_device",
            "no_active_workflow_during_worker_topology_change",
        }
        if set(gpu.get("placement_gates", [])) != required_placement_gates:
            raise ValueError("v34 GPU placement gates drifted")

        cpu = self.cpu_capacity
        if (
            cpu.get("fixed_concurrent_activity_slots") != 16
            or cpu.get("threads_per_activity") != 1
            or cpu.get("require_verified_free_logical_cores_at_least") != 16
        ):
            raise ValueError("v34 Rosetta CPU concurrency drifted")
        if cpu.get("OMP_NUM_THREADS") != 1 or cpu.get("MKL_NUM_THREADS") != 1:
            raise ValueError("v34 Rosetta thread caps drifted")
        if cpu.get("topology_change_during_formal_run_forbidden") is not True:
            raise ValueError("v34 Rosetta topology may not change during a formal run")

        scheduler = self.fair_scheduler
        if scheduler.get("queue_sort_fields") != ["arm_order", "parent_order"]:
            raise ValueError("v34 fair queue order drifted")
        required_scheduler_guards = (
            "structure_queue_inherits_episode_order",
            "no_arm_or_provider_priority",
            "no_cross_arm_memory",
            "completed_or_failed_episode_never_refilled_or_substituted",
        )
        if scheduler.get("arm_identity_visible_to_scheduler") is not False:
            raise ValueError("v34 scheduler can observe sealed arm identity")
        if scheduler.get("maximum_inflight_episodes_per_parent") != 1:
            raise ValueError("v34 permits concurrent arms from one parent")
        if scheduler.get("maximum_inflight_episodes") != 24:
            raise ValueError("v34 episode concurrency drifted")
        if not all(scheduler.get(key) is True for key in required_scheduler_guards):
            raise ValueError("v34 scheduler guards are incomplete")

        retry = self.retry_contract
        if retry.get("maximum_attempts_per_logical_tool_call") != 2:
            raise ValueError("v34 retry budget drifted")
        required_retry_guards = (
            "same_input_seed_protocol_and_idempotency_key_required",
            "reseeding_forbidden",
            "refill_or_substitution_forbidden",
            "retry_does_not_create_additional_scientific_observation",
        )
        if not all(retry.get(key) is True for key in required_retry_guards):
            raise ValueError("v34 retry guards are incomplete")
        if retry.get("recovery_order") != [
            "database_idempotency_lookup",
            "object_store_content_hash_lookup",
            "retry_same_logical_call_only_if_no_committed_success_exists",
        ]:
            raise ValueError("v34 idempotent recovery order drifted")
        if retry.get("exhausted_retry_policy") != (
            "fail_formal_completion_without_replacement_run"
        ):
            raise ValueError("v34 exhausted retries must fail closed")

        evidence = self.database_evidence
        required_evidence = (
            "PostgreSQL_is_authoritative",
            "object_store_is_content_addressed",
            "capacity_contract_artifact_required",
            "worker_placement_snapshot_artifact_required",
            "scheduler_queue_manifest_artifact_required",
            "attempt_ledger_artifact_required",
            "persist_every_attempt_as_typed_lifecycle_event",
            "persist_worker_host_gpu_pid_role_release_and_environment",
            "persist_queue_position_dispatch_start_finish_and_outcome",
            "persist_retry_classification_and_idempotency_recovery",
            "database_object_store_only_replay_required",
            "local_logs_csv_and_markdown_are_exports_only",
        )
        if not all(evidence.get(key) is True for key in required_evidence):
            raise ValueError("v34 capacity evidence contract is incomplete")
        if self.formal_run.execution_authorized or self.formal_run.submitted:
            raise ValueError("v34 capacity contract cannot authorize execution")
        return self


def load_v34_capacity_contract(path: Path) -> V34CapacityContract:
    return V34CapacityContract.model_validate(
        yaml.safe_load(path.read_text(encoding="utf-8"))
    )


def build_v34_fair_episode_queue(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the sealed, arm-agnostic 96-episode dispatch order."""
    if len(episodes) != 96:
        raise ValueError("v34 capacity queue requires exactly 96 episodes")
    identities = {
        (int(item["parent_order"]), int(item["arm_order"])) for item in episodes
    }
    expected = {(parent, arm) for parent in range(1, 25) for arm in range(1, 5)}
    if identities != expected:
        raise ValueError("v34 capacity queue episode grid drifted")
    if len(identities) != len(episodes):
        raise ValueError("v34 capacity queue contains duplicate episodes")
    queue = sorted(
        episodes,
        key=lambda item: (int(item["arm_order"]), int(item["parent_order"])),
    )
    return [
        {
            "queue_position": position,
            "parent_order": int(item["parent_order"]),
            "arm_order": int(item["arm_order"]),
            "parent_id": str(item["parent_id"]),
            "opaque_label": str(item["opaque_label"]),
        }
        for position, item in enumerate(queue, start=1)
    ]


def build_v34_static_capacity_preflight(
    *, contract_path: Path, episodes: list[dict[str, Any]]
) -> dict[str, Any]:
    """Freeze a static capacity plan without inspecting hosts or authorizing work."""
    contract = load_v34_capacity_contract(contract_path)
    queue = build_v34_fair_episode_queue(episodes)
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "capacity_contract_sha256": sha256_bytes(contract_path.read_bytes()),
        "queue_manifest_sha256": sha256_json(queue),
        "episode_count": len(queue),
        "maximum_boltz_workers": contract.gpu_capacity["maximum_concurrent_workers"],
        "rosetta_activity_slots": contract.cpu_capacity[
            "fixed_concurrent_activity_slots"
        ],
        "host_or_process_observation_performed": False,
        "remote_process_started_or_stopped": False,
        "formal_run_authorized": False,
        "formal_run_submitted": False,
        "remaining_dynamic_gates": list(contract.gpu_capacity["placement_gates"]),
    }
    result["static_preflight_sha256"] = sha256_json(result)
    return result

