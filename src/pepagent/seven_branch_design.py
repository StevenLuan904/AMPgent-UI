from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pepagent.provenance.hashing import sha256_json

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
