import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pepagent.db.base import Base, TimestampMixin


class Target(Base, TimestampMixin):
    __tablename__ = "targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    organism: Mapped[str | None] = mapped_column(String(255))
    accession: Mapped[str | None] = mapped_column(String(128))
    sequence: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    pockets: Mapped[list["TargetPocket"]] = relationship(back_populates="target")


class ExperimentRun(Base, TimestampMixin):
    __tablename__ = "experiment_runs"
    __table_args__ = (
        UniqueConstraint(
            "formal_submission_key",
            name="uq_experiment_runs_formal_submission_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("targets.id"), nullable=False)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    spec_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    formal_submission_key: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    temporal_workflow_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    temporal_run_id: Mapped[str | None] = mapped_column(String(255))
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiment_runs.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    target: Mapped[Target] = relationship()
    candidates: Mapped[list["Candidate"]] = relationship(back_populates="run")


class ExperimentRunTargetBranch(Base):
    """Frozen per-target branch identity for one multi-target experiment run."""

    __tablename__ = "experiment_run_target_branches"
    __table_args__ = (
        UniqueConstraint("run_id", "target_id", name="uq_run_target_branch_target"),
        UniqueConstraint("run_id", "branch_key", name="uq_run_target_branch_key"),
        UniqueConstraint("run_id", "evidence_namespace", name="uq_run_target_branch_namespace"),
        CheckConstraint(
            "native_pocket_id <> wrong_pocket_id",
            name="ck_run_target_branch_distinct_pockets",
        ),
        Index("ix_run_target_branch_status", "run_id", "status"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), primary_key=True)
    branch_order: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_key: Mapped[str] = mapped_column(String(128), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("targets.id"), nullable=False)
    panel_role: Mapped[str] = mapped_column(String(32), nullable=False)
    qualification_witness_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    coordinate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    native_pocket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("target_pockets.id"), nullable=False
    )
    wrong_pocket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("target_pockets.id"), nullable=False
    )
    evidence_namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RunStageCheckpoint(Base):
    """Append-only durable observation and controller decision for a run stage."""

    __tablename__ = "run_stage_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "stage_name",
            "observation_no",
            name="uq_run_stage_checkpoint_observation",
        ),
        Index("ix_run_stage_checkpoint_latest", "run_id", "stage_order", "observed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_no: Mapped[int] = mapped_column(Integer, nullable=False)
    durable_count: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_durable_count: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_status: Mapped[str] = mapped_column(String(32), nullable=False)
    controller_action: Mapped[str] = mapped_column(String(64), nullable=False)
    reasons_json: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    tasks_json: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    receipt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MultiTargetStructureEvidenceRecord(Base):
    """One target/control/seed-specific Boltz pose or Rosetta decoy."""

    __tablename__ = "multitarget_structure_evidence_records"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "candidate_id",
            "target_id",
            "control_lane",
            "boltz_seed",
            "evidence_kind",
            "decoy_ordinal",
            name="uq_multitarget_structure_evidence_identity",
        ),
        CheckConstraint(
            "control_lane IN ('native', 'wrong_pocket')",
            name="ck_multitarget_structure_control_lane",
        ),
        CheckConstraint(
            "(evidence_kind = 'boltz_pose' AND decoy_ordinal = -1) OR "
            "(evidence_kind = 'rosetta_decoy' AND decoy_ordinal >= 0)",
            name="ck_multitarget_structure_evidence_kind_ordinal",
        ),
        Index(
            "ix_multitarget_structure_evidence_branch",
            "run_id",
            "target_id",
            "control_lane",
            "evidence_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_runs.id"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id"), nullable=False
    )
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("targets.id"), nullable=False)
    tool_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_calls.id"), nullable=False
    )
    evidence_namespace: Mapped[str] = mapped_column(String(255), nullable=False)
    control_lane: Mapped[str] = mapped_column(String(32), nullable=False)
    boltz_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    decoy_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    task_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    output_artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    score_artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Candidate(Base, TimestampMixin):
    __tablename__ = "candidates"
    __table_args__ = (
        Index("ix_candidate_run_generation", "run_id", "generation"),
        Index("ix_candidate_run_sequence", "run_id", "sequence_sha256", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    sequence: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id"))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    proposal_rank: Mapped[int | None] = mapped_column(Integer)
    generator_call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tool_calls.id"))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    run: Mapped[ExperimentRun] = relationship(back_populates="candidates")
    parent: Mapped["Candidate | None"] = relationship(remote_side=[id])
    evaluations: Mapped[list["Evaluation"]] = relationship(back_populates="candidate")


class CandidateOccurrence(Base):
    """Immutable record of one sequence proposal, before candidate deduplication."""

    __tablename__ = "candidate_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "tool_call_id",
            "occurrence_rank",
            name="uq_candidate_occurrence_call_rank",
        ),
        CheckConstraint(
            "(occurrence_kind = 'de_novo' AND parent_candidate_id IS NULL) OR "
            "(occurrence_kind <> 'de_novo' AND parent_candidate_id IS NOT NULL)",
            name="ck_candidate_occurrence_parent_semantics",
        ),
        Index("ix_candidate_occurrence_run_label", "run_id", "opaque_arm_label"),
        Index("ix_candidate_occurrence_run_sequence", "run_id", "sequence_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    tool_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tool_calls.id"), nullable=False)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id"))
    parent_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidates.id"), nullable=True
    )
    occurrence_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    occurrence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    opaque_arm_label: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_call_idempotency", "idempotency_key", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(128), nullable=False)
    model_uri: Mapped[str | None] = mapped_column(Text)
    weights_sha256: Mapped[str | None] = mapped_column(String(64))
    environment_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    random_seed: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    output_sha256: Mapped[str | None] = mapped_column(String(64))
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ToolCallDependency(Base):
    """Typed edge between two persisted experiment attempts."""

    __tablename__ = "tool_call_dependencies"
    __table_args__ = (Index("ix_tool_call_dependency_parent", "parent_tool_call_id"),)

    child_tool_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_calls.id"), primary_key=True
    )
    parent_tool_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_calls.id"), primary_key=True
    )
    relation_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentDecision(Base):
    """Immutable original Agent record plus its machine-executable projection."""

    __tablename__ = "agent_decisions"
    __table_args__ = (Index("ix_agent_decision_run_generation", "run_id", "generation"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(128))
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    response_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    structured_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    response_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AgentDecisionToolCallEdge(Base):
    """Typed graph edge between an Agent decision and an operation attempt."""

    __tablename__ = "agent_decision_tool_call_edges"

    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_decisions.id"), primary_key=True
    )
    tool_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tool_calls.id"), primary_key=True)
    direction: Mapped[str] = mapped_column(String(16), primary_key=True)
    relation_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (
        Index("ix_evaluation_candidate_metric", "candidate_id", "metric_name"),
        Index(
            "ix_evaluation_unique_evidence",
            "candidate_id",
            "metric_name",
            "tool_call_id",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id"), nullable=False)
    tool_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tool_calls.id"), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False)
    numeric_value: Mapped[float | None] = mapped_column(Float)
    text_value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    out_of_domain: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    limitations_json: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    candidate: Mapped[Candidate] = relationship(back_populates="evaluations")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class ModelRelease(Base):
    __tablename__ = "model_releases"
    __table_args__ = (
        UniqueConstraint(
            "name", "source_revision", "weights_sha256", name="uq_model_release_identity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(128), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    weights_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(128), nullable=False)
    admission_status: Mapped[str] = mapped_column(String(32), nullable=False)
    mlflow_model_name: Mapped[str | None] = mapped_column(String(255))
    mlflow_model_version: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ModelReleaseArtifact(Base):
    __tablename__ = "model_release_artifacts"

    model_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_releases.id"), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(64), primary_key=True)


class EvidenceArtifact(Base):
    __tablename__ = "evidence_artifacts"

    tool_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tool_calls.id"), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(64), primary_key=True)


class HarnessRelease(Base):
    """Immutable identity and evidence boundary for one Agent harness release."""

    __tablename__ = "harness_releases"
    __table_args__ = (Index("ix_harness_release_scope_status", "scope_id", "release_status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    harness_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    release_status: Mapped[str] = mapped_column(String(32), nullable=False)
    change_hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    primary_changed_component: Mapped[str] = mapped_column(String(128), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    model_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    environment_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_taxonomy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_contract_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    history_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    allowed_evidence_slice_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    forbidden_holdout_manifest_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    endpoint_contract_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    rollback_harness_release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("harness_releases.id")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HarnessLineageEdge(Base):
    """Typed immutable edge in the harness release DAG."""

    __tablename__ = "harness_lineage_edges"
    __table_args__ = (
        CheckConstraint(
            "child_release_id <> parent_release_id",
            name="harness_lineage_not_self",
        ),
        Index("ix_harness_lineage_parent", "parent_release_id"),
    )

    child_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("harness_releases.id"), primary_key=True
    )
    parent_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("harness_releases.id"), primary_key=True
    )
    relation_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HarnessTrial(Base):
    """One frozen counterfactual, shadow, or prospective comparison."""

    __tablename__ = "harness_trials"
    __table_args__ = (
        CheckConstraint(
            "champion_release_id <> challenger_release_id",
            name="harness_trial_distinct_releases",
        ),
        Index("ix_harness_trial_scope_phase", "scope_id", "phase"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trial_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    champion_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("harness_releases.id"), nullable=False
    )
    challenger_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("harness_releases.id"), nullable=False
    )
    parent_trial_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("harness_trials.id"))
    history_partition_manifest_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    assignment_manifest_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    blinding_manifest_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    endpoint_contract_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    budget_contract_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    adjudication_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiment_runs.id"))
    blinded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    adjudication_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unblinded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HarnessAssignment(Base):
    """One paired episode assignment to a frozen harness release."""

    __tablename__ = "harness_assignments"
    __table_args__ = (
        UniqueConstraint(
            "trial_id",
            "episode_key",
            "assigned_release_id",
            name="uq_harness_assignment_episode_release",
        ),
        UniqueConstraint(
            "trial_id",
            "assignment_rank",
            name="uq_harness_assignment_trial_rank",
        ),
        Index("ix_harness_assignment_trial_pair", "trial_id", "pair_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trial_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("harness_trials.id"), nullable=False)
    experiment_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_runs.id"), nullable=False
    )
    episode_key: Mapped[str] = mapped_column(String(128), nullable=False)
    pair_key: Mapped[str] = mapped_column(String(128), nullable=False)
    assigned_release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("harness_releases.id"), nullable=False
    )
    opaque_arm_label: Mapped[str] = mapped_column(String(64), nullable=False)
    assignment_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    random_seed: Mapped[int | None] = mapped_column(BigInteger)
    resource_class: Mapped[str] = mapped_column(String(64), nullable=False)
    controls_formal_action: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HarnessOutcome(Base):
    """Independent endpoint-family outcome for one harness assignment."""

    __tablename__ = "harness_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id",
            "endpoint_family",
            "endpoint_name",
            "tool_call_id",
            name="uq_harness_outcome_evidence",
        ),
        Index("ix_harness_outcome_assignment_family", "assignment_id", "endpoint_family"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("harness_assignments.id"), nullable=False
    )
    endpoint_family: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tool_calls.id"), nullable=False)
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    numeric_value: Mapped[float | None] = mapped_column(Float)
    text_value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    limitations_json: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HarnessPromotionDecision(Base):
    """Append-only scoped promotion, retention, rejection, or rollback decision."""

    __tablename__ = "harness_promotion_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prospective_trial_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("harness_trials.id"), nullable=False, unique=True
    )
    counterfactual_trial_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("harness_trials.id"), nullable=False
    )
    shadow_trial_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("harness_trials.id"), nullable=False
    )
    agent_decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_decisions.id"), nullable=False, unique=True
    )
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    promoted_release_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("harness_releases.id"))
    rollback_release_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("harness_releases.id"))
    decision_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TargetPocket(Base, TimestampMixin):
    __tablename__ = "target_pockets"
    __table_args__ = (
        UniqueConstraint("target_id", "pocket_key", name="uq_target_pocket_key"),
        Index("ix_target_pocket_conditioning", "conditioning_enabled", "evidence_score"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("targets.id"), nullable=False)
    pocket_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    pocket_type: Mapped[str] = mapped_column(String(64), nullable=False)
    functional_role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_grade: Mapped[str] = mapped_column(String(8), nullable=False)
    evidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    conditioning_priority: Mapped[str] = mapped_column(String(32), nullable=False)
    conditioning_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    residue_indices: Mapped[list[int]] = mapped_column(JSONB, default=list, nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    limitations_json: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    target: Mapped[Target] = relationship(back_populates="pockets")
    evidence: Mapped[list["PocketEvidence"]] = relationship(back_populates="pocket")


class PocketEvidence(Base, TimestampMixin):
    __tablename__ = "pocket_evidence"
    __table_args__ = (
        Index("ix_pocket_evidence_pocket", "pocket_id"),
        UniqueConstraint("evidence_sha256", name="uq_pocket_evidence_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("targets.id"), nullable=False)
    pocket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("target_pockets.id"))
    evidence_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="legacy")
    evidence_grade: Mapped[str] = mapped_column(String(8), nullable=False, default="U")
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_accession: Mapped[str | None] = mapped_column(String(128))
    source_version: Mapped[str | None] = mapped_column(String(128))
    source_revision_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chain_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    source_residue_indices: Mapped[list[int]] = mapped_column(JSONB, default=list, nullable=False)
    residue_indices: Mapped[list[int]] = mapped_column(JSONB, default=list, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    experimental_method: Mapped[str | None] = mapped_column(String(128))
    resolution_angstrom: Mapped[float | None] = mapped_column(Float)
    mapping_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    limitations_json: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    pocket: Mapped[TargetPocket | None] = relationship(back_populates="evidence")


class TargetQualificationAudit(Base):
    """Append-only target qualification row preserved before panel selection."""

    __tablename__ = "target_qualification_audits"
    __table_args__ = (
        UniqueConstraint(
            "audit_scope_id",
            "shortlist_order",
            name="uq_target_qualification_scope_order",
        ),
        UniqueConstraint(
            "audit_scope_id",
            "target_key",
            name="uq_target_qualification_scope_key",
        ),
        UniqueConstraint(
            "audit_scope_id",
            "target_id",
            name="uq_target_qualification_scope_target",
        ),
        CheckConstraint("shortlist_order > 0", name="target_qualification_positive_order"),
        CheckConstraint(
            "primary_pocket_id IS NULL OR wrong_pocket_id IS NULL "
            "OR primary_pocket_id <> wrong_pocket_id",
            name="target_qualification_distinct_pockets",
        ),
        Index("ix_target_qualification_scope_status", "audit_scope_id", "audit_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    audit_scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    shortlist_order: Mapped[int] = mapped_column(Integer, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("targets.id"), nullable=False)
    audit_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_runs.id"), nullable=False
    )
    audit_tool_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_calls.id"), nullable=False
    )
    audit_decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_decisions.id"), nullable=False
    )
    target_key: Mapped[str] = mapped_column(String(128), nullable=False)
    organism_and_strain: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_accession: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence_entry_version: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_admission_basis: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sequence_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    source_manifest_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    feature_evidence_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    structure_source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    coordinate_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    structure_validation_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id")
    )
    sequence_structure_mapping_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id")
    )
    primary_pocket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("target_pockets.id"))
    wrong_pocket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("target_pockets.id"))
    primary_pocket_grade: Mapped[str | None] = mapped_column(String(8))
    primary_pocket_definition_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id")
    )
    wrong_pocket_definition_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id")
    )
    audit_status: Mapped[str] = mapped_column(String(32), nullable=False)
    rejection_reasons_json: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    diversity_vector_json: Mapped[list[float] | None] = mapped_column(JSONB)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TargetPanelSelectionWitness(Base):
    """Frozen database identity for one deterministic multi-target panel selection."""

    __tablename__ = "target_panel_selection_witnesses"
    __table_args__ = (
        CheckConstraint(
            "requested_new_target_count BETWEEN 3 AND 5",
            name="target_panel_requested_count_range",
        ),
        Index("ix_target_panel_selection_status", "selection_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    audit_scope_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_method: Mapped[str] = mapped_column(String(128), nullable=False)
    selection_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_runs.id"), nullable=False
    )
    selection_tool_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_calls.id"), nullable=False
    )
    selection_decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_decisions.id"), nullable=False
    )
    requested_new_target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    target_names_selected_before_audit: Mapped[bool] = mapped_column(Boolean, nullable=False)
    peptide_or_structure_outcomes_used_for_selection: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )
    target_agnostic_amp_lane_retained: Mapped[bool] = mapped_column(Boolean, nullable=False)
    acea_anchor_vector_json: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    acea_anchor_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    selection_witness_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    snapshot_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    selection_status: Mapped[str] = mapped_column(String(32), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TargetPanelSelectionMember(Base):
    """Ordered typed edge from a panel witness to one selected audit row."""

    __tablename__ = "target_panel_selection_members"
    __table_args__ = (
        UniqueConstraint(
            "selection_witness_id",
            "target_audit_id",
            name="uq_target_panel_selection_member_audit",
        ),
        CheckConstraint("selection_rank > 0", name="target_panel_member_positive_rank"),
    )

    selection_witness_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("target_panel_selection_witnesses.id"), primary_key=True
    )
    selection_rank: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_audit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("target_qualification_audits.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LifecycleEvent(Base):
    """Append-only audit trail for runs, candidates, and evidence."""

    __tablename__ = "lifecycle_events"
    __table_args__ = (
        Index(
            "ix_lifecycle_aggregate",
            "aggregate_type",
            "aggregate_id",
            "sequence_no",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
