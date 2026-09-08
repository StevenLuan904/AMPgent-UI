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
    ForeignKeyConstraint,
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
        CheckConstraint(
            "(phase_code IS NULL AND phase_ordinal IS NULL) OR "
            "(phase_code IS NOT NULL AND phase_ordinal >= 0 AND phase_code IN "
            "('generation','score_all','challenger','qd_lineage','boltz','rosetta',"
            "'md','pool_s','unclassified'))",
            name="ck_experiment_run_phase_pair",
        ),
        Index("ix_experiment_run_group", "run_group_id"),
        Index("ix_experiment_run_root", "root_run_id"),
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
    run_group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scientific_run_groups.id"))
    root_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiment_runs.id"))
    phase_code: Mapped[str | None] = mapped_column(String(32))
    phase_ordinal: Mapped[int | None] = mapped_column(Integer)
    aggregation_basis: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    target: Mapped[Target] = relationship()
    candidates: Mapped[list["Candidate"]] = relationship(back_populates="run")


class ScientificRoot(Base):
    """Explicit scientific root; never inferred from title, target, sequence, or time."""

    __tablename__ = "scientific_roots"
    __table_args__ = (
        UniqueConstraint("root_key", name="uq_scientific_root_key"),
        UniqueConstraint("root_run_id", name="uq_scientific_root_run"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    root_key: Mapped[str] = mapped_column(String(128), nullable=False)
    root_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ScientificRunGroup(Base):
    """Explicit family of runs sharing one declared scientific root."""

    __tablename__ = "scientific_run_groups"
    __table_args__ = (
        UniqueConstraint("group_key", name="uq_scientific_run_group_key"),
        UniqueConstraint("root_run_id", name="uq_scientific_run_group_root"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    group_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scientific_root_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scientific_roots.id"), nullable=False
    )
    root_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RunLineageEdge(Base):
    """Typed explicit relationship between runs; initial backfill uses parent_run_id only."""

    __tablename__ = "run_lineage_edges"
    __table_args__ = (
        UniqueConstraint("edge_sha256", name="uq_run_lineage_edge_sha256"),
        UniqueConstraint(
            "parent_run_id", "child_run_id", "relation_type", name="uq_run_lineage_identity"
        ),
        CheckConstraint("parent_run_id <> child_run_id", name="ck_run_lineage_not_self"),
        CheckConstraint("relation_ordinal > 0", name="ck_run_lineage_positive_ordinal"),
        Index("ix_run_lineage_child", "child_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    parent_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_runs.id"), nullable=False
    )
    child_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_runs.id"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    relation_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    binding_basis: Mapped[str] = mapped_column(String(64), nullable=False)
    edge_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id"), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("targets.id"), nullable=False)
    tool_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tool_calls.id"), nullable=False)
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
        Index("ix_candidate_sequence_sha256", "sequence_sha256"),
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


class RunEvidenceAttachment(Base):
    """Exact subject/producer binding for post-hoc or cross-run evidence."""

    __tablename__ = "run_evidence_attachments"
    __table_args__ = (
        UniqueConstraint("attachment_sha256", name="uq_run_evidence_attachment_sha256"),
        CheckConstraint(
            "phase_code IN ('generation','score_all','challenger','qd_lineage',"
            "'boltz','rosetta','md','pool_s','unclassified')",
            name="ck_run_evidence_attachment_phase",
        ),
        CheckConstraint("phase_order >= 0", name="ck_run_evidence_phase_order"),
        CheckConstraint("evidence_ordinal > 0", name="ck_run_evidence_positive_ordinal"),
        Index(
            "ix_run_evidence_subject_timeline",
            "subject_run_id",
            "phase_order",
            "evidence_ordinal",
        ),
        Index("ix_run_evidence_candidate", "subject_candidate_id", "phase_order"),
        Index("ix_run_evidence_producer", "producer_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    subject_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_runs.id"), nullable=False
    )
    subject_candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id"))
    producer_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_runs.id"), nullable=False
    )
    tool_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tool_calls.id"), nullable=False)
    phase_code: Mapped[str] = mapped_column(String(32), nullable=False)
    phase_order: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_role: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_basis: Mapped[str] = mapped_column(String(64), nullable=False)
    attachment_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RunInvalidationEvent(Base):
    """Append-only explicit invalidation/reinstatement; run failure alone is not invalidation."""

    __tablename__ = "run_invalidation_events"
    __table_args__ = (
        UniqueConstraint("event_sha256", name="uq_run_invalidation_event_sha256"),
        UniqueConstraint("run_id", "event_ordinal", name="uq_run_invalidation_event_ordinal"),
        CheckConstraint("event_ordinal > 0", name="ck_run_invalidation_positive_ordinal"),
        CheckConstraint(
            "event_type IN ('invalidate','reinstate')", name="ck_run_invalidation_event_type"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    event_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_decisions.id"))
    event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class EvidenceInvalidationEvent(Base):
    """Append-only validity history for an explicitly identified evidence record."""

    __tablename__ = "evidence_invalidation_events"
    __table_args__ = (
        UniqueConstraint("event_sha256", name="uq_evidence_invalidation_event_sha256"),
        UniqueConstraint(
            "record_kind",
            "record_id",
            "event_ordinal",
            name="uq_evidence_invalidation_event_ordinal",
        ),
        CheckConstraint("event_ordinal > 0", name="ck_evidence_invalidation_positive_ordinal"),
        CheckConstraint(
            "event_type IN ('invalidate','reinstate')",
            name="ck_evidence_invalidation_event_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    subject_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiment_runs.id"), nullable=False
    )
    subject_candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id"))
    record_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_decisions.id"))
    event_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AutoResearchScorerInvocation(Base):
    """Append-only raw scorer invocation ledger for replayable score-all rounds."""

    __tablename__ = "autoresearch_scorer_invocations"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "candidate_id",
            "metric_name",
            "ordinal",
            name="uq_autoresearch_scorer_invocation_slot",
        ),
        CheckConstraint("ordinal > 0", name="autoresearch_scorer_invocation_positive_ordinal"),
        Index("ix_autoresearch_scorer_invocation_tool_call", "tool_call_id"),
        Index("ix_autoresearch_scorer_invocation_run_ordinal", "run_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id"), nullable=False)
    parent_candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id"))
    parent_sequence_sha256: Mapped[str | None] = mapped_column(String(64))
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_release_key: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tool_calls.id"), nullable=False)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_invocation_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


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
            "ix_evaluation_shadow_challenger_lookup",
            "subject_run_id",
            "candidate_id",
            "evidence_role",
            "model_release_key",
        ),
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
    # Candidate-level shadow/challenger observations may be produced by an
    # operational evidence run rather than by the candidate's scientific run.
    # Keep the subject run explicit so consumers never infer it from titles,
    # targets, sequences, or the ToolCall's operational run.
    subject_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiment_runs.id"), nullable=True
    )
    evidence_role: Mapped[str | None] = mapped_column(String(32))
    evidence_family: Mapped[str | None] = mapped_column(String(64))
    model_release_key: Mapped[str | None] = mapped_column(String(128))
    applicability_status: Mapped[str | None] = mapped_column(String(32))
    conflict_status: Mapped[str | None] = mapped_column(String(64))
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


