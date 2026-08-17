from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import (
    Candidate,
    CandidateOccurrence,
    ExperimentRun,
    ExperimentRunTargetBranch,
    RunStageCheckpoint,
    Target,
    TargetPocket,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.domain.enums import EvaluationStatus
from pepagent.provenance.hashing import sha256_json
from pepagent.v38_run_control import RunControlDecision, StageName
from pepagent.v38_science_execution import (
    ScoreAllProposalCohort,
    V38SequenceExecutionContract,
)


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


class GeneratorCellToolBinding(FrozenModel):
    cell_ordinal: int = Field(ge=0)
    generator_id: str = Field(min_length=1)
    seed: int
    tool_call_id: UUID
    opaque_arm_label: str = Field(min_length=1, max_length=64)


class ScoreAllCohortPersistenceReceipt(FrozenModel):
    schema_version: str = "v38.score-all-cohort-persistence.1"
    run_id: UUID
    execution_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: str
    raw_occurrence_count: int = Field(ge=0)
    promoted_candidate_count: int = Field(ge=0)
    invalid_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    tool_call_ids: tuple[UUID, ...]

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


def _validate_score_all_bindings(
    contract: V38SequenceExecutionContract,
    bindings: tuple[GeneratorCellToolBinding, ...],
) -> dict[tuple[str, int], GeneratorCellToolBinding]:
    if [item.cell_ordinal for item in bindings] != list(range(len(contract.cells))):
        raise ValueError("generator persistence binding ordinals must be contiguous")
    by_identity = {(item.generator_id, item.seed): item for item in bindings}
    expected = {(cell.generator_id, cell.seed) for cell in contract.cells}
    if len(by_identity) != len(bindings) or set(by_identity) != expected:
        raise ValueError("generator persistence bindings do not match the frozen contract")
    if len({item.tool_call_id for item in bindings}) != len(bindings):
        raise ValueError("each generator cell requires a distinct ToolCall")
    if len({item.opaque_arm_label for item in bindings}) != len(bindings):
        raise ValueError("each generator cell requires a distinct opaque arm label")
    for cell in contract.cells:
        if by_identity[(cell.generator_id, cell.seed)].cell_ordinal != cell.ordinal:
            raise ValueError("generator persistence binding ordinal drifted")
    return by_identity


def _score_all_receipt(
    *,
    run_id: UUID,
    contract: V38SequenceExecutionContract,
    cohort: ScoreAllProposalCohort,
    bindings: tuple[GeneratorCellToolBinding, ...],
    action: str,
) -> ScoreAllCohortPersistenceReceipt:
    return ScoreAllCohortPersistenceReceipt(
        run_id=run_id,
        execution_contract_sha256=contract.sha256(),
        cohort_sha256=cohort.sha256(),
        action=action,
        raw_occurrence_count=cohort.raw_occurrence_count,
        promoted_candidate_count=cohort.promoted_unique_count,
        invalid_count=cohort.invalid_count,
        duplicate_count=cohort.duplicate_count,
        tool_call_ids=tuple(item.tool_call_id for item in bindings),
    )


async def persist_score_all_proposal_cohort(
    session: AsyncSession,
    *,
    run_id: UUID,
    contract: V38SequenceExecutionContract,
    cohort: ScoreAllProposalCohort,
    bindings: tuple[GeneratorCellToolBinding, ...],
) -> ScoreAllCohortPersistenceReceipt:
    """Persist all raw proposals and every valid unique candidate atomically.

    The caller owns the transaction. A complete byte-identical retry is recovered;
    any partial pre-existing occurrence set fails closed instead of being backfilled.
    """
    if cohort.execution_contract_sha256 != contract.sha256():
        raise ValueError("score-all cohort does not match the execution contract")
    if cohort.raw_occurrence_count != contract.expected_raw_occurrences:
        raise ValueError("score-all cohort does not cover the frozen raw budget")
    by_identity = _validate_score_all_bindings(contract, bindings)
    run = await session.get(ExperimentRun, run_id)
    if run is None:
        raise ValueError("score-all cohort run does not exist")
    calls: dict[UUID, ToolCall] = {}
    for binding in bindings:
        call = await session.get(ToolCall, binding.tool_call_id)
        if call is None or call.run_id != run_id:
            raise ValueError("generator ToolCall is missing or cross-run")
        if call.status != EvaluationStatus.SUCCEEDED:
            raise ValueError("generator ToolCall is not durably completed")
        calls[call.id] = call

    existing = (
        (
            await session.execute(
                select(CandidateOccurrence)
                .where(CandidateOccurrence.tool_call_id.in_(tuple(calls)))
                .order_by(
                    CandidateOccurrence.tool_call_id,
                    CandidateOccurrence.occurrence_rank,
                )
            )
        )
        .scalars()
        .all()
    )
    if existing:
        if len(existing) != cohort.raw_occurrence_count:
            raise ValueError("partial score-all occurrence persistence detected")
        existing_by_key = {
            (item.tool_call_id, item.occurrence_rank): item for item in existing
        }
        for proposal in cohort.occurrences:
            binding = by_identity[(proposal.generator_id, proposal.seed)]
            row = existing_by_key.get((binding.tool_call_id, proposal.raw_rank))
            if row is None:
                raise ValueError("score-all occurrence recovery set is incomplete")
            expected_metadata = {
                "schema_version": cohort.schema_version,
                "cohort_sha256": cohort.sha256(),
                "execution_contract_sha256": contract.sha256(),
                "source_ordinal": proposal.source_ordinal,
                "disposition": proposal.disposition,
                "duplicate_of_source_ordinal": proposal.duplicate_of_source_ordinal,
            }
            if (
                row.run_id != run_id
                or row.opaque_arm_label != binding.opaque_arm_label
                or row.sequence != proposal.normalized_sequence
                or row.sequence_sha256 != proposal.sequence_sha256
                or row.metadata_json != expected_metadata
                or (row.candidate_id is not None) != proposal.promoted_for_scoring
            ):
                raise ValueError("score-all occurrence recovery payload drifted")
        return _score_all_receipt(
            run_id=run_id,
            contract=contract,
            cohort=cohort,
            bindings=bindings,
            action="recovered_complete",
        )

    repository = ExperimentRepository(session)
    candidates_by_sha: dict[str, Candidate] = {}
    for proposal in cohort.occurrences:
        binding = by_identity[(proposal.generator_id, proposal.seed)]
        candidate: Candidate | None = None
        if proposal.promoted_for_scoring:
            candidate = await repository.add_candidate(
                run_id=run_id,
                sequence=proposal.normalized_sequence,
                generation=0,
                proposal_rank=proposal.source_ordinal,
                generator_call_id=binding.tool_call_id,
                metadata={
                    "schema_version": cohort.schema_version,
                    "cohort_sha256": cohort.sha256(),
                    "execution_contract_sha256": contract.sha256(),
                    "source_ordinal": proposal.source_ordinal,
                    "generator_id": proposal.generator_id,
                    "seed": proposal.seed,
                    "raw_rank": proposal.raw_rank,
                    "score_all_sequence_metrics_required": True,
                },
                actor="v38-score-all-persistence",
            )
            candidates_by_sha[proposal.sequence_sha256] = candidate
        await repository.record_candidate_occurrence(
            run_id=run_id,
            tool_call_id=binding.tool_call_id,
            parent_candidate_id=None,
            occurrence_rank=proposal.raw_rank,
            occurrence_kind="de_novo",
            opaque_arm_label=binding.opaque_arm_label,
            sequence=proposal.normalized_sequence,
            candidate_id=candidate.id if candidate is not None else None,
            metadata={
                "schema_version": cohort.schema_version,
                "cohort_sha256": cohort.sha256(),
                "execution_contract_sha256": contract.sha256(),
                "source_ordinal": proposal.source_ordinal,
                "disposition": proposal.disposition,
                "duplicate_of_source_ordinal": proposal.duplicate_of_source_ordinal,
            },
        )
    if len(candidates_by_sha) != cohort.promoted_unique_count:
        raise ValueError("promoted candidate persistence count drifted")
    receipt = _score_all_receipt(
        run_id=run_id,
        contract=contract,
        cohort=cohort,
        bindings=bindings,
        action="persisted",
    )
    await repository.append_event(
        "run",
        run_id,
        "v38.score_all_cohort.persisted",
        "v38-score-all-persistence",
        receipt.model_dump(mode="json"),
    )
    return receipt


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
