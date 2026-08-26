from __future__ import annotations

import math
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from pepagent.provenance.hashing import sha256_json, sha256_text
from pepagent.v38_science_execution import (
    V38_METRIC_OBSERVATIONS,
    GeneratorCell,
    V38SequenceExecutionContract,
)

SEQUENCE_METRICS = frozenset(
    {
        "hydrophobic_moment_eisenberg",
        "hydrophobic_ratio_modlamp",
        "maximum_hydrophobic_run",
        "net_charge_ph7_4",
        "guruprasad_instability_index",
        "macrel_amp_probability",
        "macrel_hemolysis_probability",
        "macrel_hemolysis_label",
        "llamp_log10_mic_um",
        "amp_read_log10_mic_um",
        "toxinpred3_hybrid_score",
        "toxinpred3_label",
    }
)
GeneratorAllocationPolicy = Literal[
    "balanced_then_yield_v1", "safety_biased_hydramp_v1"
]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DesignBranch(FrozenModel):
    branch_key: str = Field(min_length=1)
    branch_kind: Literal["target_specific", "target_agnostic"]
    target_key: str | None = None
    target_sequence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    requested_delivery_count: int = Field(gt=0)
    initial_raw_budget: int = Field(gt=0)
    target_sequence_interaction_required: bool
    structure_scoring: Literal["optional", "not_applicable"]

    @model_validator(mode="after")
    def validate_branch_semantics(self) -> DesignBranch:
        if self.initial_raw_budget <= self.requested_delivery_count:
            raise ValueError("initial raw budget must exceed the delivery quota")
        if self.branch_kind == "target_specific":
            if not self.target_key or not self.target_sequence_sha256:
                raise ValueError("target-specific branch requires a frozen target sequence")
            if not self.target_sequence_interaction_required:
                raise ValueError("target-specific branch requires sequence interaction scoring")
            if self.structure_scoring != "optional":
                raise ValueError("target-specific structure scoring must remain optional")
        else:
            if self.target_key is not None or self.target_sequence_sha256 is not None:
                raise ValueError("target-agnostic branch cannot bind a target sequence")
            if self.target_sequence_interaction_required:
                raise ValueError("target-agnostic branch cannot require target interaction scoring")
            if self.structure_scoring != "not_applicable":
                raise ValueError("target-agnostic structure scoring is not applicable")
        return self


class SevenBranchDesignContract(FrozenModel):
    schema_version: Literal["ampgent.seven_branch_design.v1"] = (
        "ampgent.seven_branch_design.v1"
    )
    target_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    historical_winner_stability_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    historical_family_summary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    branches: tuple[DesignBranch, ...]
    required_sequence_metrics: frozenset[str]
    persist_every_raw_occurrence: Literal[True] = True
    score_all_valid_unique_sequences: Literal[True] = True
    branch_local_target_ranking: Literal[True] = True
    shared_cross_target_binding_required: Literal[False] = False
    historical_work_output_reuse_allowed: Literal[False] = False
    historical_pool_guides_novelty_and_seed_policy: Literal[True] = True
    dynamic_top_up_until_every_quota_is_filled: Literal[True] = True

    @field_serializer("required_sequence_metrics")
    def serialize_required_sequence_metrics(self, value: frozenset[str]) -> list[str]:
        return sorted(value)

    @model_validator(mode="after")
    def validate_topology(self) -> SevenBranchDesignContract:
        if len(self.branches) != 7:
            raise ValueError("design contract requires six targets and one target-agnostic branch")
        keys = [branch.branch_key for branch in self.branches]
        if len(keys) != len(set(keys)):
            raise ValueError("branch keys must be unique")
        targets = [branch for branch in self.branches if branch.branch_kind == "target_specific"]
        agnostic = [branch for branch in self.branches if branch.branch_kind == "target_agnostic"]
        if len(targets) != 6 or len(agnostic) != 1:
            raise ValueError(
                "design contract requires exactly six target branches and one agnostic"
            )
        if {branch.requested_delivery_count for branch in targets} != {150}:
            raise ValueError("every target branch must request 150 candidates")
        if agnostic[0].requested_delivery_count != 1000:
            raise ValueError("target-agnostic branch must request 1000 candidates")
        if sum(branch.requested_delivery_count for branch in self.branches) != 1900:
            raise ValueError("total delivery quota must be 1900")
        if self.required_sequence_metrics != SEQUENCE_METRICS:
            raise ValueError("seven-branch contract must retain the twelve sequence metrics")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class SevenBranchRoundBinding(FrozenModel):
    """Bind one immutable score-all child run to one branch-local generation round."""

    schema_version: Literal["ampgent.seven_branch_round.v1"] = (
        "ampgent.seven_branch_round.v1"
    )
    design_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch_key: str = Field(min_length=1)
    branch_kind: Literal["target_specific", "target_agnostic"]
    target_key: str | None = None
    target_sequence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    round_ordinal: int = Field(ge=0)
    expected_raw_occurrences: int = Field(gt=0, multiple_of=100)
    execution_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    independent_frozen_run_identity_required: Literal[True] = True
    defer_structure_to_optional_branch_enhancement: Literal[True] = True

    @model_validator(mode="after")
    def validate_branch_binding(self) -> SevenBranchRoundBinding:
        if self.branch_kind == "target_specific":
            if not self.target_key or not self.target_sequence_sha256:
                raise ValueError("target-specific round requires a frozen target sequence")
        elif self.target_key is not None or self.target_sequence_sha256 is not None:
            raise ValueError("target-agnostic round cannot bind a target sequence")
        return self