class AutoResearchAction(Base):
    """Immutable machine-executable action selected for one evolution iteration."""

    __tablename__ = "autoresearch_actions"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "iteration_no",
            "branch_key",
            "action_ordinal",
            name="uq_autoresearch_action_slot",
        ),
        CheckConstraint("iteration_no >= 0", name="autoresearch_action_nonnegative_iteration"),
        CheckConstraint("action_ordinal > 0", name="autoresearch_action_positive_ordinal"),
        CheckConstraint(
            "action_kind IN ('point_edit', 'controlled_mix', 'de_novo')",
            name="autoresearch_action_kind",
        ),
        CheckConstraint(
            "(skill_revision_id IS NULL AND skill_experiment_id IS NULL) OR "
            "(skill_revision_id IS NOT NULL AND skill_experiment_id IS NOT NULL)",
            name="autoresearch_action_skill_binding_pair",
        ),
        Index("ix_autoresearch_action_iteration", "run_id", "iteration_no", "branch_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    iteration_no: Mapped[int] = mapped_column(Integer, nullable=False)
    branch_key: Mapped[str] = mapped_column(String(128), nullable=False)
    action_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    action_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    random_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    agent_decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_decisions.id"), nullable=False
    )
    rationale_text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_objectives_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    forbidden_changes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    action_spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    action_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    skill_revision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("skills.id"))
    skill_experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skill_experiments.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CandidateLineageEdge(Base):
    """Typed action-to-child edge, including every crossover source candidate."""

    __tablename__ = "candidate_lineage_edges"
    __table_args__ = (
        UniqueConstraint(
            "action_id",
            "child_candidate_id",
            "relation_role",
            "source_ordinal",
            name="uq_candidate_lineage_action_child_source",
        ),
        CheckConstraint("source_ordinal > 0", name="candidate_lineage_positive_ordinal"),
        CheckConstraint(
            "relation_role IN ("
            "'de_novo_origin', 'primary_parent', 'donor', 'backbone', 'target_module'"
            ")",
            name="candidate_lineage_role",
        ),
        CheckConstraint(
            "(relation_role = 'de_novo_origin' AND parent_candidate_id IS NULL) OR "
            "(relation_role <> 'de_novo_origin' AND parent_candidate_id IS NOT NULL)",
            name="candidate_lineage_parent_semantics",
        ),
        CheckConstraint(
            "parent_candidate_id IS NULL OR child_candidate_id <> parent_candidate_id",
            name="candidate_lineage_not_self",
        ),
        Index("ix_candidate_lineage_child", "child_candidate_id"),
        Index("ix_candidate_lineage_parent", "parent_candidate_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("autoresearch_actions.id"), nullable=False
    )
    child_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id"), nullable=False
    )
    parent_candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("candidates.id"))
    relation_role: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_spans_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    edge_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AutoResearchMetricDelta(Base):
    """One replayable child-versus-control comparison for one frozen metric."""

    __tablename__ = "autoresearch_metric_deltas"
    __table_args__ = (
        UniqueConstraint(
            "action_id",
            "child_candidate_id",
            "comparator_candidate_id",
            "metric_name",
            name="uq_autoresearch_metric_delta_identity",
        ),
        CheckConstraint(
            "child_candidate_id <> comparator_candidate_id",
            name="autoresearch_metric_delta_distinct_candidates",
        ),
        CheckConstraint(
            "parent_evaluation_id <> child_evaluation_id",
            name="autoresearch_metric_delta_distinct_evaluations",
        ),
        CheckConstraint(
            "comparison_kind IN ('numeric_delta', 'categorical_transition')",
            name="autoresearch_metric_delta_kind",
        ),
        CheckConstraint(
            "direction IN ('minimize', 'maximize', 'audit', 'categorical')",
            name="autoresearch_metric_delta_direction",
        ),
        CheckConstraint(
            "(comparison_kind = 'numeric_delta' AND numeric_delta IS NOT NULL) OR "
            "(comparison_kind = 'categorical_transition' AND numeric_delta IS NULL)",
            name="autoresearch_metric_delta_value_semantics",
        ),
        Index("ix_autoresearch_metric_delta_child", "child_candidate_id", "metric_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("autoresearch_actions.id"), nullable=False
    )
    child_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id"), nullable=False
    )
    comparator_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id"), nullable=False
    )
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluations.id"), nullable=False
    )
    child_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluations.id"), nullable=False
    )
    comparison_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    numeric_delta: Mapped[float | None] = mapped_column(Float)
    improved: Mapped[bool | None] = mapped_column(Boolean)
    comparison_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    delta_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AutoResearchArchiveVersion(Base):
    """Append-only version of one named branch-local AutoResearch archive."""

    __tablename__ = "autoresearch_archive_versions"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "iteration_no",
            "branch_key",
            "archive_name",
            name="uq_autoresearch_archive_version_identity",
        ),
        CheckConstraint("iteration_no >= 0", name="autoresearch_archive_nonnegative_iteration"),
        Index(
            "ix_autoresearch_archive_latest",
            "run_id",
            "branch_key",
            "archive_name",
            "iteration_no",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    iteration_no: Mapped[int] = mapped_column(Integer, nullable=False)
    branch_key: Mapped[str] = mapped_column(String(128), nullable=False)
    archive_name: Mapped[str] = mapped_column(String(128), nullable=False)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("autoresearch_archive_versions.id")
    )
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tool_calls.id"), nullable=False)
    snapshot_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AutoResearchArchiveMembership(Base):
    """Full add/retain/remove transition for one candidate at an archive version."""

    __tablename__ = "autoresearch_archive_memberships"
    __table_args__ = (
        UniqueConstraint(
            "archive_version_id",
            "member_ordinal",
            name="uq_autoresearch_archive_member_ordinal",
        ),
        CheckConstraint(
            "change_kind IN ('add', 'retain', 'remove')",
            name="autoresearch_archive_membership_change",
        ),
        CheckConstraint(
            "(change_kind = 'remove' AND is_active = false AND member_ordinal IS NULL) OR "
            "(change_kind IN ('add', 'retain') AND is_active = true AND member_ordinal > 0)",
            name="autoresearch_archive_membership_state",
        ),
        Index("ix_autoresearch_archive_membership_candidate", "candidate_id"),
    )

    archive_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("autoresearch_archive_versions.id"), primary_key=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id"), primary_key=True)
    change_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    member_ordinal: Mapped[int | None] = mapped_column(Integer)
    source_action_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("autoresearch_actions.id")
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    witness_candidate_ids_json: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AutoResearchCheckpoint(Base):
    """Typed extension of a run-stage checkpoint with an exact replay receipt."""

    __tablename__ = "autoresearch_checkpoints"
    __table_args__ = (
        UniqueConstraint("run_id", "iteration_no", name="uq_autoresearch_checkpoint_iteration"),
        CheckConstraint("iteration_no >= 0", name="autoresearch_checkpoint_nonnegative_iteration"),
        CheckConstraint(
            "score_all_candidate_count > 0 AND score_all_required_metric_count > 0",
            name="autoresearch_checkpoint_positive_score_all_counts",
        ),
        CheckConstraint(
            "score_all_expected_evaluation_count = "
            "score_all_candidate_count * score_all_required_metric_count",
            name="autoresearch_checkpoint_expected_score_all_count",
        ),
        CheckConstraint(
            "score_all_completed_evaluation_count = score_all_expected_evaluation_count",
            name="autoresearch_checkpoint_complete_score_all",
        ),
        CheckConstraint("replay_verified = true", name="autoresearch_checkpoint_replay_verified"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiment_runs.id"), nullable=False)
    iteration_no: Mapped[int] = mapped_column(Integer, nullable=False)
    run_stage_checkpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run_stage_checkpoints.id"), nullable=False, unique=True
    )
    agent_decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_decisions.id"), nullable=False
    )
    action_batch_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_before_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    archive_after_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    score_all_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    score_all_required_metric_count: Mapped[int] = mapped_column(Integer, nullable=False)
    score_all_expected_evaluation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    score_all_completed_evaluation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    next_controller_action: Mapped[str] = mapped_column(String(64), nullable=False)
    replay_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    replay_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    replay_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    receipt_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Skill(Base, TimestampMixin):
    """One immutable semantic/action revision in the evidence-gated skill library."""

    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("skill_key", "revision", name="uq_skill_key_revision"),
        UniqueConstraint("program_fingerprint", name="uq_skill_program_fingerprint"),
        CheckConstraint("revision > 0", name="skill_positive_revision"),
        CheckConstraint("state_version > 0", name="skill_positive_state_version"),
        CheckConstraint(
            "skill_kind IN ("
            "'edit_program', 'search_program', 'search_policy', 'generator_tool', 'composite'"
            ")",
            name="skill_kind",
        ),
        CheckConstraint(
            "lifecycle_state IN ("
            "'proposed', 'compiled', 'testing', 'promoted', 'active', "
            "'rejected', 'suspended', 'retired'"
            ")",
            name="skill_lifecycle_state",
        ),
        CheckConstraint(
            "lifecycle_state IN ('proposed', 'rejected') OR ("
            "compiled_ir_json IS NOT NULL AND compiler_version IS NOT NULL AND "
            "program_fingerprint IS NOT NULL AND family_fingerprint IS NOT NULL"
            ")",
            name="skill_compilation_state",
        ),
        CheckConstraint(
            "lifecycle_state NOT IN ('promoted', 'active', 'suspended', 'retired') OR "
            "(promotion_scope_json IS NOT NULL AND promotion_decision_json IS NOT NULL AND "
            "promotion_decision_sha256 IS NOT NULL)",
            name="skill_promotion_scope",
        ),
        CheckConstraint(
            "lifecycle_state <> 'active' OR active_library_sha256 IS NOT NULL",
            name="skill_active_library",
        ),
        CheckConstraint(
            "(planner_policy_json IS NULL AND planner_policy_sha256 IS NULL AND "
            "planner_policy_artifact_id IS NULL) OR "
            "(planner_policy_json IS NOT NULL AND planner_policy_sha256 IS NOT NULL AND "
            "planner_policy_artifact_id IS NOT NULL)",
            name="skill_planner_policy_binding",
        ),
        CheckConstraint(
            "canonical_skill_revision_id IS NULL OR canonical_skill_revision_id <> id",
            name="skill_canonical_not_self",
        ),
        CheckConstraint(
            "supersedes_skill_revision_id IS NULL OR supersedes_skill_revision_id <> id",
            name="skill_supersedes_not_self",
        ),
        Index("ix_skill_state_kind", "lifecycle_state", "skill_kind"),
        Index("ix_skill_family_fingerprint", "family_fingerprint"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    skill_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, default="proposed")
    semantic_spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    semantic_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    context_spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    context_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_effect_spec_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    expected_effect_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    compiled_ir_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    compiler_version: Mapped[str | None] = mapped_column(String(128))
    compiler_environment_sha256: Mapped[str | None] = mapped_column(String(64))
    program_fingerprint: Mapped[str | None] = mapped_column(String(64))
    family_fingerprint: Mapped[str | None] = mapped_column(String(64))
    canonical_skill_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skills.id")
    )
    supersedes_skill_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skills.id")
    )
    history_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_manifest_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    created_by_agent_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_decisions.id")
    )
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    promotion_scope_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    promotion_decision_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    promotion_decision_sha256: Mapped[str | None] = mapped_column(String(64))
    active_library_sha256: Mapped[str | None] = mapped_column(String(64))
    planner_policy_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    planner_policy_sha256: Mapped[str | None] = mapped_column(String(64))
    planner_policy_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id")
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class SkillExperiment(Base):
    """A frozen controlled edit or campaign evaluation of one Skill."""

    __tablename__ = "skill_experiments"
    __table_args__ = (
        CheckConstraint(
            "experiment_type IN ('paired_edit', 'campaign_ab')",
            name="skill_experiment_type",
        ),
        CheckConstraint(
            "(experiment_type = 'campaign_ab' AND baseline_run_id IS NOT NULL AND "
            "treatment_run_id IS NOT NULL AND harness_trial_id IS NOT NULL AND "
            "baseline_run_id <> treatment_run_id) OR "
            "(experiment_type = 'paired_edit' AND baseline_run_id IS NULL AND "
            "treatment_run_id IS NULL AND harness_trial_id IS NULL)",
            name="skill_experiment_type_campaign_bindings",
        ),
        CheckConstraint(
            "phase IN ('historical_replay', 'counterfactual', 'shadow', 'prospective')",
            name="skill_experiment_phase",
        ),
        CheckConstraint(
            "status IN ('draft', 'preregistered', 'running', 'completed', 'failed', "
            "'invalidated')",
            name="skill_experiment_status",
        ),
        CheckConstraint(
            "baseline_program_fingerprint <> treatment_program_fingerprint",
            name="skill_experiment_distinct_programs",
        ),
        CheckConstraint(
            "baseline_scorer_unit_budget >= 0 AND treatment_scorer_unit_budget >= 0 AND "
            "baseline_proposal_budget >= 0 AND treatment_proposal_budget >= 0 AND "
            "baseline_generator_tool_call_budget >= 0 AND "
            "treatment_generator_tool_call_budget >= 0",
            name="skill_experiment_nonnegative_budgets",
        ),
        CheckConstraint(
            "phase <> 'prospective' OR (testing_state_version IS NOT NULL AND "
            "testing_state_version > 0 AND blinding_manifest_artifact_id IS NOT NULL AND "
            "preregistered_at IS NOT NULL)",
            name="skill_experiment_prospective_testing_version",
        ),
        CheckConstraint(
            "baseline_scorer_units_consumed IS NULL OR baseline_scorer_units_consumed >= 0",
            name="skill_experiment_nonnegative_baseline_scorer_usage",
        ),
        CheckConstraint(
            "treatment_scorer_units_consumed IS NULL OR treatment_scorer_units_consumed >= 0",
            name="skill_experiment_nonnegative_treatment_scorer_usage",
        ),
        CheckConstraint(
            "baseline_proposals_consumed IS NULL OR baseline_proposals_consumed >= 0",
            name="skill_experiment_nonnegative_baseline_proposal_usage",
        ),
        CheckConstraint(
            "treatment_proposals_consumed IS NULL OR treatment_proposals_consumed >= 0",
            name="skill_experiment_nonnegative_treatment_proposal_usage",
        ),
        CheckConstraint(
            "baseline_generator_tool_calls_consumed IS NULL OR "
            "baseline_generator_tool_calls_consumed >= 0",
            name="skill_experiment_nonnegative_baseline_generator_usage",
        ),
        CheckConstraint(
            "treatment_generator_tool_calls_consumed IS NULL OR "
            "treatment_generator_tool_calls_consumed >= 0",
            name="skill_experiment_nonnegative_treatment_generator_usage",
        ),
        CheckConstraint(
            "status <> 'completed' OR ("
            "baseline_scorer_units_consumed IS NOT NULL AND "
            "treatment_scorer_units_consumed IS NOT NULL AND "
            "baseline_proposals_consumed IS NOT NULL AND "
            "treatment_proposals_consumed IS NOT NULL AND "
            "baseline_generator_tool_calls_consumed IS NOT NULL AND "
            "treatment_generator_tool_calls_consumed IS NOT NULL AND "
            "baseline_atomic_scorer_usage_json IS NOT NULL AND "
            "treatment_atomic_scorer_usage_json IS NOT NULL AND "
            "scorer_usage_ledger_artifact_id IS NOT NULL AND "
            "result_artifact_id IS NOT NULL AND "
            "replay_artifact_id IS NOT NULL AND "
            "denominator_complete = true AND replay_verified = true"
            ")",
            name="skill_experiment_completed_usage_ledger",
        ),
        CheckConstraint(
            "status <> 'completed' OR (receipt_sha256 IS NOT NULL AND finished_at IS NOT NULL)",
            name="skill_experiment_completed_receipt",
        ),
        CheckConstraint(
            "status <> 'completed' OR ("
            "scorer_usage_ledger_artifact_id <> result_artifact_id AND "
            "scorer_usage_ledger_artifact_id <> replay_artifact_id AND "
            "result_artifact_id <> replay_artifact_id)",
            name="skill_experiment_distinct_terminal_artifacts",
        ),
        CheckConstraint(
            "status <> 'completed' OR phase <> 'prospective' OR ("
            "baseline_scorer_units_consumed = baseline_scorer_unit_budget AND "
            "treatment_scorer_units_consumed = treatment_scorer_unit_budget"
            ")",
            name="skill_experiment_completed_prospective_budget_consumed",
        ),
        CheckConstraint(
            "status <> 'completed' OR ("
            "baseline_atomic_scorer_usage_json = baseline_atomic_scorer_budget_json AND "
            "treatment_atomic_scorer_usage_json = treatment_atomic_scorer_budget_json"
            ")",
            name="skill_experiment_completed_atomic_budget_consumed",
        ),
        CheckConstraint(
            "status <> 'completed' OR phase <> 'prospective' OR ("
            "baseline_proposals_consumed = baseline_proposal_budget AND "
            "treatment_proposals_consumed = treatment_proposal_budget AND "
            "baseline_generator_tool_calls_consumed = "
            "baseline_generator_tool_call_budget AND "
            "treatment_generator_tool_calls_consumed = "
            "treatment_generator_tool_call_budget"
            ")",
            name="skill_experiment_completed_prospective_proposals_consumed",
        ),
        CheckConstraint(
            "status = 'draft' OR ("
            "history_partition_artifact_id IS NOT NULL AND "
            "pair_or_seed_manifest_artifact_id IS NOT NULL AND "
            "budget_contract_artifact_id IS NOT NULL AND "
            "endpoint_contract_artifact_id IS NOT NULL AND "
            "evaluator_manifest_artifact_id IS NOT NULL"
            ")",
            name="skill_experiment_preregistration_artifacts",
        ),
        Index(
            "ix_skill_experiment_skill_phase",
            "skill_revision_id",
            "phase",
            "testing_state_version",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    skill_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id"), nullable=False)
    experiment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    testing_state_version: Mapped[int | None] = mapped_column(Integer)
    scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    context_spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    context_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    hypothesis_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    baseline_program_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    treatment_program_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    history_partition_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id")
    )
    pair_or_seed_manifest_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id")
    )
    budget_contract_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id")
    )
    endpoint_contract_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id")
    )
    evaluator_manifest_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id")
    )
    blinding_manifest_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id")
    )
    randomization_seed: Mapped[int | None] = mapped_column(BigInteger)
    baseline_scorer_unit_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    treatment_scorer_unit_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_atomic_scorer_budget_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    treatment_atomic_scorer_budget_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False
    )
    baseline_proposal_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    treatment_proposal_budget: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_generator_tool_call_budget: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    treatment_generator_tool_call_budget: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    baseline_scorer_units_consumed: Mapped[int | None] = mapped_column(Integer)
    treatment_scorer_units_consumed: Mapped[int | None] = mapped_column(Integer)
    baseline_atomic_scorer_usage_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    treatment_atomic_scorer_usage_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    baseline_proposals_consumed: Mapped[int | None] = mapped_column(Integer)
    treatment_proposals_consumed: Mapped[int | None] = mapped_column(Integer)
    baseline_generator_tool_calls_consumed: Mapped[int | None] = mapped_column(Integer)
    treatment_generator_tool_calls_consumed: Mapped[int | None] = mapped_column(Integer)
    scorer_usage_ledger_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id")
    )
    budget_shortfall_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    baseline_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiment_runs.id"))
    treatment_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiment_runs.id"))
    harness_trial_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("harness_trials.id"))
    preregistered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    replay_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    denominator_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    replay_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    receipt_sha256: Mapped[str | None] = mapped_column(String(64), unique=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SkillCampaignComparison(Base):
    """One replayable controlled baseline-versus-Skill campaign effect."""

    __tablename__ = "skill_campaign_comparisons"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "independent_unit_id",
            "endpoint_name",
            name="uq_skill_campaign_comparison_cell",
        ),
        CheckConstraint(
            "baseline_program_fingerprint <> treatment_program_fingerprint",
            name="skill_campaign_distinct_programs",
        ),
        CheckConstraint(
            "direction IN ('minimize', 'maximize')",
            name="skill_campaign_direction",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'incomparable')",
            name="skill_campaign_status",
        ),
        CheckConstraint(
            "(status = 'succeeded' AND baseline_raw_outcome IS NOT NULL AND "
            "treatment_raw_outcome IS NOT NULL AND raw_delta IS NOT NULL AND "
            "improvement_delta IS NOT NULL) OR "
            "(status <> 'succeeded' AND baseline_raw_outcome IS NULL AND "
            "treatment_raw_outcome IS NULL AND raw_delta IS NULL AND "
            "improvement_delta IS NULL)",
            name="skill_campaign_effect_value_semantics",
        ),
        CheckConstraint(
            "baseline_outcome_artifact_id <> treatment_outcome_artifact_id AND "
            "baseline_receipt_artifact_id <> treatment_receipt_artifact_id AND "
            "baseline_harness_outcome_id <> treatment_harness_outcome_id AND "
            "baseline_search_tool_call_id <> treatment_search_tool_call_id AND "
            "baseline_portfolio_artifact_id <> treatment_portfolio_artifact_id",
            name="skill_campaign_distinct_arm_artifacts",
        ),
        CheckConstraint(
            "denominator_complete = true AND replay_verified = true",
            name="skill_campaign_complete_replay",
        ),
        Index(
            "ix_skill_campaign_comparison_experiment",
            "experiment_id",
            "independent_unit_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skill_experiments.id"), nullable=False
    )
    independent_unit_id: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint_name: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint_family: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_version: Mapped[str] = mapped_column(String(128), nullable=False)
    initial_pool_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    seed_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_contract_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    random_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    baseline_program_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    treatment_program_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_frozen_budget_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    treatment_frozen_budget_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    baseline_harness_outcome_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("harness_outcomes.id"), nullable=False
    )
    treatment_harness_outcome_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("harness_outcomes.id"), nullable=False
    )
    baseline_outcome_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    treatment_outcome_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    baseline_receipt_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    treatment_receipt_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    baseline_search_tool_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_calls.id"), nullable=False
    )
    treatment_search_tool_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_calls.id"), nullable=False
    )
    baseline_portfolio_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    treatment_portfolio_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    baseline_raw_outcome: Mapped[float | None] = mapped_column(Float)
    treatment_raw_outcome: Mapped[float | None] = mapped_column(Float)
    raw_delta: Mapped[float | None] = mapped_column(Float)
    improvement_delta: Mapped[float | None] = mapped_column(Float)
    denominator_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    replay_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    comparison_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SkillEvidence(Base):
    """One immutable endpoint/context observation or effect/decision snapshot."""

    __tablename__ = "skill_evidence"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "independent_unit_id",
            "endpoint_name",
            name="uq_skill_evidence_experiment_cell",
        ),
        CheckConstraint(
            "evidence_kind IN ("
            "'historical_hypothesis', 'paired_edit_effect', 'campaign_effect', "
            "'failure_observation', 'equivalence_assertion', "
            "'effect_estimate_snapshot', 'promotion_decision'"
            ")",
            name="skill_evidence_kind",
        ),
        CheckConstraint(
            "evidence_grade IN ("
            "'retrospective_observational', 'counterfactual', 'shadow', "
            "'prospective_controlled', 'external_experimental'"
            ")",
            name="skill_evidence_grade",
        ),
        CheckConstraint(
            "comparison_kind IN ('numeric_delta', 'categorical_transition', 'set_outcome')",
            name="skill_evidence_comparison_kind",
        ),
        CheckConstraint(
            "direction IN ('minimize', 'maximize', 'audit', 'categorical')",
            name="skill_evidence_direction",
        ),
        CheckConstraint(
            "sample_size >= 0 AND denominator >= sample_size",
            name="skill_evidence_complete_denominator",
        ),
        CheckConstraint(
            "promotion_eligible = false OR evidence_grade = 'prospective_controlled'",
            name="skill_evidence_promotion_grade",
        ),
        CheckConstraint(
            "promotion_eligible = false OR (experiment_id IS NOT NULL AND "
            "replay_artifact_id IS NOT NULL AND "
            "((source_autoresearch_metric_delta_id IS NOT NULL AND "
            "source_skill_campaign_comparison_id IS NULL AND "
            "source_harness_outcome_id IS NULL) OR "
            "(source_autoresearch_metric_delta_id IS NULL AND "
            "source_skill_campaign_comparison_id IS NOT NULL AND "
            "source_harness_outcome_id IS NULL)))",
            name="skill_evidence_promotion_typed_source",
        ),
        CheckConstraint(
            "(source_autoresearch_metric_delta_id IS NULL AND "
            "source_skill_campaign_comparison_id IS NULL) OR "
            "(source_autoresearch_metric_delta_id IS NULL AND "
            "source_harness_outcome_id IS NULL) OR "
            "(source_skill_campaign_comparison_id IS NULL AND "
            "source_harness_outcome_id IS NULL)",
            name="skill_evidence_at_most_one_typed_source",
        ),
        Index(
            "ix_skill_evidence_effect_context",
            "skill_revision_id",
            "context_sha256",
            "endpoint_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("skills.id"), nullable=False)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("skill_experiments.id"))
    evidence_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_grade: Mapped[str] = mapped_column(String(32), nullable=False)
    promotion_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    context_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    program_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_family: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_name: Mapped[str] = mapped_column(String(128), nullable=False)
    independent_unit_id: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_version: Mapped[str] = mapped_column(String(128), nullable=False)
    comparison_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    effect_value: Mapped[float | None] = mapped_column(Float)
    raw_effect_value: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(64))
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    denominator: Mapped[int] = mapped_column(Integer, nullable=False)
    uncertainty_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    limitations_json: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    source_autoresearch_metric_delta_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("autoresearch_metric_deltas.id")
    )
    source_skill_campaign_comparison_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skill_campaign_comparisons.id")
    )
    source_harness_outcome_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("harness_outcomes.id")
    )
    source_artifact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artifacts.id"), nullable=False
    )
    effect_model_version: Mapped[str | None] = mapped_column(String(128))
    effect_model_config_sha256: Mapped[str | None] = mapped_column(String(64))
    replay_artifact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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


class EvidenceArtifactLocation(Base):
    """Append-only locator witness for one ToolCall-to-Artifact evidence edge.

    An Artifact is globally content-addressed by SHA-256, so the same bytes may
    legitimately be observed at multiple immutable CAS paths.  The edge stays
    deduplicated by ToolCall/Artifact/role while this child table preserves
    every exact requested location (including multiple aliases in one call).
    """

    __tablename__ = "evidence_artifact_locations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tool_call_id", "artifact_id", "role"],
            [
                "evidence_artifacts.tool_call_id",
                "evidence_artifacts.artifact_id",
                "evidence_artifacts.role",
            ],
            name="fk_evidence_artifact_location_edge",
        ),
    )

    tool_call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    role: Mapped[str] = mapped_column(String(64), primary_key=True)
    location_witness_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    requested_storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    location_metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


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
