from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from math import floor
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import (
    AgentDecision,
    Candidate,
    CandidateOccurrence,
    Evaluation,
    EvidenceArtifact,
    ExperimentRun,
    LifecycleEvent,
    Target,
    ToolCall,
)
from pepagent.provenance.hashing import sha256_json, sha256_text

TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
DEFAULT_KNOWLEDGE_PROVIDER_TASK_ID = "019fad3e-76b8-7e32-8455-d2e9b31d33e5"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _canonical_rows(rows: Iterable[Iterable[object]]) -> list[list[str | None]]:
    return [[str(value) if value is not None else None for value in row] for row in rows]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HistoricalRunSummary(FrozenModel):
    run_id: UUID
    target_id: UUID
    target_name: str
    status: Literal["succeeded", "failed", "cancelled"]
    spec_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formal_submission_key: str | None = None
    parent_run_id: UUID | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime
    candidate_count: int = Field(ge=0)
    occurrence_count: int = Field(ge=0)
    evaluation_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    succeeded_tool_call_count: int = Field(ge=0)
    failed_tool_call_count: int = Field(ge=0)
    decision_count: int = Field(ge=0)
    evidence_link_count: int = Field(ge=0)
    distinct_artifact_count: int = Field(ge=0)
    lifecycle_event_count: int = Field(ge=0)
    evidence_graph_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminal_event_type: str
    historical_role: Literal["decision_replay", "failure_denominator", "cancelled_denominator"]
    output_reuse_forbidden: Literal[True] = True

    @model_validator(mode="after")
    def validate_terminal_role(self) -> HistoricalRunSummary:
        expected_role = {
            "succeeded": "decision_replay",
            "failed": "failure_denominator",
            "cancelled": "cancelled_denominator",
        }[self.status]
        if self.historical_role != expected_role:
            raise ValueError("historical_role must preserve the terminal status denominator")
        if self.created_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("historical timestamps must be timezone-aware")
        if self.finished_at < self.created_at:
            raise ValueError("finished_at precedes created_at")
        if self.succeeded_tool_call_count + self.failed_tool_call_count > self.tool_call_count:
            raise ValueError("tool-call outcome counts exceed the total")
        return self


