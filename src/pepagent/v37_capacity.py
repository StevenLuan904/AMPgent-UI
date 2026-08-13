from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, model_validator

from pepagent.provenance.hashing import sha256_bytes, sha256_json

V37_PIPELINE_STAGES = ("proposal", "evaluation", "boltz", "rosetta")
V37_CAPACITY_ARTIFACT_ROLES = (
    "worker_placement_snapshot",
    "pipeline_manifest",
    "pipeline_queue_transition_ledger",
)
V37_REQUIRED_WORKER_ROLE_BY_QUEUE_KEY = {
    "workflow_and_control": "v37-control",
    "generator": "v37-generator",
    "provider": "v37-provider",
    "sequence_metrics": "metrics",
    "boltz": "boltz2",
    "rosetta": "rosetta",
}
V37_DEFAULT_TASK_QUEUES = {
    "workflow_and_control": "pepagent-control-v37",
    "generator": "pepagent-generator-v37",
    "provider": "pepagent-provider-v37",
    "sequence_metrics": "pepagent-cpu-metrics",
    "boltz": "pepagent-gpu-boltz2",
    "rosetta": "pepagent-cpu-rosetta",
}
V37_STAGE_ACTIVITY_CONTRACT = {
    "proposal": ("generate_v37_batch", "generator", 1),
    "evaluation": ("evaluate_v37_sequence_metric", "sequence_metrics", 5),
    "boltz": ("predict_v37_boltz2_complex", "boltz", 3),
    "rosetta": ("score_v37_rosetta_complex", "rosetta", 3),
}


class CapacityFormalRun(BaseModel):
    execution_authorized: bool
    submitted: bool