class SevenBranchRoundRequest(FrozenModel):
    """A pre-reserved child run; identities are never allocated during Temporal replay."""

    run_id: UUID
    workflow_id: str = Field(min_length=1)
    request: dict[str, Any]

    @model_validator(mode="after")
    def validate_request_binding(self) -> SevenBranchRoundRequest:
        if str(self.request.get("run_id")) != str(self.run_id):
            raise ValueError("seven-branch child request run identity drifted")
        binding = SevenBranchRoundBinding.model_validate(
            self.request.get("seven_branch_round")
        )
        if sha256_json(self.request.get("execution_contract")) != (
            binding.execution_contract_sha256
        ):
            raise ValueError("seven-branch child execution contract identity drifted")
        return self


class TargetSequenceRuntime(FrozenModel):
    target_key: str = Field(min_length=1)
    accession: str = Field(min_length=1)
    sequence: str = Field(min_length=1)
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_sequence_identity(self) -> TargetSequenceRuntime:
        normalized = "".join(self.sequence.split()).upper()
        if normalized != self.sequence:
            raise ValueError("target runtime sequence must already be normalized")
        if sha256_text(normalized) != self.sequence_sha256:
            raise ValueError("target runtime sequence identity drifted")
        return self


class SevenBranchDesignSchedule(FrozenModel):
    """One durable controller epoch over pre-frozen branch-local child runs."""

    schema_version: Literal["ampgent.seven_branch_schedule.v1"] = (
        "ampgent.seven_branch_schedule.v1"
    )
    controller_run_id: UUID
    design_contract: SevenBranchDesignContract
    target_runtime_by_key: dict[str, TargetSequenceRuntime]
    rounds: tuple[SevenBranchRoundRequest, ...]

    @model_validator(mode="after")
    def validate_schedule(self) -> SevenBranchDesignSchedule:
        if len(self.rounds) != 7:
            raise ValueError("seven-branch v1 schedule requires one initial round per branch")
        run_ids = [item.run_id for item in self.rounds]
        workflow_ids = [item.workflow_id for item in self.rounds]
        if len(run_ids) != len(set(run_ids)) or len(workflow_ids) != len(
            set(workflow_ids)
        ):
            raise ValueError("seven-branch child run and workflow identities must be unique")
        expected_contract_sha = self.design_contract.sha256()
        bindings = [
            SevenBranchRoundBinding.model_validate(item.request["seven_branch_round"])
            for item in self.rounds
        ]
        if any(item.design_contract_sha256 != expected_contract_sha for item in bindings):
            raise ValueError("seven-branch round is bound to another design contract")
        branch_by_key = {item.branch_key: item for item in self.design_contract.branches}
        target_branches = {
            item.target_key: item
            for item in self.design_contract.branches
            if item.branch_kind == "target_specific"
        }
        if set(self.target_runtime_by_key) != set(target_branches):
            raise ValueError("target runtimes do not cover the six target branches")
        for target_key, runtime in self.target_runtime_by_key.items():
            branch = target_branches[target_key]
            if runtime.target_key != target_key:
                raise ValueError("target runtime key drifted")
            if runtime.sequence_sha256 != branch.target_sequence_sha256:
                raise ValueError("target runtime sequence differs from design branch")
        seen_ordinals: dict[str, list[int]] = {}
        for frozen_round, binding in zip(self.rounds, bindings, strict=True):
            branch = branch_by_key.get(binding.branch_key)
            if branch is None:
                raise ValueError("seven-branch round references an unknown branch")
            if binding.branch_kind != branch.branch_kind:
                raise ValueError("seven-branch round kind drifted")
            if binding.target_key != branch.target_key:
                raise ValueError("seven-branch round target drifted")
            if binding.target_sequence_sha256 != branch.target_sequence_sha256:
                raise ValueError("seven-branch round target sequence drifted")
            queues = frozen_round.request.get("task_queues")
            if not isinstance(queues, dict):
                raise ValueError("seven-branch child task queues are missing")
            if binding.branch_kind == "target_specific" and not isinstance(
                queues.get("target_sequence"), str
            ):
                raise ValueError("target-specific child lacks target-sequence queue")
            seen_ordinals.setdefault(binding.branch_key, []).append(binding.round_ordinal)
        for ordinals in seen_ordinals.values():
            if ordinals != [0]:
                raise ValueError("seven-branch v1 initial round ordinal must be zero")
        if set(seen_ordinals) != set(branch_by_key):
            raise ValueError("seven-branch v1 schedule must cover all seven branches")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class SevenBranchTopUpEpochBranch(FrozenModel):
    branch_key: str
    prior_source_run_ids: tuple[UUID, ...]
    prior_evidence_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    top_up_plan: BranchTopUpPlan | BranchQualityTopUpPlan
    frozen_round: SevenBranchRoundRequest

    @model_validator(mode="after")
    def validate_epoch_branch(self) -> SevenBranchTopUpEpochBranch:
        if not self.prior_source_run_ids or len(self.prior_source_run_ids) != len(
            set(self.prior_source_run_ids)
        ):
            raise ValueError("top-up epoch requires unique prior source runs")
        binding = SevenBranchRoundBinding.model_validate(
            self.frozen_round.request["seven_branch_round"]
        )
        if binding.branch_key != self.branch_key:
            raise ValueError("top-up epoch branch key drifted")
        if self.top_up_plan.branch_key != self.branch_key:
            raise ValueError("top-up plan belongs to another branch")
        if self.top_up_plan.action not in {
            "freeze_successor_round",
            "freeze_quality_successor_round",
        }:
            raise ValueError("top-up epoch cannot freeze a completed branch")
        if binding.round_ordinal != self.top_up_plan.next_round_ordinal:
            raise ValueError("top-up epoch round ordinal drifted")
        if (
            binding.expected_raw_occurrences
            != self.top_up_plan.recommended_raw_budget
        ):
            raise ValueError("top-up epoch raw budget drifted")
        return self