class HistoricalEvidenceSnapshot(FrozenModel):
    schema_version: Literal["v38.history-snapshot.1"] = "v38.history-snapshot.1"
    history_cutoff_at: datetime
    terminal_runs: tuple[HistoricalRunSummary, ...]
    terminal_run_count: int = Field(ge=0)
    excluded_nonterminal_run_ids: tuple[UUID, ...] = ()
    complete_terminal_denominator: Literal[True] = True
    immutable_reference_only: Literal[True] = True
    candidate_copy_or_backfill_forbidden: Literal[True] = True
    holdout_outcome_access_forbidden: Literal[True] = True

    @model_validator(mode="after")
    def validate_snapshot(self) -> HistoricalEvidenceSnapshot:
        if self.terminal_run_count != len(self.terminal_runs):
            raise ValueError("terminal_run_count does not match the snapshot")
        run_ids = [item.run_id for item in self.terminal_runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("duplicate terminal run in history snapshot")
        ordered = sorted(self.terminal_runs, key=lambda item: (item.created_at, str(item.run_id)))
        if list(self.terminal_runs) != ordered:
            raise ValueError("terminal runs must be in deterministic chronological order")
        if set(run_ids).intersection(self.excluded_nonterminal_run_ids):
            raise ValueError("a run cannot be both terminal and excluded")
        if any(item.finished_at > self.history_cutoff_at for item in self.terminal_runs):
            raise ValueError("snapshot includes evidence after history_cutoff_at")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


async def _count(session: AsyncSession, statement: object) -> int:
    return int((await session.scalar(statement)) or 0)


async def build_historical_evidence_snapshot(
    session: AsyncSession,
    *,
    history_cutoff_at: datetime,
) -> HistoricalEvidenceSnapshot:
    """Build an immutable history index without copying any candidate or outcome payload."""

    if history_cutoff_at.tzinfo is None:
        raise ValueError("history_cutoff_at must be timezone-aware")
    cutoff_utc = history_cutoff_at.astimezone(UTC)
    database_cutoff = cutoff_utc.replace(tzinfo=None)
    rows = (
        await session.execute(
            select(ExperimentRun, Target.name)
            .join(Target, Target.id == ExperimentRun.target_id)
            .where(ExperimentRun.created_at <= database_cutoff)
            .order_by(ExperimentRun.created_at, ExperimentRun.id)
        )
    ).all()
    terminal: list[HistoricalRunSummary] = []
    excluded: list[UUID] = []
    for run, target_name in rows:
        if run.status not in TERMINAL_RUN_STATUSES or run.finished_at is None:
            excluded.append(run.id)
            continue
        candidate_count = await _count(
            session, select(func.count(Candidate.id)).where(Candidate.run_id == run.id)
        )
        occurrence_count = await _count(
            session,
            select(func.count(CandidateOccurrence.id)).where(CandidateOccurrence.run_id == run.id),
        )
        evaluation_count = await _count(
            session,
            select(func.count(Evaluation.id))
            .join(Candidate, Candidate.id == Evaluation.candidate_id)
            .where(Candidate.run_id == run.id),
        )
        tool_call_count = await _count(
            session, select(func.count(ToolCall.id)).where(ToolCall.run_id == run.id)
        )
        succeeded_tool_call_count = await _count(
            session,
            select(func.count(ToolCall.id)).where(
                ToolCall.run_id == run.id, ToolCall.status == "succeeded"
            ),
        )
        failed_tool_call_count = await _count(
            session,
            select(func.count(ToolCall.id)).where(
                ToolCall.run_id == run.id, ToolCall.status == "failed"
            ),
        )
        decision_count = await _count(
            session, select(func.count(AgentDecision.id)).where(AgentDecision.run_id == run.id)
        )
        evidence_link_count = await _count(
            session,
            select(func.count())
            .select_from(EvidenceArtifact)
            .join(ToolCall, ToolCall.id == EvidenceArtifact.tool_call_id)
            .where(ToolCall.run_id == run.id),
        )
        distinct_artifact_count = await _count(
            session,
            select(func.count(func.distinct(EvidenceArtifact.artifact_id)))
            .select_from(EvidenceArtifact)
            .join(ToolCall, ToolCall.id == EvidenceArtifact.tool_call_id)
            .where(ToolCall.run_id == run.id),
        )
        lifecycle_event_count = await _count(
            session,
            select(func.count(LifecycleEvent.id)).where(
                LifecycleEvent.aggregate_type == "run", LifecycleEvent.aggregate_id == run.id
            ),
        )
        terminal_event_type = await session.scalar(
            select(LifecycleEvent.event_type)
            .where(LifecycleEvent.aggregate_type == "run", LifecycleEvent.aggregate_id == run.id)
            .order_by(LifecycleEvent.sequence_no.desc())
            .limit(1)
        )
        candidates = (
            await session.execute(
                select(
                    Candidate.id,
                    Candidate.sequence_sha256,
                    Candidate.parent_id,
                    Candidate.generation,
                    Candidate.status,
                )
                .where(Candidate.run_id == run.id)
                .order_by(Candidate.id)
            )
        ).all()
        occurrences = (
            await session.execute(
                select(
                    CandidateOccurrence.id,
                    CandidateOccurrence.candidate_id,
                    CandidateOccurrence.sequence_sha256,
                    CandidateOccurrence.occurrence_rank,
                    CandidateOccurrence.occurrence_kind,
                )
                .where(CandidateOccurrence.run_id == run.id)
                .order_by(CandidateOccurrence.id)
            )
        ).all()
        evaluations = (
            await session.execute(
                select(
                    Evaluation.id,
                    Evaluation.candidate_id,
                    Evaluation.metric_name,
                    Evaluation.tool_call_id,
                    Evaluation.numeric_value,
                    Evaluation.text_value,
                )
                .join(Candidate, Candidate.id == Evaluation.candidate_id)
                .where(Candidate.run_id == run.id)
                .order_by(Evaluation.id)
            )
        ).all()
        tool_calls = (
            await session.execute(
                select(
                    ToolCall.id,
                    ToolCall.status,
                    ToolCall.input_sha256,
                    ToolCall.output_sha256,
                )
                .where(ToolCall.run_id == run.id)
                .order_by(ToolCall.id)
            )
        ).all()
        decisions = (
            await session.execute(
                select(
                    AgentDecision.id,
                    AgentDecision.status,
                    AgentDecision.prompt_sha256,
                    AgentDecision.response_sha256,
                )
                .where(AgentDecision.run_id == run.id)
                .order_by(AgentDecision.id)
            )
        ).all()
        evidence = (
            await session.execute(
                select(
                    EvidenceArtifact.tool_call_id,
                    EvidenceArtifact.artifact_id,
                    EvidenceArtifact.role,
                )
                .join(ToolCall, ToolCall.id == EvidenceArtifact.tool_call_id)
                .where(ToolCall.run_id == run.id)
                .order_by(
                    EvidenceArtifact.tool_call_id,
                    EvidenceArtifact.artifact_id,
                    EvidenceArtifact.role,
                )
            )
        ).all()
        lifecycle = (
            await session.execute(
                select(
                    LifecycleEvent.sequence_no,
                    LifecycleEvent.event_type,
                    LifecycleEvent.payload_sha256,
                )
                .where(
                    LifecycleEvent.aggregate_type == "run",
                    LifecycleEvent.aggregate_id == run.id,
                )
                .order_by(LifecycleEvent.sequence_no)
            )
        ).all()
        graph_manifest = sha256_json(
            {
                "candidates": _canonical_rows(candidates),
                "occurrences": _canonical_rows(occurrences),
                "evaluations": _canonical_rows(evaluations),
                "tool_calls": _canonical_rows(tool_calls),
                "decisions": _canonical_rows(decisions),
                "evidence": _canonical_rows(evidence),
                "lifecycle": _canonical_rows(lifecycle),
            }
        )
        terminal.append(
            HistoricalRunSummary(
                run_id=run.id,
                target_id=run.target_id,
                target_name=target_name,
                status=run.status,
                spec_sha256=run.spec_sha256,
                formal_submission_key=run.formal_submission_key,
                parent_run_id=run.parent_run_id,
                created_at=_as_utc(run.created_at),
                started_at=_as_utc(run.started_at) if run.started_at else None,
                finished_at=_as_utc(run.finished_at),
                candidate_count=candidate_count,
                occurrence_count=occurrence_count,
                evaluation_count=evaluation_count,
                tool_call_count=tool_call_count,
                succeeded_tool_call_count=succeeded_tool_call_count,
                failed_tool_call_count=failed_tool_call_count,
                decision_count=decision_count,
                evidence_link_count=evidence_link_count,
                distinct_artifact_count=distinct_artifact_count,
                lifecycle_event_count=lifecycle_event_count,
                evidence_graph_manifest_sha256=graph_manifest,
                terminal_event_type=terminal_event_type or f"run.{run.status}",
                historical_role={
                    "succeeded": "decision_replay",
                    "failed": "failure_denominator",
                    "cancelled": "cancelled_denominator",
                }[run.status],
            )
        )
    return HistoricalEvidenceSnapshot(
        history_cutoff_at=cutoff_utc,
        terminal_runs=tuple(terminal),
        terminal_run_count=len(terminal),
        excluded_nonterminal_run_ids=tuple(excluded),
    )


class KnowledgeUseTrace(FrozenModel):
    provider_task_id: str = DEFAULT_KNOWLEDGE_PROVIDER_TASK_ID
    card_id: str
    query_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    passage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["adopt", "reject"]
    rationale: str = Field(min_length=1)


class MetricObservation(FrozenModel):
    metric_name: str
    status: Literal["succeeded", "failed"]
    numeric_value: float | None = None
    text_value: str | None = None
    out_of_domain: bool = False


class NumericGate(FrozenModel):
    metric_name: str
    direction: Literal["min", "max"]
    threshold: float
    purpose: Literal["safety", "validity"]
    threshold_source: Literal["provider_contract", "operational_guard"]
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LabelGate(FrozenModel):
    metric_name: str
    allowed_values: frozenset[str]
    purpose: Literal["safety", "validity"]
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class MetricAgreementGate(FrozenModel):
    metric_names: tuple[str, ...] = Field(min_length=2)
    maximum_spread: float = Field(ge=0)
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ParetoObjective(FrozenModel):
    metric_name: str
    direction: Literal["min", "max"]


class ExplorationPolicy(FrozenModel):
    maximum_fraction_of_structure_budget: float = Field(ge=0, le=0.2)
    hard_safety_bypass_forbidden: Literal[True] = True
    deterministic_selection_required: Literal[True] = True
    unused_exploration_slots_cannot_refill_from_rejected: Literal[True] = True


class RefinementPolicy(FrozenModel):
    maximum_rounds: int = Field(ge=1, le=5)
    minimum_mature_core_size: int = Field(ge=1)
    children_per_parent: int = Field(ge=1, le=8)
    zero_core_action: Literal["refine_without_lowering_safety"] = "refine_without_lowering_safety"
    parent_child_lineage_required: Literal[True] = True
    unchanged_parent_control_required: Literal[True] = True


class SequenceMaturityPolicy(FrozenModel):
    required_metrics: frozenset[str]
    numeric_gates: tuple[NumericGate, ...]
    label_gates: tuple[LabelGate, ...]
    pareto_objectives: tuple[ParetoObjective, ...] = Field(min_length=2)
    agreement_gates: tuple[MetricAgreementGate, ...] = ()
    minimum_rank_stability: float = Field(ge=0, le=1)
    structure_budget: int = Field(gt=0)
    exploration: ExplorationPolicy
    refinement: RefinementPolicy
    refined_candidate_requires_adopted_knowledge: Literal[True] = True
    threshold_derivation_from_current_batch_quantiles_forbidden: Literal[True] = True
    diversity_applied_after_quality_admission: Literal[True] = True
    no_forced_fill: Literal[True] = True

    @model_validator(mode="after")
    def validate_gate_and_objective_roles(self) -> SequenceMaturityPolicy:
        objective_names = [item.metric_name for item in self.pareto_objectives]
        if len(objective_names) != len(set(objective_names)):
            raise ValueError("Pareto objective names must be unique")
        if not set(objective_names).issubset(self.required_metrics):
            raise ValueError("Pareto objectives must be required metrics")
        gate_names = {
            *(item.metric_name for item in self.numeric_gates),
            *(item.metric_name for item in self.label_gates),
        }
        if not gate_names.issubset(self.required_metrics):
            raise ValueError("hard gates must be required metrics")
        if self.refinement.minimum_mature_core_size > self.structure_budget:
            raise ValueError("minimum mature core exceeds the structure budget")
        return self


class SequenceCandidateEvidence(FrozenModel):
    candidate_id: UUID
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_candidate_id: UUID | None = None
    generation: int = Field(ge=0)
    observations: tuple[MetricObservation, ...]
    rank_stability: float = Field(ge=0, le=1)
    knowledge_traces: tuple[KnowledgeUseTrace, ...] = ()
    proposal_context_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SequenceMaturityDecision(FrozenModel):
    candidate_id: UUID
    status: Literal["pareto_eligible", "promising_uncertain", "rejected"]
    structure_eligible: bool
    reasons: tuple[str, ...]


def assess_sequence_maturity(
    candidate: SequenceCandidateEvidence,
    policy: SequenceMaturityPolicy,
) -> SequenceMaturityDecision:
    observations: dict[str, MetricObservation] = {}
    duplicate_metrics: set[str] = set()
    for observation in candidate.observations:
        if observation.metric_name in observations:
            duplicate_metrics.add(observation.metric_name)
        observations[observation.metric_name] = observation
    hard_failures: list[str] = []
    conflicts: list[str] = []
    if duplicate_metrics:
        hard_failures.append(f"duplicate_metrics:{','.join(sorted(duplicate_metrics))}")
    missing = policy.required_metrics.difference(observations)
    if missing:
        hard_failures.append(f"missing_metrics:{','.join(sorted(missing))}")
    for name in sorted(policy.required_metrics.intersection(observations)):
        observation = observations[name]
        if observation.status != "succeeded":
            hard_failures.append(f"metric_failed:{name}")
        if observation.out_of_domain:
            hard_failures.append(f"out_of_domain:{name}")
    for gate in policy.numeric_gates:
        observation = observations.get(gate.metric_name)
        if observation is None or observation.numeric_value is None:
            hard_failures.append(f"numeric_value_missing:{gate.metric_name}")
            continue
        failed = (gate.direction == "min" and observation.numeric_value < gate.threshold) or (
            gate.direction == "max" and observation.numeric_value > gate.threshold
        )
        if failed:
            hard_failures.append(f"numeric_gate_failed:{gate.metric_name}")
    for gate in policy.label_gates:
        observation = observations.get(gate.metric_name)
        if observation is None or observation.text_value not in gate.allowed_values:
            hard_failures.append(f"label_gate_failed:{gate.metric_name}")
    for gate in policy.agreement_gates:
        values = [
            observations[name].numeric_value
            for name in gate.metric_names
            if name in observations and observations[name].numeric_value is not None
        ]
        if len(values) != len(gate.metric_names):
            hard_failures.append(f"agreement_input_missing:{','.join(gate.metric_names)}")
        elif max(values) - min(values) > gate.maximum_spread:
            conflicts.append(f"metric_disagreement:{','.join(gate.metric_names)}")
    if candidate.rank_stability < policy.minimum_rank_stability:
        conflicts.append("rank_instability")
    if candidate.parent_candidate_id is not None and not any(
        trace.decision == "adopt" for trace in candidate.knowledge_traces
    ):
        hard_failures.append("refinement_without_adopted_knowledge")
    if hard_failures:
        return SequenceMaturityDecision(
            candidate_id=candidate.candidate_id,
            status="rejected",
            structure_eligible=False,
            reasons=tuple(hard_failures + conflicts),
        )
    if conflicts:
        return SequenceMaturityDecision(
            candidate_id=candidate.candidate_id,
            status="promising_uncertain",
            structure_eligible=False,
            reasons=tuple(conflicts),
        )
    return SequenceMaturityDecision(
        candidate_id=candidate.candidate_id,
        status="pareto_eligible",
        structure_eligible=False,
        reasons=("hard_safety_and_validity_gates_passed",),
    )


class CohortCandidateDecision(FrozenModel):
    candidate_id: UUID
    status: Literal["mature_core", "promising_uncertain", "rejected"]
    structure_eligible: bool
    pareto_front: int | None = Field(default=None, ge=1)
    reasons: tuple[str, ...]


class SequenceCohortAdmission(FrozenModel):
    schema_version: Literal["v38.sequence-cohort-admission.2"] = "v38.sequence-cohort-admission.2"
    refinement_round: int = Field(ge=0)
    decisions: tuple[CohortCandidateDecision, ...]
    mature_core_candidate_ids: tuple[UUID, ...]
    exploration_candidate_ids: tuple[UUID, ...]
    rejected_candidate_ids: tuple[UUID, ...]
    refinement_required: bool
    structure_dispatch_allowed: bool
    unused_structure_slots: int = Field(ge=0)
    safety_thresholds_lowered: Literal[False] = False
    forced_fill_used: Literal[False] = False


def _objective_values(
    candidate: SequenceCandidateEvidence,
    policy: SequenceMaturityPolicy,
) -> tuple[float, ...]:
    observations = {item.metric_name: item for item in candidate.observations}
    values: list[float] = []
    for objective in policy.pareto_objectives:
        value = observations[objective.metric_name].numeric_value
        if value is None:
            raise ValueError(f"Pareto objective is not numeric: {objective.metric_name}")
        values.append(value if objective.direction == "min" else -value)
    return tuple(values)


def _dominates(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return all(a <= b for a, b in zip(left, right, strict=True)) and any(
        a < b for a, b in zip(left, right, strict=True)
    )


def _pareto_fronts(
    candidates: tuple[SequenceCandidateEvidence, ...],
    policy: SequenceMaturityPolicy,
) -> dict[UUID, int]:
    remaining = {item.candidate_id: _objective_values(item, policy) for item in candidates}
    fronts: dict[UUID, int] = {}
    front_number = 1
    while remaining:
        current = sorted(
            candidate_id
            for candidate_id, values in remaining.items()
            if not any(
                other_id != candidate_id and _dominates(other_values, values)
                for other_id, other_values in remaining.items()
            )
        )
        if not current:
            raise ValueError("unable to construct deterministic Pareto fronts")
        for candidate_id in current:
            fronts[candidate_id] = front_number
            del remaining[candidate_id]
        front_number += 1
    return fronts


def compute_leave_one_objective_out_rank_stability(
    candidates: tuple[SequenceCandidateEvidence, ...],
    policy: SequenceMaturityPolicy,
) -> dict[UUID, float]:
    """Measure front membership stability without inventing metric cutoffs."""

    if not candidates:
        raise ValueError("rank stability requires a non-empty cohort")
    if len({item.candidate_id for item in candidates}) != len(candidates):
        raise ValueError("rank stability cohort contains duplicate candidates")
    objective_count = len(policy.pareto_objectives)
    objective_values = {
        item.candidate_id: _objective_values(item, policy) for item in candidates
    }
    projections = [tuple(range(objective_count))]
    if objective_count > 2:
        projections.extend(
            tuple(index for index in range(objective_count) if index != omitted)
            for omitted in range(objective_count)
        )
    front_counts = {item.candidate_id: 0 for item in candidates}
    for axes in projections:
        projected = {
            candidate_id: tuple(values[index] for index in axes)
            for candidate_id, values in objective_values.items()
        }
        for candidate_id, values in projected.items():
            dominated = any(
                other_id != candidate_id and _dominates(other_values, values)
                for other_id, other_values in projected.items()
            )
            if not dominated:
                front_counts[candidate_id] += 1
    return {
        candidate_id: count / len(projections)
        for candidate_id, count in front_counts.items()
    }


def admit_sequence_cohort(
    candidates: tuple[SequenceCandidateEvidence, ...],
    policy: SequenceMaturityPolicy,
    *,
    refinement_round: int,
) -> SequenceCohortAdmission:
    """Admit a cohort without inventing activity cutoffs or bypassing safety gates."""

    if not candidates:
        raise ValueError("sequence cohort is empty")
    if len({item.candidate_id for item in candidates}) != len(candidates):
        raise ValueError("duplicate candidate identity in sequence cohort")
    preliminary = {item.candidate_id: assess_sequence_maturity(item, policy) for item in candidates}
    stable = tuple(
        item for item in candidates if preliminary[item.candidate_id].status == "pareto_eligible"
    )
    uncertain = tuple(
        item
        for item in candidates
        if preliminary[item.candidate_id].status == "promising_uncertain"
    )
    rejected = tuple(
        item for item in candidates if preliminary[item.candidate_id].status == "rejected"
    )
    fronts = _pareto_fronts(stable, policy) if stable else {}
    exploration_budget = floor(
        policy.structure_budget * policy.exploration.maximum_fraction_of_structure_budget
    )
    core_budget = policy.structure_budget - exploration_budget
    pareto_front = tuple(item for item in stable if fronts[item.candidate_id] == 1)
    nonfront_stable = tuple(item for item in stable if fronts[item.candidate_id] != 1)
    stable_ordered = sorted(
        pareto_front,
        key=lambda item: (
            fronts[item.candidate_id],
            -item.rank_stability,
            _objective_values(item, policy),
            str(item.candidate_id),
        ),
    )
    core = tuple(stable_ordered[:core_budget])
    core_ids = {item.candidate_id for item in core}
    exploration_pool = sorted(
        (*uncertain, *stable_ordered[core_budget:], *nonfront_stable),
        key=lambda item: (
            0 if item in uncertain else 1,
            -item.rank_stability,
            str(item.candidate_id),
        ),
    )
    exploration = tuple(exploration_pool[:exploration_budget])
    exploration_ids = {item.candidate_id for item in exploration}
    enough_core = len(core) >= policy.refinement.minimum_mature_core_size
    rounds_remain = refinement_round < policy.refinement.maximum_rounds
    refinement_required = not enough_core and rounds_remain
    structure_dispatch_allowed = enough_core or (
        bool(core) and refinement_round >= policy.refinement.maximum_rounds
    )
    decisions: list[CohortCandidateDecision] = []
    for item in sorted(candidates, key=lambda candidate: str(candidate.candidate_id)):
        base = preliminary[item.candidate_id]
        if item.candidate_id in core_ids:
            decisions.append(
                CohortCandidateDecision(
                    candidate_id=item.candidate_id,
                    status="mature_core",
                    structure_eligible=structure_dispatch_allowed,
                    pareto_front=fronts[item.candidate_id],
                    reasons=("selected_by_deterministic_nonweighted_pareto_front",),
                )
            )
        elif item.candidate_id in exploration_ids:
            decisions.append(
                CohortCandidateDecision(
                    candidate_id=item.candidate_id,
                    status="promising_uncertain",
                    structure_eligible=structure_dispatch_allowed,
                    pareto_front=fronts.get(item.candidate_id),
                    reasons=base.reasons + ("selected_within_fixed_exploration_budget",),
                )
            )
        elif base.status == "rejected":
            decisions.append(
                CohortCandidateDecision(
                    candidate_id=item.candidate_id,
                    status="rejected",
                    structure_eligible=False,
                    reasons=base.reasons,
                )
            )
        else:
            decisions.append(
                CohortCandidateDecision(
                    candidate_id=item.candidate_id,
                    status="promising_uncertain",
                    structure_eligible=False,
                    pareto_front=fronts.get(item.candidate_id),
                    reasons=base.reasons + ("outside_frozen_structure_budget",),
                )
            )
    used_slots = len(core) + len(exploration) if structure_dispatch_allowed else 0
    return SequenceCohortAdmission(
        refinement_round=refinement_round,
        decisions=tuple(decisions),
        mature_core_candidate_ids=tuple(item.candidate_id for item in core),
        exploration_candidate_ids=tuple(item.candidate_id for item in exploration),
        rejected_candidate_ids=tuple(item.candidate_id for item in rejected),
        refinement_required=refinement_required,
        structure_dispatch_allowed=structure_dispatch_allowed,
        unused_structure_slots=policy.structure_budget - used_slots,
    )


class SequenceRefinementTask(FrozenModel):
    schema_version: Literal["v38.sequence-refinement-task.1"] = (
        "v38.sequence-refinement-task.1"
    )
    parent_candidate_id: UUID
    parent_sequence: str = Field(min_length=10, max_length=25)
    parent_sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    refinement_round: int = Field(ge=1, le=5)
    requested_children: int = Field(ge=1, le=8)
    provider_task_id: str = DEFAULT_KNOWLEDGE_PROVIDER_TASK_ID
    knowledge_context_pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    objective_metric_names: tuple[str, ...] = Field(min_length=2)
    parent_control_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    knowledge_trace_required: Literal[True] = True
    unchanged_parent_control_required: Literal[True] = True
    child_must_repeat_full_sequence_panel: Literal[True] = True
    safety_gate_relaxation_forbidden: Literal[True] = True

    @model_validator(mode="after")
    def validate_parent_identity(self) -> SequenceRefinementTask:
        sequence = "".join(self.parent_sequence.split()).upper()
        if sha256_text(sequence) != self.parent_sequence_sha256:
            raise ValueError("refinement parent sequence identity drifted")
        expected_control = sha256_json(
            {
                "parent_candidate_id": str(self.parent_candidate_id),
                "parent_sequence": sequence,
                "control": "unchanged_parent",
                "refinement_round": self.refinement_round,
            }
        )
        if self.parent_control_sha256 != expected_control:
            raise ValueError("refinement parent control identity drifted")
        return self


class SequenceRefinementPlan(FrozenModel):
    schema_version: Literal["v38.sequence-refinement-plan.1"] = (
        "v38.sequence-refinement-plan.1"
    )
    refinement_round: int = Field(ge=1, le=5)
    admission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tasks: tuple[SequenceRefinementTask, ...]
    parent_controls_retained: Literal[True] = True
    full_rescoring_required: Literal[True] = True
    structure_dispatch_forbidden_until_readmission: Literal[True] = True
    safety_thresholds_lowered: Literal[False] = False
    forced_fill_used: Literal[False] = False

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


def build_sequence_refinement_plan(
    *,
    admission: SequenceCohortAdmission,
    candidates: tuple[SequenceCandidateEvidence, ...],
    parent_sequences: dict[UUID, str],
    policy: SequenceMaturityPolicy,
    knowledge_context_pack_sha256: str,
) -> SequenceRefinementPlan:
    """Create bounded knowledge work without lowering gates or dispatching structure."""

    if not admission.refinement_required or admission.structure_dispatch_allowed:
        raise ValueError("refinement plan requires a blocked refinement admission")
    next_round = admission.refinement_round + 1
    if next_round > policy.refinement.maximum_rounds:
        raise ValueError("refinement rounds are exhausted")
    decisions = {item.candidate_id: item for item in admission.decisions}
    by_id = {item.candidate_id: item for item in candidates}
    if set(decisions) != set(by_id):
        raise ValueError("admission and refinement cohort identities differ")
    invalid_prefixes = (
        "duplicate_metrics:",
        "missing_metrics:",
        "metric_failed:",
        "out_of_domain:",
        "refinement_without_adopted_knowledge",
    )
    eligible = [
        item
        for item in candidates
        if not any(
            reason.startswith(invalid_prefixes)
            for reason in decisions[item.candidate_id].reasons
        )
    ]
    eligible.sort(
        key=lambda item: (
            0 if decisions[item.candidate_id].status == "promising_uncertain" else 1,
            -item.rank_stability,
            str(item.candidate_id),
        )
    )
    parent_limit = max(
        1,
        (
            policy.refinement.minimum_mature_core_size
            + policy.refinement.children_per_parent
            - 1
        )
        // policy.refinement.children_per_parent,
    )
    objective_names = tuple(item.metric_name for item in policy.pareto_objectives)
    tasks: list[SequenceRefinementTask] = []
    for candidate in eligible[:parent_limit]:
        sequence = "".join(parent_sequences.get(candidate.candidate_id, "").split()).upper()
        if not 10 <= len(sequence) <= 25:
            raise ValueError(f"missing valid parent sequence: {candidate.candidate_id}")
        tasks.append(
            SequenceRefinementTask(
                parent_candidate_id=candidate.candidate_id,
                parent_sequence=sequence,
                parent_sequence_sha256=candidate.sequence_sha256,
                refinement_round=next_round,
                requested_children=policy.refinement.children_per_parent,
                knowledge_context_pack_sha256=knowledge_context_pack_sha256,
                objective_metric_names=objective_names,
                parent_control_sha256=sha256_json(
                    {
                        "parent_candidate_id": str(candidate.candidate_id),
                        "parent_sequence": sequence,
                        "control": "unchanged_parent",
                        "refinement_round": next_round,
                    }
                ),
            )
        )
    if not tasks:
        raise ValueError("no evidence-complete parent is eligible for refinement")
    return SequenceRefinementPlan(
        refinement_round=next_round,
        admission_sha256=sha256_json(admission.model_dump(mode="json")),
        tasks=tuple(tasks),
    )


def build_default_v38_maturity_policy() -> SequenceMaturityPolicy:
    """Freeze validity/safety gates while keeping activity threshold-free."""

    required = frozenset(
        {
            "hydrophobic_moment_eisenberg",
            "hydrophobic_ratio_modlamp",
            "maximum_hydrophobic_run",
            "net_charge_ph7_4",
            "macrel_amp_probability",
            "macrel_hemolysis_probability",
            "macrel_hemolysis_label",
            "llamp_log10_mic_um",
            "amp_read_log10_mic_um",
            "toxinpred3_hybrid_score",
            "toxinpred3_label",
        }
    )
    return SequenceMaturityPolicy(
        required_metrics=required,
        numeric_gates=(),
        label_gates=(
            LabelGate(
                metric_name="macrel_hemolysis_label",
                allowed_values=frozenset({"low"}),
                purpose="safety",
                evidence_sha256=sha256_json(
                    {"provider": "macrel", "contract_label": "low"}
                ),
            ),
            LabelGate(
                metric_name="toxinpred3_label",
                allowed_values=frozenset({"Non-Toxin"}),
                purpose="safety",
                evidence_sha256=sha256_json(
                    {"provider": "toxinpred3", "contract_label": "Non-Toxin"}
                ),
            ),
        ),
        pareto_objectives=(
            ParetoObjective(metric_name="llamp_log10_mic_um", direction="min"),
            ParetoObjective(metric_name="amp_read_log10_mic_um", direction="min"),
            ParetoObjective(metric_name="macrel_amp_probability", direction="max"),
            ParetoObjective(metric_name="macrel_hemolysis_probability", direction="min"),
            ParetoObjective(metric_name="toxinpred3_hybrid_score", direction="min"),
            ParetoObjective(metric_name="hydrophobic_moment_eisenberg", direction="max"),
            ParetoObjective(metric_name="maximum_hydrophobic_run", direction="min"),
        ),
        agreement_gates=(),
        minimum_rank_stability=0.8,
        structure_budget=48,
        exploration=ExplorationPolicy(maximum_fraction_of_structure_budget=0.2),
        refinement=RefinementPolicy(
            maximum_rounds=3,
            minimum_mature_core_size=12,
            children_per_parent=3,
        ),
    )


class TargetBranchSpec(FrozenModel):
    target_key: str
    target_id: UUID
    target_sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    native_pocket_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    wrong_pocket_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    qualification_witness_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_grade: Literal["A", "B"]
    panel_role: Literal["reference_anchor", "qualified_target"]
    structure_budget: int = Field(gt=0)
    boltz_seeds_per_candidate: int = Field(gt=0)
    rosetta_decoys_per_pose: int = Field(gt=0)
    target_agnostic_amp_lane_retained: Literal[True] = True


class TargetQualificationWitness(FrozenModel):
    schema_version: Literal["v38.target-qualification.1"] = (
        "v38.target-qualification.1"
    )
    target_key: str
    target_id: UUID
    target_sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_source_accession: str
    coordinate_source_uri: str
    coordinate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_size_bytes: int = Field(gt=0)
    coordinate_model_count: int = Field(gt=0)
    coordinate_atom_count: int = Field(gt=0)
    primary_pocket_id: UUID
    primary_pocket_definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_pocket_grade: Literal["A", "B"]
    primary_evidence_sha256: tuple[str, ...] = Field(min_length=1)
    wrong_pocket_id: UUID
    wrong_pocket_definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    wrong_pocket_grade: Literal["A", "B"]
    wrong_evidence_sha256: tuple[str, ...] = Field(min_length=1)
    selected_before_peptide_outcomes: Literal[True] = True
    peptide_or_structure_outcomes_used_for_selection: Literal[False] = False
    target_agnostic_amp_lane_retained: Literal[True] = True

    @model_validator(mode="after")
    def validate_control(self) -> TargetQualificationWitness:
        if self.primary_pocket_id == self.wrong_pocket_id:
            raise ValueError("target qualification requires a distinct wrong pocket")
        hashes = (*self.primary_evidence_sha256, *self.wrong_evidence_sha256)
        if any(
            len(item) != 64 or any(char not in "0123456789abcdef" for char in item)
            for item in hashes
        ):
            raise ValueError("target qualification evidence SHA is invalid")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class MultiTargetExecutionPlan(FrozenModel):
    schema_version: Literal["v38.multitarget-plan.1"] = "v38.multitarget-plan.1"
    harness_release_id: str
    history_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    shared_sequence_cohort_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sequence_maturity_decision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_branches: tuple[TargetBranchSpec, ...] = Field(min_length=2, max_length=6)
    max_parallel_targets: int = Field(ge=2, le=6)
    execution_mode: Literal["parallel_isolated_target_branches"] = (
        "parallel_isolated_target_branches"
    )
    shared_sequence_stage_once: Literal[True] = True
    same_sequence_inputs_across_targets: Literal[True] = True
    per_target_evidence_namespace_required: Literal[True] = True
    target_outcome_cannot_mutate_other_branch: Literal[True] = True
    target_selection_precedes_peptide_outcomes: Literal[True] = True
    no_forced_target_refill: Literal[True] = True

    @model_validator(mode="after")
    def validate_parallel_branches(self) -> MultiTargetExecutionPlan:
        keys = [branch.target_key for branch in self.target_branches]
        ids = [branch.target_id for branch in self.target_branches]
        if len(keys) != len(set(keys)) or len(ids) != len(set(ids)):
            raise ValueError("target branches must be unique")
        if not any(branch.panel_role == "qualified_target" for branch in self.target_branches):
            raise ValueError("a reference anchor cannot be the only target role")
        if self.max_parallel_targets > len(self.target_branches):
            raise ValueError("parallelism exceeds the target panel")
        for branch in self.target_branches:
            if branch.native_pocket_sha256 == branch.wrong_pocket_sha256:
                raise ValueError("native and wrong-pocket controls must be distinct")
        budgets = {
            (
                branch.structure_budget,
                branch.boltz_seeds_per_candidate,
                branch.rosetta_decoys_per_pose,
            )
            for branch in self.target_branches
        }
        if len(budgets) != 1:
            raise ValueError("all target branches require an equal preregistered science budget")
        return self


class TargetDispatch(FrozenModel):
    target_key: str
    target_id: UUID
    parallel_wave: int
    evidence_namespace: str
    candidate_ids: tuple[UUID, ...]


class MultiTargetStructureTask(FrozenModel):
    target_key: str
    target_id: UUID
    candidate_id: UUID
    parallel_wave: int = Field(ge=0)
    control_lane: Literal["native", "wrong_pocket"]
    pocket_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    boltz_seed: int = Field(ge=0)
    rosetta_decoys_per_pose: int = Field(gt=0)
    evidence_namespace: str
    ordinal: int = Field(ge=0)

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class MultiTargetBoltzEvidence(FrozenModel):
    schema_version: Literal["v38.multitarget-boltz-evidence.1"] = (
        "v38.multitarget-boltz-evidence.1"
    )
    task: MultiTargetStructureTask
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_call_id: UUID
    coordinate_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    raw_result_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameters_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["succeeded"] = "succeeded"

    @model_validator(mode="after")
    def validate_task_binding(self) -> MultiTargetBoltzEvidence:
        if self.task_sha256 != self.task.sha256():
            raise ValueError("Boltz evidence is not bound to its exact v38 structure task")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class RosettaDecoyEvidence(FrozenModel):
    decoy_ordinal: int = Field(ge=0)
    input_structure_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_structure_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    score_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_score: float


class MultiTargetRosettaEvidence(FrozenModel):
    schema_version: Literal["v38.multitarget-rosetta-evidence.2"] = (
        "v38.multitarget-rosetta-evidence.2"
    )
    task: MultiTargetStructureTask
    task_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    boltz_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    boltz_coordinate_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    converted_input_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prepared_input_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prepacked_input_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_call_id: UUID
    raw_result_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decoys: tuple[RosettaDecoyEvidence, ...] = Field(min_length=1)
    status: Literal["succeeded"] = "succeeded"

    @model_validator(mode="after")
    def validate_pose_and_decoys(self) -> MultiTargetRosettaEvidence:
        if self.task_sha256 != self.task.sha256():
            raise ValueError("Rosetta evidence is not bound to its exact v38 structure task")
        if len(self.decoys) != self.task.rosetta_decoys_per_pose:
            raise ValueError("Rosetta decoy count differs from the frozen pose budget")
        if [item.decoy_ordinal for item in self.decoys] != list(range(len(self.decoys))):
            raise ValueError("Rosetta decoy ordinals must be contiguous")
        if any(
            item.input_structure_sha256 != self.prepacked_input_artifact_sha256
            for item in self.decoys
        ):
            raise ValueError("Rosetta decoy input is not the bound prepacked coordinate")
        output_hashes = [item.output_structure_sha256 for item in self.decoys]
        score_hashes = [item.score_record_sha256 for item in self.decoys]
        if len(output_hashes) != len(set(output_hashes)):
            raise ValueError("Rosetta output structure hashes must be unique per decoy")
        if len(score_hashes) != len(set(score_hashes)):
            raise ValueError("Rosetta score record hashes must be unique per decoy")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


def build_parallel_target_dispatch(
    plan: MultiTargetExecutionPlan,
    *,
    mature_candidate_ids: tuple[UUID, ...],
) -> tuple[TargetDispatch, ...]:
    if not mature_candidate_ids:
        raise ValueError("no mature sequence candidates were admitted")
    if len(mature_candidate_ids) != len(set(mature_candidate_ids)):
        raise ValueError("duplicate candidate in shared sequence cohort")
    return tuple(
        TargetDispatch(
            target_key=branch.target_key,
            target_id=branch.target_id,
            parallel_wave=index // plan.max_parallel_targets,
            evidence_namespace=f"target/{branch.target_key}/{branch.target_id}",
            candidate_ids=mature_candidate_ids,
        )
        for index, branch in enumerate(plan.target_branches)
    )


def build_multitarget_structure_tasks(
    plan: MultiTargetExecutionPlan,
    *,
    dispatches: tuple[TargetDispatch, ...],
    boltz_seeds: tuple[int, ...],
) -> tuple[MultiTargetStructureTask, ...]:
    if len(boltz_seeds) != len(set(boltz_seeds)):
        raise ValueError("Boltz seeds must be unique")
    branches = {branch.target_key: branch for branch in plan.target_branches}
    if {dispatch.target_key for dispatch in dispatches} != set(branches):
        raise ValueError("dispatches must cover every preregistered target exactly once")
    if len(dispatches) != len(branches):
        raise ValueError("duplicate target dispatch")

    tasks: list[MultiTargetStructureTask] = []
    ordinal = 0
    for dispatch in dispatches:
        branch = branches[dispatch.target_key]
        if dispatch.target_id != branch.target_id:
            raise ValueError("dispatch target identity does not match the frozen branch")
        if len(dispatch.candidate_ids) > branch.structure_budget:
            raise ValueError("admitted sequence cohort exceeds the target structure budget")
        if len(boltz_seeds) != branch.boltz_seeds_per_candidate:
            raise ValueError("Boltz seed count differs from the frozen target budget")
        expected_namespace = f"target/{branch.target_key}/{branch.target_id}"
        if dispatch.evidence_namespace != expected_namespace:
            raise ValueError("dispatch evidence namespace is not target-isolated")
        for candidate_id in dispatch.candidate_ids:
            for control_lane, pocket_sha256 in (
                ("native", branch.native_pocket_sha256),
                ("wrong_pocket", branch.wrong_pocket_sha256),
            ):
                for seed in boltz_seeds:
                    tasks.append(
                        MultiTargetStructureTask(
                            target_key=branch.target_key,
                            target_id=branch.target_id,
                            candidate_id=candidate_id,
                            parallel_wave=dispatch.parallel_wave,
                            control_lane=control_lane,
                            pocket_sha256=pocket_sha256,
                            boltz_seed=seed,
                            rosetta_decoys_per_pose=branch.rosetta_decoys_per_pose,
                            evidence_namespace=(
                                f"{dispatch.evidence_namespace}/{control_lane}"
                            ),
                            ordinal=ordinal,
                        )
                    )
                    ordinal += 1
    return tuple(tasks)