class V37CapacityContract(BaseModel):
    capacity_contract_id: Literal["acea_v37_rapid_champion_capacity"]
    version: Literal["v37-capacity-v2"]
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
            ("192.168.99.19", 4),
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
        if not {
            ("192.168.99.32", "2"),
            ("192.168.99.32", "3"),
        } <= prohibited:
            raise ValueError("v37 prohibited GPU placement set is incomplete")
        if gpu.get("maximum_concurrent_workers") != 4 or gpu.get("activity_slots_per_worker") != 1:
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
            "boltz": 4,
            "rosetta": 16,
        }
        expected_queues = {
            "proposal": 32,
            "evaluation": 64,
            "boltz": 16,
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


def validate_v37_worker_placement_snapshot(
    payload: dict[str, Any],
    *,
    contract: V37CapacityContract | None = None,
    expected_task_queues: dict[str, str] | None = None,
    expected_source_revision: str | None = None,
    reference_time: datetime | None = None,
    maximum_age_seconds: int = 300,
) -> dict[str, Any]:
    """Validate the exact launch-boundary worker placement evidence."""
    required = {
        "schema_version",
        "captured_at",
        "active_workflow_count",
        "topology_frozen_for_run",
        "placements",
    }
    if set(payload) != required or payload.get("schema_version") != (
        "v37.worker-placement-snapshot.1"
    ):
        raise ValueError("v37 worker placement snapshot schema drifted")
    if payload.get("topology_frozen_for_run") is not True:
        raise ValueError("v37 worker topology is not frozen")
    if int(payload.get("active_workflow_count", -1)) != 0:
        raise ValueError("v37 worker placement snapshot was not captured at zero active workflows")
    try:
        captured_at = datetime.fromisoformat(
            str(payload["captured_at"]).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise ValueError("v37 worker placement capture timestamp is invalid") from error
    if captured_at.tzinfo is None:
        raise ValueError("v37 worker placement capture timestamp lacks timezone")
    captured_at = captured_at.astimezone(UTC)
    if reference_time is not None:
        now = reference_time.astimezone(UTC)
        age = (now - captured_at).total_seconds()
        if age < -30 or age > maximum_age_seconds:
            raise ValueError("v37 worker placement snapshot is stale or future-dated")
    placements = payload.get("placements")
    if not isinstance(placements, list) or not placements:
        raise ValueError("v37 worker placement snapshot is empty")
    required_placement = {
        "physical_host",
        "gpu_index",
        "pid",
        "role",
        "task_queue",
        "poller_identity",
        "poller_last_access_at",
        "source_revision",
        "release_sha256",
        "environment_sha256",
        "weights_sha256",
        "ampgent_owned",
        "foreign_process_present",
    }
    eligible = (
        {
            (str(item["host"]), int(item["gpu_index"]))
            for item in contract.resource_capacity["gpu"]["eligible_placements"]
        }
        if contract is not None
        else {
            ("192.168.99.19", 4),
            ("192.168.99.19", 5),
            ("synth", 5),
            ("synth", 6),
        }
    )
    gpu_placements: set[tuple[str, int]] = set()
    identities: set[tuple[str, int, str]] = set()
    queue_roles: dict[str, set[str]] = {}
    for item in placements:
        if not isinstance(item, dict) or set(item) != required_placement:
            raise ValueError("v37 worker placement row schema drifted")
        if item["ampgent_owned"] is not True or item["foreign_process_present"] is not False:
            raise ValueError("v37 worker placement is unsafe or unowned")
        if int(item["pid"]) < 1 or any(
            not isinstance(item[field], str) or not item[field].strip()
            for field in (
                "physical_host",
                "role",
                "task_queue",
                "poller_identity",
                "source_revision",
                "release_sha256",
                "environment_sha256",
            )
        ):
            raise ValueError("v37 worker placement identity is incomplete")
        if not re.fullmatch(r"[0-9a-f]{64}", str(item["release_sha256"])) or not re.fullmatch(
            r"[0-9a-f]{64}", str(item["environment_sha256"])
        ):
            raise ValueError("v37 worker placement release or environment SHA is invalid")
        source_revision = str(item["source_revision"])
        if expected_source_revision is not None:
            if source_revision != expected_source_revision:
                raise ValueError("v37 worker placement source revision drifted")
        elif not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source_revision):
            raise ValueError("v37 worker placement source revision is invalid")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+", str(item["poller_identity"])):
            raise ValueError("v37 worker placement poller identity is invalid")
        try:
            last_access = datetime.fromisoformat(
                str(item["poller_last_access_at"]).replace("Z", "+00:00")
            )
        except ValueError as error:
            raise ValueError("v37 worker poller last-access timestamp is invalid") from error
        if last_access.tzinfo is None or abs(
            (last_access.astimezone(UTC) - captured_at).total_seconds()
        ) > 120:
            raise ValueError("v37 worker poller last-access is stale")
        identity = (str(item["physical_host"]), int(item["pid"]), str(item["role"]))
        if identity in identities:
            raise ValueError("v37 worker placement identity is duplicated")
        identities.add(identity)
        queue_roles.setdefault(str(item["task_queue"]), set()).add(str(item["role"]))
        if item["gpu_index"] is not None:
            placement = (str(item["physical_host"]), int(item["gpu_index"]))
            if placement not in eligible:
                raise ValueError("v37 worker placement uses a non-eligible GPU")
            if placement in gpu_placements:
                raise ValueError("v37 worker placement oversubscribes a GPU")
            gpu_placements.add(placement)
        if item["role"] == "boltz2" and not re.fullmatch(
            r"[0-9a-f]{64}", str(item["weights_sha256"])
        ):
            raise ValueError("v37 Boltz worker weights SHA is invalid")
    maximum_workers = (
        int(contract.resource_capacity["gpu"]["maximum_concurrent_workers"])
        if contract is not None
        else 4
    )
    if len(gpu_placements) > maximum_workers:
        raise ValueError("v37 worker placement exceeds frozen GPU capacity")
    queues = expected_task_queues or V37_DEFAULT_TASK_QUEUES
    if set(queues) != set(V37_REQUIRED_WORKER_ROLE_BY_QUEUE_KEY):
        raise ValueError("v37 expected task queue contract drifted")
    expected_queue_roles = {
        str(queues[key]): V37_REQUIRED_WORKER_ROLE_BY_QUEUE_KEY[key]
        for key in V37_REQUIRED_WORKER_ROLE_BY_QUEUE_KEY
    }
    if set(queue_roles) != set(expected_queue_roles):
        raise ValueError("v37 worker placement task queue coverage drifted")
    if any(queue_roles[queue] != {role} for queue, role in expected_queue_roles.items()):
        raise ValueError("v37 worker placement role-to-task-queue mapping drifted")
    return payload


def build_v37_pipeline_queue_transition_ledger(
    *,
    pipeline_manifest: dict[str, Any],
    stage_outcomes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the deterministic FIFO queue/transition ledger for replay."""
    expected_ids = {
        logical_id
        for item in pipeline_manifest.get("items", [])
        for logical_id in item.get("stage_logical_ids", {}).values()
    }
    if set(stage_outcomes) != expected_ids:
        raise ValueError("v37 pipeline stage outcome coverage drifted")
    transitions = []
    sequence = 0
    for item in pipeline_manifest["items"]:
        ordinal = int(item["proposal_ordinal"])
        for stage in V37_PIPELINE_STAGES:
            sequence += 1
            logical_id = item["stage_logical_ids"][stage]
            outcome = stage_outcomes[logical_id]
            if set(outcome) != {"outcome", "activity_receipts"}:
                raise ValueError("v37 pipeline stage outcome schema drifted")
            if outcome["outcome"] not in {
                "succeeded",
                "skipped_not_selected",
                "failed_without_replacement",
            }:
                raise ValueError("v37 pipeline stage outcome is invalid")
            receipts = outcome["activity_receipts"]
            if not isinstance(receipts, list):
                raise ValueError("v37 pipeline activity receipts must be a list")
            if outcome["outcome"] == "succeeded" and not receipts:
                raise ValueError("v37 succeeded pipeline stage lacks activity receipts")
            if outcome["outcome"] == "skipped_not_selected" and receipts:
                raise ValueError("v37 skipped pipeline stage cannot have activity receipts")
            validated_receipts = [_validate_activity_transition_receipt(row) for row in receipts]
            dispatch_at = (
                min(row["scheduled_at"] for row in validated_receipts)
                if validated_receipts
                else None
            )
            start_at = (
                min(row["started_at"] for row in validated_receipts)
                if validated_receipts
                else None
            )
            finish_at = (
                max(row["finished_at"] for row in validated_receipts)
                if validated_receipts
                else None
            )
            transitions.append(
                {
                    "stage_logical_id": logical_id,
                    "occurrence_id": item["occurrence_id"],
                    "proposal_ordinal": ordinal,
                    "stage": stage,
                    "queue_position": ordinal,
                    "dispatch_sequence": sequence,
                    "dispatch_at": dispatch_at,
                    "start_at": start_at,
                    "finish_at": finish_at,
                    "outcome": outcome["outcome"],
                    "backpressure_observed": any(
                        row["schedule_to_start_seconds"] > 0.0
                        for row in validated_receipts
                    ),
                    "activity_receipts": validated_receipts,
                }
            )
    result: dict[str, Any] = {
        "schema_version": "v37.pipeline-queue-transition-ledger.1",
        "pipeline_manifest_sha256": pipeline_manifest["pipeline_manifest_sha256"],
        "scheduling_policy": "deterministic_fifo_single_arm",
        "transitions": transitions,
    }
    result["ledger_sha256"] = sha256_json(result)
    return result


def _validate_activity_transition_receipt(payload: Any) -> dict[str, Any]:
    required = {
        "schema_version",
        "activity_id",
        "activity_type",
        "attempt",
        "task_queue",
        "scheduled_at",
        "started_at",
        "finished_at",
        "schedule_to_start_seconds",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("v37 activity transition receipt schema drifted")
    if payload["schema_version"] != "v37.activity-transition-receipt.1":
        raise ValueError("v37 activity transition receipt version drifted")
    if int(payload["attempt"]) < 1 or any(
        not isinstance(payload[field], str) or not payload[field].strip()
        for field in ("activity_id", "activity_type", "task_queue")
    ):
        raise ValueError("v37 activity transition receipt identity is incomplete")
    parsed = []
    for field in ("scheduled_at", "started_at", "finished_at"):
        try:
            value = datetime.fromisoformat(str(payload[field]).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("v37 activity transition timestamp is invalid") from error
        if value.tzinfo is None:
            raise ValueError("v37 activity transition timestamp lacks timezone")
        parsed.append(value.astimezone(UTC))
    scheduled, started, finished = parsed
    if not scheduled <= started <= finished:
        raise ValueError("v37 activity transition timestamps are not monotonic")
    observed_latency = float(payload["schedule_to_start_seconds"])
    expected_latency = (started - scheduled).total_seconds()
    if observed_latency < 0 or abs(observed_latency - expected_latency) > 1e-6:
        raise ValueError("v37 activity schedule-to-start latency drifted")
    return payload


def validate_v37_capacity_replay_artifacts(
    *,
    contract: V37CapacityContract | None = None,
    worker_placement_snapshot: dict[str, Any],
    pipeline_manifest: dict[str, Any],
    queue_transition_ledger: dict[str, Any],
    expected_stage_outcomes: dict[str, str] | None = None,
) -> None:
    validate_v37_worker_placement_snapshot(worker_placement_snapshot, contract=contract)
    if pipeline_manifest.get("pipeline_manifest_sha256") != sha256_json(
        {
            key: value
            for key, value in pipeline_manifest.items()
            if key != "pipeline_manifest_sha256"
        }
    ):
        raise ValueError("v37 pipeline manifest self-hash drifted")
    if tuple(pipeline_manifest.get("stage_order", [])) != V37_PIPELINE_STAGES:
        raise ValueError("v37 pipeline manifest stage order drifted")
    rebuilt_manifest = build_v37_pipeline_manifest(
        [
            {
                "proposal_ordinal": item.get("proposal_ordinal"),
                "occurrence_id": item.get("occurrence_id"),
            }
            for item in pipeline_manifest.get("items", [])
        ]
    )
    if pipeline_manifest != rebuilt_manifest:
        raise ValueError("v37 pipeline manifest semantics drifted")
    ledger_identity = {
        key: value for key, value in queue_transition_ledger.items() if key != "ledger_sha256"
    }
    if queue_transition_ledger.get("ledger_sha256") != sha256_json(ledger_identity):
        raise ValueError("v37 pipeline transition ledger self-hash drifted")
    if queue_transition_ledger.get("pipeline_manifest_sha256") != pipeline_manifest.get(
        "pipeline_manifest_sha256"
    ):
        raise ValueError("v37 pipeline transition ledger belongs to another manifest")
    expected = {
        logical_id
        for item in pipeline_manifest.get("items", [])
        for logical_id in item.get("stage_logical_ids", {}).values()
    }
    observed = [
        str(item.get("stage_logical_id"))
        for item in queue_transition_ledger.get("transitions", [])
    ]
    if len(observed) != len(expected) or set(observed) != expected:
        raise ValueError("v37 pipeline transition ledger coverage drifted")
    transitions = queue_transition_ledger.get("transitions")
    if not isinstance(transitions, list):
        raise ValueError("v37 pipeline transition ledger rows are missing")
    outcomes: dict[str, dict[str, Any]] = {}
    for row in transitions:
        if not isinstance(row, dict):
            raise ValueError("v37 pipeline transition row is invalid")
        logical_id = str(row.get("stage_logical_id"))
        outcomes[logical_id] = {
            "outcome": row.get("outcome"),
            "activity_receipts": row.get("activity_receipts"),
        }
    rebuilt_ledger = build_v37_pipeline_queue_transition_ledger(
        pipeline_manifest=pipeline_manifest,
        stage_outcomes=outcomes,
    )
    if queue_transition_ledger != rebuilt_ledger:
        raise ValueError("v37 pipeline transition ledger semantics drifted")
    for row in transitions:
        stage = str(row["stage"])
        expected_type, queue_key, expected_count = V37_STAGE_ACTIVITY_CONTRACT[stage]
        receipts = row["activity_receipts"]
        if row["outcome"] == "succeeded" and (
            len(receipts) != expected_count
            or any(
                receipt["activity_type"] != expected_type
                or receipt["task_queue"] != V37_DEFAULT_TASK_QUEUES[queue_key]
                for receipt in receipts
            )
        ):
            raise ValueError("v37 pipeline activity receipt contract drifted")
    if expected_stage_outcomes is not None and {
        logical_id: outcome["outcome"] for logical_id, outcome in outcomes.items()
    } != expected_stage_outcomes:
        raise ValueError("v37 pipeline outcomes differ from database evidence")


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