class SevenBranchTopUpSchedule(FrozenModel):
    """One independently frozen successor epoch for incomplete branches."""

    schema_version: Literal[
        "ampgent.seven_branch_top_up_schedule.v1",
        "ampgent.seven_branch_top_up_schedule.v2",
    ] = (
        "ampgent.seven_branch_top_up_schedule.v1"
    )
    controller_run_id: UUID
    parent_controller_run_id: UUID
    epoch_ordinal: int = Field(ge=1)
    design_contract: SevenBranchDesignContract
    target_runtime_by_key: dict[str, TargetSequenceRuntime]
    branches: tuple[SevenBranchTopUpEpochBranch, ...]

    @model_validator(mode="after")
    def validate_schedule(self) -> SevenBranchTopUpSchedule:
        if not self.branches:
            raise ValueError("top-up schedule requires at least one incomplete branch")
        keys = [item.branch_key for item in self.branches]
        if len(keys) != len(set(keys)):
            raise ValueError("top-up schedule branches must be unique within an epoch")
        branch_by_key = {
            item.branch_key: item for item in self.design_contract.branches
        }
        if any(key not in branch_by_key for key in keys):
            raise ValueError("top-up schedule references an unknown branch")
        run_ids = [item.frozen_round.run_id for item in self.branches]
        workflow_ids = [item.frozen_round.workflow_id for item in self.branches]
        if len(run_ids) != len(set(run_ids)) or len(workflow_ids) != len(
            set(workflow_ids)
        ):
            raise ValueError("top-up child identities must be unique")
        target_branches = {
            item.target_key: item
            for item in self.design_contract.branches
            if item.branch_kind == "target_specific"
        }
        if set(self.target_runtime_by_key) != set(target_branches):
            raise ValueError("top-up target runtimes do not cover the six targets")
        for item in self.branches:
            branch = branch_by_key[item.branch_key]
            binding = SevenBranchRoundBinding.model_validate(
                item.frozen_round.request["seven_branch_round"]
            )
            if binding.design_contract_sha256 != self.design_contract.sha256():
                raise ValueError("top-up round is bound to another design contract")
            if binding.branch_kind != branch.branch_kind:
                raise ValueError("top-up round branch kind drifted")
            if binding.target_key != branch.target_key:
                raise ValueError("top-up round target drifted")
            if binding.target_sequence_sha256 != branch.target_sequence_sha256:
                raise ValueError("top-up round target sequence drifted")
        quality_plans = [
            item
            for item in self.branches
            if isinstance(item.top_up_plan, BranchQualityTopUpPlan)
        ]
        if self.schema_version == "ampgent.seven_branch_top_up_schedule.v1" and (
            quality_plans
        ):
            raise ValueError("quality top-up plans require schedule schema v2")
        if self.schema_version == "ampgent.seven_branch_top_up_schedule.v2" and (
            len(quality_plans) != len(self.branches)
        ):
            raise ValueError("quality schedule v2 requires quality plans for every branch")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


