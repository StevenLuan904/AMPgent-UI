from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


def build_seven_branch_round_execution_contract(
    contract: SevenBranchDesignContract,
    *,
    branch_key: str,
    round_ordinal: int,
    raw_budget: int | None = None,
) -> tuple[SevenBranchRoundBinding, V38SequenceExecutionContract]:
    """Project one branch budget into balanced, frozen 100-occurrence generator cells."""

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
    resolved_budget = branch.initial_raw_budget if raw_budget is None else raw_budget
    if resolved_budget <= 0 or resolved_budget % 300 != 0:
        raise ValueError("branch raw budget must be a positive multiple of 300")
    cell_count = resolved_budget // 100
    per_generator = cell_count // 3
    generators = ("hydramp", "ampgan_v2", "amp_designer")
    seed_base = 20_290_000 + branch_index * 10_000 + round_ordinal * 1_000
    cells = tuple(
        GeneratorCell(
            ordinal=ordinal,
            generator_id=generators[ordinal // per_generator],
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


BranchAction = Literal[
    "generate_or_refine_more",
    "complete_sequence_score_all",
    "complete_target_sequence_scoring",
    "qualify_and_diversify",
    "deliver_quota",
    "quota_complete",
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
