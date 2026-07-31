import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class ExperimentRun(Base, TimestampMixin):
    __tablename__ = "experiment_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("targets.id"), nullable=False)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    spec_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    temporal_workflow_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    temporal_run_id: Mapped[str | None] = mapped_column(String(255))
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("experiment_runs.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    target: Mapped[Target] = relationship()
    candidates: Mapped[list["Candidate"]] = relationship(back_populates="run")


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

    tool_call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tool_calls.id"), primary_key=True
    )
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(64), primary_key=True)


class PocketEvidence(Base, TimestampMixin):
    __tablename__ = "pocket_evidence"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("targets.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(128))
    residue_indices: Mapped[list[int]] = mapped_column(JSONB, default=list, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


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