def build_seven_branch_round_execution_contract(
    contract: SevenBranchDesignContract,
    *,
    branch_key: str,
    round_ordinal: int,
    raw_budget: int | None = None,
    generator_allocation_policy: GeneratorAllocationPolicy = "balanced_then_yield_v1",
) -> tuple[SevenBranchRoundBinding, V38SequenceExecutionContract]:
    """Project one branch budget into frozen 100-occurrence generator cells."""

    try:
        branch_index, branch = next(
            (index, item)
            for index, item in enumerate(contract.branches)
            if item.branch_key == branch_key
        )
    except StopIteration as exc:
        raise ValueError(f"unknown seven-branch key: {branch_key}") from exc
    if round_ordinal < 0:
        raise ValueError("branch round ordinal cannot be negative")
    if generator_allocation_policy not in {
        "balanced_then_yield_v1",
        "safety_biased_hydramp_v1",
    }:
        raise ValueError("unknown generator allocation policy")
    resolved_budget = branch.initial_raw_budget if raw_budget is None else raw_budget
    if resolved_budget <= 0 or resolved_budget % 300 != 0:
        raise ValueError("branch raw budget must be a positive multiple of 300")
    cell_count = resolved_budget // 100
    if round_ordinal < 2:
        per_generator = cell_count // 3
        generators = tuple(
            generator
            for generator in ("hydramp", "ampgan_v2", "amp_designer")
            for _ in range(per_generator)
        )
    elif generator_allocation_policy == "safety_biased_hydramp_v1":
        # Round-7/8 target-agnostic audits showed joint low-toxicity/low-hemolysis
        # yields of 38.46% (HydrAMP), 11.43% (AMP-GAN), and 4.53%
        # (AMP-Designer). Keep all independent arms for frontier diversity while
        # allocating most new evidence to the safer observed scaffold source.
        hydramp_cells = max(1, round(cell_count * 0.70))
        ampgan_cells = max(1, round(cell_count * 0.20))
        amp_designer_cells = cell_count - hydramp_cells - ampgan_cells
        if amp_designer_cells < 1:
            amp_designer_cells = 1
            hydramp_cells = cell_count - ampgan_cells - amp_designer_cells
        generators = (
            *("hydramp" for _ in range(hydramp_cells)),
            *("ampgan_v2" for _ in range(ampgan_cells)),
            *("amp_designer" for _ in range(amp_designer_cells)),
        )
    else:
        # Two balanced rounds established a durable yield ordering while showing
        # that all three arms still contribute distinct sequence families.  Later
        # rounds exploit the faster, higher-yield arm without dropping either
        # independent generator below one 100-occurrence cell.
        hydramp_cells = max(1, round(cell_count * 0.50))
        ampgan_cells = max(1, round(cell_count * 0.30))
        amp_designer_cells = cell_count - hydramp_cells - ampgan_cells
        if amp_designer_cells < 1:
            amp_designer_cells = 1
            hydramp_cells = cell_count - ampgan_cells - amp_designer_cells
        generators = (
            *("hydramp" for _ in range(hydramp_cells)),
            *("ampgan_v2" for _ in range(ampgan_cells)),
            *("amp_designer" for _ in range(amp_designer_cells)),
        )
    if len(generators) != cell_count:
        raise ValueError("generator allocation does not match the frozen cell budget")
    seed_base = 20_290_000 + branch_index * 10_000 + round_ordinal * 1_000
    cells = tuple(
        GeneratorCell(
            ordinal=ordinal,
            generator_id=generators[ordinal],
            seed=seed_base + ordinal + 1,
            requested_proposals=100,
        )
        for ordinal in range(cell_count)
    )
    execution = V38SequenceExecutionContract(
        cells=cells,
        expected_raw_occurrences=resolved_budget,
        metric_plugins=tuple(V38_METRIC_OBSERVATIONS),
        required_sequence_metrics=contract.required_sequence_metrics,
    )
    binding = SevenBranchRoundBinding(
        design_contract_sha256=contract.sha256(),
        branch_key=branch.branch_key,
        branch_kind=branch.branch_kind,
        target_key=branch.target_key,
        target_sequence_sha256=branch.target_sequence_sha256,
        round_ordinal=round_ordinal,
        expected_raw_occurrences=resolved_budget,
        execution_contract_sha256=execution.sha256(),
    )
    return binding, execution


class BranchProgress(FrozenModel):
    branch_key: str
    raw_count: int = Field(ge=0)
    valid_unique_count: int = Field(ge=0)
    fully_scored_count: int = Field(ge=0)
    target_sequence_scored_count: int = Field(ge=0)
    qualified_count: int = Field(ge=0)
    delivered_count: int = Field(ge=0)
    family_count: int = Field(ge=0)


