from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import ExperimentRun, Target, ToolCall
from pepagent.db.repository import ExperimentRepository
from pepagent.domain.enums import EvaluationStatus, RunStatus
from pepagent.provenance.hashing import sha256_json

TARGET_ACCESSIONS = {
    "acea": "P0A9G6",
    "angpt1": "NP_001272991.1",
    "fgf2": "NP_032032.1",
    "gyra": "NP_416734.1",
    "pbp2a": "WP_308061015.1",
    "vegfa": "NP_001020421.2",
}
OPERATIONAL_RUN_NAMESPACE = uuid.UUID("5ff0c144-b9e8-4ee4-928e-ea7c8764a0ad")


class OperationalCallRecord(BaseModel):
    """One real invocation recorded without attaching it to a failed formal run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["ampgent.autoresearch-operational-call.1"] = (
        "ampgent.autoresearch-operational-call.1"
    )
    operation_key: str = Field(min_length=1, max_length=512)
    target_key: Literal["acea", "angpt1", "fgf2", "gyra", "pbp2a", "vegfa"]
    purpose: Literal[
        "generation",
        "score_all",
        "challenger",
        "structure",
        "rosetta",
        "audit_reconciliation",
    ]
    tool_name: str = Field(min_length=1, max_length=128)
    tool_version: str = Field(min_length=1, max_length=128)
    status: Literal["running", "succeeded", "failed"]
    attempt: int = Field(default=1, ge=1)
    input_payload: dict[str, Any]
    parameters: dict[str, Any] = Field(default_factory=dict)
    execution_context: dict[str, Any]
    output_payload: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    model_uri: str | None = None
    weights_identity: str | None = None
    random_seed: int | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    actor: str = Field(default="ampgent-operator", min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> OperationalCallRecord:
        if not self.execution_context:
            raise ValueError("operational call execution context must not be empty")
        if self.status == "running" and (
            self.output_payload is not None
            or self.error is not None
            or self.finished_at is not None
        ):
            raise ValueError("running operational call cannot carry a terminal payload")
        if self.status == "succeeded" and (
            self.output_payload is None or self.error is not None
        ):
            raise ValueError("succeeded operational call requires output and forbids error")
        if self.status == "failed" and (self.error is None or self.output_payload is not None):
            raise ValueError("failed operational call requires error and forbids output")
        for value in (self.queued_at, self.started_at, self.finished_at):
            if value is not None and value.utcoffset() is None:
                raise ValueError("operational call timestamps must include a timezone")
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("operational call finished before it started")
        return self


def operational_run_id(record: OperationalCallRecord) -> uuid.UUID:
    return uuid.uuid5(
        OPERATIONAL_RUN_NAMESPACE,
        f"{record.target_key}:{record.operation_key}",
    )


async def _resolve_target(session: AsyncSession, target_key: str) -> Target:
    accession = TARGET_ACCESSIONS[target_key]
    targets = list(
        await session.scalars(select(Target).where(Target.accession == accession))
    )
    if len(targets) != 1:
        raise ValueError(
            f"expected exactly one target for {target_key}/{accession}, found {len(targets)}"
        )
    return targets[0]


def _run_spec(record: OperationalCallRecord, target_id: uuid.UUID) -> dict[str, Any]:
    return {
        "schema_version": "ampgent.autoresearch-operational-run.1",
        "operation_key": record.operation_key,
        "target_key": record.target_key,
        "target_id": str(target_id),
        "purpose": record.purpose,
        "tool_name": record.tool_name,
        "formal_scientific_run": False,
        "authoritative_call_record": "postgresql",
    }


def _terminal_event_payload(record: OperationalCallRecord, call_id: uuid.UUID) -> dict[str, Any]:
    return {
        "tool_call_id": str(call_id),
        "operation_key": record.operation_key,
        "target_key": record.target_key,
        "purpose": record.purpose,
        "status": record.status,
        "attempt": record.attempt,
        "execution_context": record.execution_context,
        "output": record.output_payload,
        "error": record.error,
    }


async def persist_operational_call(
    session: AsyncSession,
    record: OperationalCallRecord,
) -> tuple[ExperimentRun, ToolCall]:
    """Persist one running or terminal invocation and its append-only lifecycle event."""

    run_id = operational_run_id(record)
    lock_id = int.from_bytes(run_id.bytes[:8], byteorder="big", signed=True)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": lock_id},
    )
    target = await _resolve_target(session, record.target_key)
    spec = _run_spec(record, target.id)
    now = datetime.now(UTC)
    repository = ExperimentRepository(session)
    run = await session.get(ExperimentRun, run_id, with_for_update=True)
    if run is None:
        run = ExperimentRun(
            id=run_id,
            target_id=target.id,
            spec_json=spec,
            spec_sha256=sha256_json(spec),
            status=(RunStatus.RUNNING if record.status == "running" else record.status),
            started_at=record.started_at or now,
            finished_at=(record.finished_at or now) if record.status != "running" else None,
        )
        session.add(run)
        await session.flush()
        await repository.append_event(
            "run",
            run.id,
            "operational_run.created",
            record.actor,
            spec,
        )
    elif (
        run.target_id != target.id
        or run.spec_json != spec
        or run.spec_sha256 != sha256_json(spec)
    ):
        raise ValueError("operational run identity drifted")
    if record.status != "running":
        if run.status not in {RunStatus.RUNNING, record.status}:
            raise ValueError("operational run terminal state cannot be rewritten")
        run.status = record.status
        run.finished_at = record.finished_at or now

    call_input = {
        "operation_key": record.operation_key,
        "target_key": record.target_key,
        "purpose": record.purpose,
        "payload": record.input_payload,
    }
    call_parameters = {
        **record.parameters,
        "execution_context": record.execution_context,
    }
    idempotency_key = sha256_json(
        {"run_id": str(run.id), "operation_key": record.operation_key}
    )
    expected_identity = {
        "run_id": run.id,
        "tool_name": record.tool_name,
        "tool_version": record.tool_version,
        "model_uri": record.model_uri,
        "weights_sha256": record.weights_identity,
        "environment_sha256": sha256_json(record.execution_context),
        "idempotency_key": idempotency_key,
        "input_sha256": sha256_json(call_input),
        "input_json": call_input,
        "parameters_json": call_parameters,
        "random_seed": record.random_seed,
        "attempt": record.attempt,
    }
    call = await session.scalar(
        select(ToolCall).where(ToolCall.idempotency_key == idempotency_key)
    )
    if call is None:
        call = ToolCall(
            **expected_identity,
            status=record.status,
            queued_at=record.queued_at or record.started_at or now,
            started_at=record.started_at or now,
            finished_at=(record.finished_at or now) if record.status != "running" else None,
            output_sha256=(
                sha256_json(record.output_payload)
                if record.output_payload is not None
                else None
            ),
            error_json=record.error,
        )
        session.add(call)
        await session.flush()
        await repository.append_event(
            "run",
            run.id,
            f"operational.call.{record.status}",
            record.actor,
            _terminal_event_payload(record, call.id),
        )
        return run, call

    if any(getattr(call, key) != value for key, value in expected_identity.items()):
        raise ValueError("operational call retry identity drifted")
    expected_output_sha = (
        sha256_json(record.output_payload) if record.output_payload is not None else None
    )
    if call.status == record.status:
        if call.output_sha256 != expected_output_sha or call.error_json != record.error:
            raise ValueError("operational call retry terminal payload drifted")
        return run, call
    if call.status != EvaluationStatus.RUNNING or record.status == "running":
        raise ValueError("operational call terminal state cannot be rewritten")
    call.status = record.status
    call.finished_at = record.finished_at or now
    call.output_sha256 = expected_output_sha
    call.error_json = record.error
    await session.flush()
    await repository.append_event(
        "run",
        run.id,
        f"operational.call.{record.status}",
        record.actor,
        _terminal_event_payload(record, call.id),
    )
    return run, call


__all__ = [
    "OperationalCallRecord",
    "operational_run_id",
    "persist_operational_call",
]
