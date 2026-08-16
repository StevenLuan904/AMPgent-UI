from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import (
    ExperimentRun,
    ExperimentRunTargetBranch,
    RunStageCheckpoint,
    Target,
    TargetPocket,
)
from pepagent.provenance.hashing import sha256_json
from pepagent.v38_run_control import RunControlDecision, StageName


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TargetBranchBinding(FrozenModel):
    branch_order: int = Field(ge=1)
    branch_key: str = Field(min_length=1)
    target_id: UUID
    panel_role: str
    qualification_witness_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_pocket_id: UUID
    wrong_pocket_id: UUID
    evidence_namespace: str = Field(min_length=1)
    metadata: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_pockets(self) -> TargetBranchBinding:
        if self.native_pocket_id == self.wrong_pocket_id:
            raise ValueError("native and wrong pockets must differ")
        return self


class MultiTargetRunBindingReceipt(FrozenModel):
    schema_version: str = "v38.multitarget-run-binding.1"
    run_id: UUID
    branches: tuple[TargetBranchBinding, ...] = Field(min_length=2, max_length=6)
    shared_sequence_cohort_required: bool = True
    peptide_outcomes_used_for_target_selection: bool = False

    @model_validator(mode="after")
    def validate_branches(self) -> MultiTargetRunBindingReceipt:
        orders = [item.branch_order for item in self.branches]
        if orders != list(range(1, len(self.branches) + 1)):
            raise ValueError("branch order must be contiguous")
        for values in (
            [item.branch_key for item in self.branches],
            [item.target_id for item in self.branches],
            [item.evidence_namespace for item in self.branches],
        ):
            if len(values) != len(set(values)):
                raise ValueError("target branch identities must be unique")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class StageCheckpointReceipt(FrozenModel):
    schema_version: str = "v38.stage-checkpoint.1"
    run_id: UUID
    stage: StageName
    stage_order: int = Field(ge=0)
    observation_no: int = Field(ge=1)
    durable_count: int = Field(ge=0)
    expected_durable_count: int = Field(ge=0)
    stage_status: str
    decision: RunControlDecision
    observed_at: datetime

    @model_validator(mode="after")
    def validate_time(self) -> StageCheckpointReceipt:
        if self.observed_at.tzinfo is None:
            raise ValueError("checkpoint timestamp must be timezone-aware")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


async def persist_multitarget_run_binding(
    session: AsyncSession,
    receipt: MultiTargetRunBindingReceipt,
) -> tuple[ExperimentRunTargetBranch, ...]:
    run = await session.get(ExperimentRun, receipt.run_id)
    if run is None:
        raise ValueError("multi-target run does not exist")
    existing = (
        (
            await session.execute(
                select(ExperimentRunTargetBranch)
                .where(ExperimentRunTargetBranch.run_id == receipt.run_id)
                .order_by(ExperimentRunTargetBranch.branch_order)
            )
        )
        .scalars()
        .all()
    )
    if existing:
        raise ValueError("multi-target branches are immutable once persisted")
    rows: list[ExperimentRunTargetBranch] = []
    for binding in receipt.branches:
        target = await session.get(Target, binding.target_id)
        native = await session.get(TargetPocket, binding.native_pocket_id)
        wrong = await session.get(TargetPocket, binding.wrong_pocket_id)
        if target is None or native is None or wrong is None:
            raise ValueError(f"target branch references missing rows: {binding.branch_key}")
        if native.target_id != target.id or wrong.target_id != target.id:
            raise ValueError(f"target branch pocket ownership mismatch: {binding.branch_key}")
        row = ExperimentRunTargetBranch(
            run_id=receipt.run_id,
            branch_order=binding.branch_order,
            branch_key=binding.branch_key,
            target_id=binding.target_id,
            panel_role=binding.panel_role,
            qualification_witness_sha256=binding.qualification_witness_sha256,
            coordinate_sha256=binding.coordinate_sha256,
            native_pocket_id=binding.native_pocket_id,
            wrong_pocket_id=binding.wrong_pocket_id,
            evidence_namespace=binding.evidence_namespace,
            status="frozen",
            metadata_json={
                **binding.metadata,
                "binding_receipt_sha256": receipt.sha256(),
                "shared_sequence_cohort_required": receipt.shared_sequence_cohort_required,
            },
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    return tuple(rows)


async def persist_stage_checkpoint(
    session: AsyncSession,
    receipt: StageCheckpointReceipt,
) -> RunStageCheckpoint:
    run = await session.get(ExperimentRun, receipt.run_id)
    if run is None:
        raise ValueError("checkpoint run does not exist")
    row = RunStageCheckpoint(
        run_id=receipt.run_id,
        stage_name=receipt.stage,
        stage_order=receipt.stage_order,
        observation_no=receipt.observation_no,
        durable_count=receipt.durable_count,
        expected_durable_count=receipt.expected_durable_count,
        stage_status=receipt.stage_status,
        controller_action=receipt.decision.action,
        reasons_json=list(receipt.decision.reasons),
        tasks_json=list(receipt.decision.tasks),
        receipt_sha256=receipt.sha256(),
        observed_at=receipt.observed_at,
    )
    session.add(row)
    await session.flush()
    return row