QUALITY_ARCHIVE_KEYS = frozenset(
    {
        "activity_consensus",
        "amp_read_endpoint",
        "llamp_endpoint",
        "macrel_endpoint",
        "activity_safety_balance",
        "stability_degradation",
        "novel_family",
        "model_disagreement",
    }
)


class BranchQualityProgress(FrozenModel):
    """Durable quality state, kept separate from the row-delivery quota.

    Archive membership may overlap deliberately: a candidate can be both a novel
    family representative and an AMP-READ endpoint.  The controller consumes a
    frozen upstream quality policy; it does not invent weighted scores here.
    """

    schema_version: Literal["ampgent.seven-branch-quality-progress.1"] = (
        "ampgent.seven-branch-quality-progress.1"
    )
    branch_key: str = Field(min_length=1)
    quality_quota: int = Field(gt=0)
    quality_qualified_count: int = Field(ge=0)
    archive_counts: dict[str, int]
    underfilled_archives: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_archives(self) -> BranchQualityProgress:
        if set(self.archive_counts) != QUALITY_ARCHIVE_KEYS:
            raise ValueError("quality progress must report every frozen archive")
        if any(count < 0 for count in self.archive_counts.values()):
            raise ValueError("quality archive counts cannot be negative")
        if not set(self.underfilled_archives).issubset(QUALITY_ARCHIVE_KEYS):
            raise ValueError("underfilled quality archive is not frozen")
        if len(self.underfilled_archives) != len(set(self.underfilled_archives)):
            raise ValueError("underfilled quality archives must be unique")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class BranchTopUpPlan(FrozenModel):
    """A deterministic successor-round recommendation from durable branch yield."""

    schema_version: Literal[
        "ampgent.seven-branch-top-up-plan.1",
        "ampgent.seven-branch-top-up-plan.2",
    ] = (
        "ampgent.seven-branch-top-up-plan.2"
    )
    branch_key: str
    next_round_ordinal: int = Field(ge=1)
    requested_delivery_count: int = Field(gt=0)
    delivered_count: int = Field(ge=0)
    remaining_delivery_count: int = Field(ge=0)
    observed_raw_count: int = Field(ge=0)
    observed_qualified_count: int = Field(ge=0)
    observed_qualified_yield: float = Field(ge=0.0, le=1.0)
    planning_yield: float = Field(gt=0.0, le=1.0)
    safety_factor: float = Field(ge=1.0)
    uncapped_recommended_raw_budget: int | None = Field(
        default=None, ge=0, multiple_of=300
    )
    per_epoch_raw_budget_cap: int | None = Field(
        default=None, ge=0, multiple_of=300
    )
    budget_cap_applied: bool = False
    recommended_raw_budget: int = Field(ge=0, multiple_of=300)
    action: Literal["quota_complete", "freeze_successor_round"]

    @model_validator(mode="after")
    def validate_v2_budget_audit(self) -> BranchTopUpPlan:
        if self.schema_version == "ampgent.seven-branch-top-up-plan.2" and (
            self.uncapped_recommended_raw_budget is None
            or self.per_epoch_raw_budget_cap is None
        ):
            raise ValueError("v2 top-up plan requires bounded-budget audit fields")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class BranchQualityTopUpPlan(FrozenModel):
    """A bounded successor-round plan driven by observed high-quality yield."""

    schema_version: Literal["ampgent.seven-branch-quality-top-up-plan.1"] = (
        "ampgent.seven-branch-quality-top-up-plan.1"
    )
    branch_key: str = Field(min_length=1)
    next_round_ordinal: int = Field(ge=1)
    quality_quota: int = Field(gt=0)
    quality_qualified_count: int = Field(ge=0)
    remaining_quality_count: int = Field(ge=0)
    observed_raw_count: int = Field(ge=0)
    observed_quality_yield: float = Field(ge=0.0, le=1.0)
    planning_yield: float = Field(gt=0.0, le=1.0)
    safety_factor: float = Field(ge=1.0)
    uncapped_recommended_raw_budget: int = Field(ge=0, multiple_of=300)
    per_epoch_raw_budget_cap: int = Field(ge=0, multiple_of=300)
    budget_cap_applied: bool
    recommended_raw_budget: int = Field(ge=0, multiple_of=300)
    action: Literal["quality_quota_complete", "freeze_quality_successor_round"]

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class BranchDeliveryCandidate(FrozenModel):
    candidate_id: UUID
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    family_key: str = Field(min_length=1)
    admission_tier: Literal[
        "mature_core", "promising_uncertain", "exploration"
    ]
    sequence_pareto_front: int | None = Field(default=None, ge=0)
    target_conditional_nll: float | None = None
    target_conditional_ppl: float | None = None


