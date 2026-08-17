from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

StageName = Literal[
    "history_target_knowledge_freeze",
    "proposal_generation",
    "sequence_metrics",
    "sequence_refinement",
    "sequence_admission",
    "parallel_target_structure",
    "pareto_and_replay",
]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StageControlSpec(FrozenModel):
    stage: StageName
    order: int = Field(ge=0)
    expected_durable_count: int = Field(ge=0)
    progress_check_seconds: int = Field(ge=60)
    plan_conformance_seconds: int = Field(ge=300)
    stall_after_seconds: int = Field(ge=300)
    deadline_seconds: int = Field(ge=600)
    required_queue: str
    resource_class: Literal["local_cpu", "remote_gpu", "remote_cpu"]
    max_concurrency: int = Field(gt=0)
    downstream_dispatch_requires_completion: Literal[True] = True

    @model_validator(mode="after")
    def validate_timing(self) -> StageControlSpec:
        if self.progress_check_seconds > self.stall_after_seconds:
            raise ValueError("progress checks must occur before a stage is declared stalled")
        if self.stall_after_seconds >= self.deadline_seconds:
            raise ValueError("stall threshold must precede the stage deadline")
        return self


class RunControlPlan(FrozenModel):
    schema_version: Literal["v38.run-control.1"] = "v38.run-control.1"
    stages: tuple[StageControlSpec, ...]
    activity_heartbeat_seconds: int = Field(ge=10, le=60)
    operator_review_seconds: int = Field(ge=3600)
    allowed_idle_capacity_confirmations: int = Field(ge=2)
    backlog_per_slot_before_scale: int = Field(ge=2)
    exact_once_submission_required: Literal[True] = True
    old_run_mutation_forbidden: Literal[True] = True
    foreign_process_stop_forbidden: Literal[True] = True
    scientific_contract_mutation_during_recovery_forbidden: Literal[True] = True
    structure_before_sequence_admission_forbidden: Literal[True] = True

    @model_validator(mode="after")
    def validate_stages(self) -> RunControlPlan:
        orders = [item.order for item in self.stages]
        if orders != list(range(len(self.stages))):
            raise ValueError("stage order must be contiguous")
        names = [item.stage for item in self.stages]
        if len(names) != len(set(names)):
            raise ValueError("stage names must be unique")
        return self


class StageProgressObservation(FrozenModel):
    observed_at: datetime
    stage: StageName
    stage_started_at: datetime
    last_durable_progress_at: datetime
    durable_count: int = Field(ge=0)
    previous_durable_count: int = Field(ge=0)
    queue_backlog: int = Field(ge=0)
    active_owned_slots: int = Field(ge=0)
    required_poller_count: int = Field(ge=0)
    database_healthy: bool
    temporal_healthy: bool
    object_store_healthy: bool
    evidence_integrity_ok: bool
    exact_identity_ok: bool
    allowed_capacity_available: bool
    allowed_capacity_consecutive_confirmations: int = Field(ge=0)
    foreign_process_conflict: bool = False
    active_attempt_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_times(self) -> StageProgressObservation:
        for value in (
            self.observed_at,
            self.stage_started_at,
            self.last_durable_progress_at,
        ):
            if value.tzinfo is None:
                raise ValueError("control timestamps must be timezone-aware")
        if self.last_durable_progress_at < self.stage_started_at:
            raise ValueError("last progress precedes stage start")
        if self.observed_at < self.last_durable_progress_at:
            raise ValueError("observation precedes last progress")
        return self


class RunControlDecision(FrozenModel):
    action: Literal[
        "continue",
        "advance_stage",
        "wait_for_allowed_capacity",
        "wait_for_executable_release",
        "diagnose_control_plane",
        "repair_owned_worker",
        "diagnose_stage_stall",
        "scale_allowed_capacity",
        "fail_closed",
    ]
    reasons: tuple[str, ...]
    tasks: tuple[str, ...]
    resubmit_forbidden: Literal[True] = True
    scientific_contract_change_forbidden: Literal[True] = True