class BranchDeliverySelection(FrozenModel):
    schema_version: Literal["ampgent.seven-branch-delivery-selection.1"] = (
        "ampgent.seven-branch-delivery-selection.1"
    )
    branch_key: str
    requested_delivery_count: int = Field(gt=0)
    considered_candidate_ids: tuple[UUID, ...]
    selected_candidate_ids: tuple[UUID, ...]
    selected_family_count: int = Field(ge=0)
    quota_complete: bool
    exact_sequence_deduplicated: Literal[True] = True
    family_first_pass: Literal[True] = True
    target_sequence_ranking_applied: bool

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


def select_branch_delivery(
    branch: DesignBranch,
    candidates: tuple[BranchDeliveryCandidate, ...],
) -> BranchDeliverySelection:
    """Select a reproducible, family-diverse delivery set without weighted scores."""

    unique_by_sequence: dict[str, BranchDeliveryCandidate] = {}
    for item in candidates:
        current = unique_by_sequence.get(item.sequence_sha256)
        if current is None or str(item.candidate_id) < str(current.candidate_id):
            unique_by_sequence[item.sequence_sha256] = item
    unique = tuple(unique_by_sequence.values())
    if branch.target_sequence_interaction_required and any(
        item.target_conditional_nll is None or item.target_conditional_ppl is None
        for item in unique
    ):
        raise ValueError("target-specific delivery requires target sequence scores")

    def rank(item: BranchDeliveryCandidate) -> tuple[Any, ...]:
        return (
            0 if item.admission_tier == "mature_core" else 1,
            item.sequence_pareto_front
            if item.sequence_pareto_front is not None
            else 1_000_000,
            item.target_conditional_nll
            if item.target_conditional_nll is not None
            else 0.0,
            item.target_conditional_ppl
            if item.target_conditional_ppl is not None
            else 0.0,
            str(item.candidate_id),
        )

    ranked = sorted(unique, key=rank)
    selected: list[BranchDeliveryCandidate] = []
    selected_ids: set[UUID] = set()
    seen_families: set[str] = set()
    for item in ranked:
        if item.family_key in seen_families:
            continue
        selected.append(item)
        selected_ids.add(item.candidate_id)
        seen_families.add(item.family_key)
        if len(selected) == branch.requested_delivery_count:
            break
    if len(selected) < branch.requested_delivery_count:
        for item in ranked:
            if item.candidate_id in selected_ids:
                continue
            selected.append(item)
            selected_ids.add(item.candidate_id)
            if len(selected) == branch.requested_delivery_count:
                break
    return BranchDeliverySelection(
        branch_key=branch.branch_key,
        requested_delivery_count=branch.requested_delivery_count,
        considered_candidate_ids=tuple(item.candidate_id for item in ranked),
        selected_candidate_ids=tuple(item.candidate_id for item in selected),
        selected_family_count=len({item.family_key for item in selected}),
        quota_complete=len(selected) == branch.requested_delivery_count,
        target_sequence_ranking_applied=branch.target_sequence_interaction_required,
    )


def delivery_eligible_candidate_ids(admission: dict[str, Any]) -> frozenset[UUID]:
    """Return every validity/safety-passing candidate, independent of structure budget.

    The legacy admission lists are deliberately capped by the optional structure
    budget.  Seven-branch delivery is sequence-first, so its candidate pool must use
    the complete typed decision set: mature core and promising/uncertain candidates
    remain eligible, while rejected candidates do not.
    """

    decisions = admission.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("seven-branch admission decisions are missing")
    eligible: set[UUID] = set()
    observed: set[UUID] = set()
    allowed = {"mature_core", "promising_uncertain", "rejected"}
    for item in decisions:
        if not isinstance(item, dict) or item.get("status") not in allowed:
            raise ValueError("seven-branch admission decision is invalid")
        candidate_id = UUID(str(item.get("candidate_id")))
        if candidate_id in observed:
            raise ValueError("seven-branch admission duplicated a candidate decision")
        observed.add(candidate_id)
        if item["status"] != "rejected":
            eligible.add(candidate_id)
    return frozenset(eligible)


def plan_branch_top_up(
    branch: DesignBranch,
    progress: BranchProgress,
    *,
    next_round_ordinal: int,
) -> BranchTopUpPlan:
    """Size an immutable top-up round from observed qualified yield.

    The planner never lowers admission criteria.  It adds a 50% occurrence reserve
    for cross-round exact duplicates and yield variance, then rounds up to one
    balanced three-generator block (300 occurrences).  The empirical estimate is
    retained for audit, while each immutable epoch is capped at 2,400 target-specific
    or 3,000 target-agnostic occurrences.  This prevents one low-yield observation
    from creating a huge, slow batch and lets later epochs adapt to durable yield.
    When the first round has no qualified candidates, it repeats at least the frozen
    initial breadth instead of inventing an optimistic yield.
    """

    if branch.branch_key != progress.branch_key:
        raise ValueError("progress belongs to another branch")
    if next_round_ordinal < 1:
        raise ValueError("top-up round ordinal must be positive")
    delivered = min(progress.delivered_count, branch.requested_delivery_count)
    remaining = branch.requested_delivery_count - delivered
    observed_yield = (
        min(1.0, progress.qualified_count / progress.raw_count)
        if progress.raw_count
        else 0.0
    )
    safety_factor = 1.5
    per_epoch_cap = 2400 if branch.branch_kind == "target_specific" else 3000
    if remaining == 0:
        uncapped_budget = 0
        budget = 0
        action: Literal["quota_complete", "freeze_successor_round"] = (
            "quota_complete"
        )
        planning_yield = observed_yield if observed_yield > 0 else 1.0
    else:
        action = "freeze_successor_round"
        if observed_yield > 0:
            planning_yield = observed_yield
            needed = math.ceil((remaining / planning_yield) * safety_factor)
            uncapped_budget = math.ceil(needed / 300) * 300
        else:
            planning_yield = min(
                1.0,
                branch.requested_delivery_count / branch.initial_raw_budget,
            )
            uncapped_budget = max(
                branch.initial_raw_budget,
                math.ceil(
                    ((remaining / planning_yield) * safety_factor) / 300
                )
                * 300,
            )
        budget = min(uncapped_budget, per_epoch_cap)
    return BranchTopUpPlan(
        branch_key=branch.branch_key,
        next_round_ordinal=next_round_ordinal,
        requested_delivery_count=branch.requested_delivery_count,
        delivered_count=delivered,
        remaining_delivery_count=remaining,
        observed_raw_count=progress.raw_count,
        observed_qualified_count=progress.qualified_count,
        observed_qualified_yield=observed_yield,
        planning_yield=planning_yield,
        safety_factor=safety_factor,
        uncapped_recommended_raw_budget=uncapped_budget,
        per_epoch_raw_budget_cap=per_epoch_cap,
        budget_cap_applied=uncapped_budget > budget,
        recommended_raw_budget=budget,
        action=action,
    )


def plan_branch_quality_top_up(
    branch: DesignBranch,
    progress: BranchProgress,
    quality: BranchQualityProgress,
    *,
    next_round_ordinal: int,
) -> BranchQualityTopUpPlan:
    """Size the next immutable round from durable high-quality yield.

    The quality quota normally equals the requested delivery quota, so a branch
    keeps evolving until it contains enough candidates that pass the separately
    frozen quality policy.  Model-disagreement endpoints remain valid archive
    members and are not averaged away by this planner.
    """

    if branch.branch_key != progress.branch_key or branch.branch_key != quality.branch_key:
        raise ValueError("progress belongs to another branch")
    if quality.quality_quota != branch.requested_delivery_count:
        raise ValueError("quality quota must equal the frozen branch delivery quota")
    if next_round_ordinal < 1:
        raise ValueError("quality top-up round ordinal must be positive")
    qualified = min(quality.quality_qualified_count, quality.quality_quota)
    remaining = quality.quality_quota - qualified
    observed_yield = (
        min(1.0, quality.quality_qualified_count / progress.raw_count)
        if progress.raw_count
        else 0.0
    )
    safety_factor = 1.5
    per_epoch_cap = 2400 if branch.branch_kind == "target_specific" else 3000
    if remaining == 0:
        planning_yield = observed_yield if observed_yield > 0 else 1.0
        uncapped_budget = 0
        budget = 0
        action: Literal[
            "quality_quota_complete", "freeze_quality_successor_round"
        ] = "quality_quota_complete"
    else:
        action = "freeze_quality_successor_round"
        if observed_yield > 0:
            planning_yield = observed_yield
            needed = math.ceil((remaining / planning_yield) * safety_factor)
            uncapped_budget = math.ceil(needed / 300) * 300
        else:
            planning_yield = min(
                1.0,
                branch.requested_delivery_count / branch.initial_raw_budget,
            )
            uncapped_budget = max(
                branch.initial_raw_budget,
                math.ceil(((remaining / planning_yield) * safety_factor) / 300)
                * 300,
            )
        budget = min(uncapped_budget, per_epoch_cap)
    return BranchQualityTopUpPlan(
        branch_key=branch.branch_key,
        next_round_ordinal=next_round_ordinal,
        quality_quota=quality.quality_quota,
        quality_qualified_count=qualified,
        remaining_quality_count=remaining,
        observed_raw_count=progress.raw_count,
        observed_quality_yield=observed_yield,
        planning_yield=planning_yield,
        safety_factor=safety_factor,
        uncapped_recommended_raw_budget=uncapped_budget,
        per_epoch_raw_budget_cap=per_epoch_cap,
        budget_cap_applied=uncapped_budget > budget,
        recommended_raw_budget=budget,
        action=action,
    )