def build_default_run_control_plan(
    *,
    proposal_count: int = 900,
    evaluation_count: int = 9900,
    structure_branch_count: int = 3,
    structure_candidates_per_branch: int = 48,
    boltz_seeds: int = 3,
    rosetta_decoys: int = 16,
) -> RunControlPlan:
    structure_count = structure_branch_count * structure_candidates_per_branch * boltz_seeds
    rosetta_count = structure_count * rosetta_decoys
    return RunControlPlan(
        stages=(
            StageControlSpec(
                stage="history_target_knowledge_freeze",
                order=0,
                expected_durable_count=3,
                progress_check_seconds=300,
                plan_conformance_seconds=900,
                stall_after_seconds=1200,
                deadline_seconds=3600,
                required_queue="pepagent-control-v38",
                resource_class="local_cpu",
                max_concurrency=1,
            ),
            StageControlSpec(
                stage="proposal_generation",
                order=1,
                expected_durable_count=proposal_count,
                progress_check_seconds=300,
                plan_conformance_seconds=900,
                stall_after_seconds=1200,
                deadline_seconds=7200,
                required_queue="pepagent-generator-v38",
                resource_class="local_cpu",
                max_concurrency=3,
            ),
            StageControlSpec(
                stage="sequence_metrics",
                order=2,
                expected_durable_count=evaluation_count,
                progress_check_seconds=300,
                plan_conformance_seconds=900,
                stall_after_seconds=900,
                deadline_seconds=5400,
                required_queue="pepagent-cpu-metrics-v38",
                resource_class="local_cpu",
                max_concurrency=5,
            ),
            StageControlSpec(
                stage="sequence_refinement",
                order=3,
                expected_durable_count=0,
                progress_check_seconds=300,
                plan_conformance_seconds=900,
                stall_after_seconds=1200,
                deadline_seconds=5400,
                required_queue="pepagent-generator-v38",
                resource_class="local_cpu",
                max_concurrency=3,
            ),
            StageControlSpec(
                stage="sequence_admission",
                order=4,
                expected_durable_count=1,
                progress_check_seconds=120,
                plan_conformance_seconds=600,
                stall_after_seconds=600,
                deadline_seconds=1800,
                required_queue="pepagent-control-v38",
                resource_class="local_cpu",
                max_concurrency=1,
            ),
            StageControlSpec(
                stage="parallel_target_structure",
                order=5,
                expected_durable_count=structure_count + rosetta_count,
                progress_check_seconds=300,
                plan_conformance_seconds=900,
                stall_after_seconds=1800,
                deadline_seconds=43200,
                required_queue="pepagent-gpu-boltz2-v38",
                resource_class="remote_gpu",
                max_concurrency=2,
            ),
            StageControlSpec(
                stage="pareto_and_replay",
                order=6,
                expected_durable_count=2,
                progress_check_seconds=120,
                plan_conformance_seconds=600,
                stall_after_seconds=600,
                deadline_seconds=2700,
                required_queue="pepagent-control-v38",
                resource_class="local_cpu",
                max_concurrency=1,
            ),
        ),
        activity_heartbeat_seconds=30,
        operator_review_seconds=7200,
        allowed_idle_capacity_confirmations=2,
        backlog_per_slot_before_scale=3,
    )


def assess_run_control(
    plan: RunControlPlan,
    observation: StageProgressObservation,
) -> RunControlDecision:
    spec = next((item for item in plan.stages if item.stage == observation.stage), None)
    if spec is None:
        raise ValueError(f"stage is not in the run-control plan: {observation.stage}")
    if not observation.evidence_integrity_ok or not observation.exact_identity_ok:
        return RunControlDecision(
            action="fail_closed",
            reasons=("evidence_or_exact_identity_drift",),
            tasks=("freeze_current_attempt_receipt", "block_downstream_dispatch"),
        )
    unhealthy = [
        name
        for name, healthy in (
            ("database", observation.database_healthy),
            ("temporal", observation.temporal_healthy),
            ("object_store", observation.object_store_healthy),
        )
        if not healthy
    ]
    if unhealthy:
        return RunControlDecision(
            action="diagnose_control_plane",
            reasons=(f"unhealthy_services:{','.join(unhealthy)}",),
            tasks=(
                "run_read_only_service_probes",
                "verify_supervised_tunnels",
                "do_not_resubmit_workflow",
            ),
        )
    if observation.durable_count >= spec.expected_durable_count:
        return RunControlDecision(
            action="advance_stage",
            reasons=("durable_stage_target_reached",),
            tasks=("persist_stage_completion_receipt", "release_unused_stage_resources"),
        )
    if observation.required_poller_count == 0:
        return RunControlDecision(
            action="repair_owned_worker",
            reasons=("required_queue_has_no_poller",),
            tasks=(
                "verify_exact_worker_ownership",
                "verify_source_release_and_environment",
                "restart_only_owned_worker_if_no_inflight_attempt",
            ),
        )
    if spec.resource_class == "remote_gpu" and observation.active_owned_slots == 0:
        return RunControlDecision(
            action="wait_for_allowed_capacity",
            reasons=("no_authorized_gpu_slot",),
            tasks=("probe_only_allowed_gpu_indices", "retain_backlog_without_dispatch_loss"),
        )
    now = observation.observed_at.astimezone(UTC)
    since_progress = now - observation.last_durable_progress_at.astimezone(UTC)
    stage_age = now - observation.stage_started_at.astimezone(UTC)
    if since_progress >= timedelta(seconds=spec.stall_after_seconds):
        return RunControlDecision(
            action="diagnose_stage_stall",
            reasons=(
                "no_durable_progress_within_stage_stall_window",
                "stage_deadline_exceeded"
                if stage_age >= timedelta(seconds=spec.deadline_seconds)
                else "stage_deadline_not_yet_exceeded",
            ),
            tasks=(
                "inspect_pending_attempts_and_retry_history",
                "inspect_queue_backlog_and_poller_freshness",
                "repair_execution_only_defect_or_fail_closed",
            ),
        )
    scale_threshold = max(1, observation.active_owned_slots) * plan.backlog_per_slot_before_scale
    if (
        observation.queue_backlog >= scale_threshold
        and observation.allowed_capacity_available
        and observation.allowed_capacity_consecutive_confirmations
        >= plan.allowed_idle_capacity_confirmations
        and not observation.foreign_process_conflict
    ):
        return RunControlDecision(
            action="scale_allowed_capacity",
            reasons=("sustained_backlog_and_twice_verified_idle_capacity",),
            tasks=(
                "start_only_preregistered_owned_worker",
                "preserve_queue_and_science_identity",
                "record_placement_receipt",
            ),
        )
    return RunControlDecision(
        action="continue",
        reasons=("durable_progress_within_stage_plan",),
        tasks=("schedule_next_progress_check",),
    )