BranchAction = Literal[
    "generate_or_refine_more",
    "complete_sequence_score_all",
    "complete_target_sequence_scoring",
    "qualify_and_diversify",
    "deliver_quota",
    "quota_complete",
]

QualityBranchAction = Literal[
    "generate_or_refine_more",
    "complete_sequence_score_all",
    "complete_target_sequence_scoring",
    "qualify_and_diversify",
    "construct_quality_archives",
    "quality_quota_complete",
]


def next_branch_action(branch: DesignBranch, progress: BranchProgress) -> BranchAction:
    if branch.branch_key != progress.branch_key:
        raise ValueError("progress belongs to another branch")
    if progress.delivered_count >= branch.requested_delivery_count:
        return "quota_complete"
    if progress.valid_unique_count < branch.requested_delivery_count:
        return "generate_or_refine_more"
    if progress.fully_scored_count < progress.valid_unique_count:
        return "complete_sequence_score_all"
    if (
        branch.target_sequence_interaction_required
        and progress.target_sequence_scored_count < progress.fully_scored_count
    ):
        return "complete_target_sequence_scoring"
    if progress.qualified_count < branch.requested_delivery_count:
        return "generate_or_refine_more"
    if progress.family_count == 0:
        return "qualify_and_diversify"
    return "deliver_quota"


def next_controller_branch(
    contract: SevenBranchDesignContract, progress_by_branch: dict[str, BranchProgress]
) -> tuple[str, BranchAction] | None:
    missing = set(branch.branch_key for branch in contract.branches) - set(progress_by_branch)
    if missing:
        branch_key = next(
            branch.branch_key
            for branch in contract.branches
            if branch.branch_key in missing
        )
        return branch_key, "generate_or_refine_more"
    unfinished = [
        branch
        for branch in contract.branches
        if progress_by_branch[branch.branch_key].delivered_count
        < branch.requested_delivery_count
    ]
    if not unfinished:
        return None
    branch = min(
        unfinished,
        key=lambda item: (
            progress_by_branch[item.branch_key].delivered_count
            / item.requested_delivery_count,
            item.branch_key,
        ),
    )
    return branch.branch_key, next_branch_action(branch, progress_by_branch[branch.branch_key])


def next_quality_branch_action(
    branch: DesignBranch,
    progress: BranchProgress,
    quality: BranchQualityProgress,
) -> QualityBranchAction:
    """Continue after row delivery when the frozen high-quality quota is not met."""

    if branch.branch_key != progress.branch_key or branch.branch_key != quality.branch_key:
        raise ValueError("progress belongs to another branch")
    if quality.quality_quota != branch.requested_delivery_count:
        raise ValueError("quality quota must equal the frozen branch delivery quota")
    if progress.valid_unique_count < branch.requested_delivery_count:
        return "generate_or_refine_more"
    if progress.fully_scored_count < progress.valid_unique_count:
        return "complete_sequence_score_all"
    if (
        branch.target_sequence_interaction_required
        and progress.target_sequence_scored_count < progress.fully_scored_count
    ):
        return "complete_target_sequence_scoring"
    if progress.family_count == 0:
        return "qualify_and_diversify"
    if quality.underfilled_archives and quality.quality_qualified_count == 0:
        return "construct_quality_archives"
    if quality.quality_qualified_count < quality.quality_quota:
        return "generate_or_refine_more"
    return "quality_quota_complete"


def next_quality_controller_branch(
    contract: SevenBranchDesignContract,
    progress_by_branch: dict[str, BranchProgress],
    quality_by_branch: dict[str, BranchQualityProgress],
) -> tuple[str, QualityBranchAction] | None:
    """Choose the largest relative quality deficit, not the smallest row count."""

    branch_by_key = {branch.branch_key: branch for branch in contract.branches}
    for branch in contract.branches:
        if (
            branch.branch_key not in progress_by_branch
            or branch.branch_key not in quality_by_branch
        ):
            return branch.branch_key, "generate_or_refine_more"
    unfinished = [
        branch
        for branch in contract.branches
        if quality_by_branch[branch.branch_key].quality_qualified_count
        < quality_by_branch[branch.branch_key].quality_quota
    ]
    if not unfinished:
        return None
    branch = min(
        unfinished,
        key=lambda item: (
            quality_by_branch[item.branch_key].quality_qualified_count
            / quality_by_branch[item.branch_key].quality_quota,
            item.branch_key,
        ),
    )
    if branch.branch_key not in branch_by_key:
        raise ValueError("quality progress references an unknown branch")
    return (
        branch.branch_key,
        next_quality_branch_action(
            branch,
            progress_by_branch[branch.branch_key],
            quality_by_branch[branch.branch_key],
        ),
    )
