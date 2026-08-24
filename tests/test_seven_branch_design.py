from __future__ import annotations

import json
from pathlib import Path

from pepagent.seven_branch_design import (
    SEQUENCE_METRICS,
    BranchProgress,
    DesignBranch,
    SevenBranchDesignContract,
    next_branch_action,
    next_controller_branch,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _contract() -> SevenBranchDesignContract:
    target_keys = ("acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1")
    branches = tuple(
        DesignBranch(
            branch_key=target_key,
            branch_kind="target_specific",
            target_key=target_key,
            target_sequence_sha256=(f"{index + 1:x}" * 64)[:64],
            requested_delivery_count=150,
            initial_raw_budget=600,
            target_sequence_interaction_required=True,
            structure_scoring="optional",
        )
        for index, target_key in enumerate(target_keys)
    ) + (
        DesignBranch(
            branch_key="target_agnostic_amp",
            branch_kind="target_agnostic",
            requested_delivery_count=1000,
            initial_raw_budget=3000,
            target_sequence_interaction_required=False,
            structure_scoring="not_applicable",
        ),
    )
    return SevenBranchDesignContract(
        target_manifest_sha256="a" * 64,
        model_selection_sha256="b" * 64,
        historical_winner_stability_sha256="c" * 64,
        historical_family_summary_sha256="d" * 64,
        branches=branches,
        required_sequence_metrics=SEQUENCE_METRICS,
    )


def _progress(branch_key: str, **overrides: int) -> BranchProgress:
    values = {
        "raw_count": 0,
        "valid_unique_count": 0,
        "fully_scored_count": 0,
        "target_sequence_scored_count": 0,
        "qualified_count": 0,
        "delivered_count": 0,
        "family_count": 0,
    }
    values.update(overrides)
    return BranchProgress(branch_key=branch_key, **values)


def test_contract_freezes_six_by_150_plus_one_thousand() -> None:
    contract = _contract()
    assert len(contract.branches) == 7
    assert sum(branch.requested_delivery_count for branch in contract.branches) == 1900
    assert len(contract.required_sequence_metrics) == 12
    assert contract.shared_cross_target_binding_required is False
    assert contract.historical_work_output_reuse_allowed is False


def test_frozen_json_materializes_the_executable_contract() -> None:
    payload = json.loads(
        (
            REPO_ROOT
            / "config"
            / "workflows"
            / "ampgent_seven_branch_design_v1.json"
        ).read_text(encoding="utf-8")
    )
    contract = SevenBranchDesignContract.model_validate(payload)
    assert contract.sha256()
    assert sum(branch.initial_raw_budget for branch in contract.branches) == 6600


def test_target_branch_requires_pair_scoring_but_not_structure() -> None:
    branch = _contract().branches[0]
    progress = _progress(
        branch.branch_key,
        valid_unique_count=200,
        fully_scored_count=200,
        target_sequence_scored_count=0,
    )
    assert next_branch_action(branch, progress) == "complete_target_sequence_scoring"
    assert branch.structure_scoring == "optional"


def test_controller_prioritizes_largest_relative_delivery_shortfall() -> None:
    contract = _contract()
    progress = {
        branch.branch_key: _progress(
            branch.branch_key,
            valid_unique_count=branch.requested_delivery_count,
            fully_scored_count=branch.requested_delivery_count,
            target_sequence_scored_count=(
                branch.requested_delivery_count
                if branch.target_sequence_interaction_required
                else 0
            ),
            qualified_count=branch.requested_delivery_count,
            delivered_count=branch.requested_delivery_count,
            family_count=branch.requested_delivery_count,
        )
        for branch in contract.branches
    }
    progress["fgf2"] = _progress("fgf2", valid_unique_count=20)
    progress["target_agnostic_amp"] = _progress(
        "target_agnostic_amp", valid_unique_count=500, delivered_count=250
    )
    assert next_controller_branch(contract, progress) == (
        "fgf2",
        "generate_or_refine_more",
    )


def test_controller_finishes_only_when_all_seven_quotas_are_delivered() -> None:
    contract = _contract()
    progress = {
        branch.branch_key: _progress(
            branch.branch_key, delivered_count=branch.requested_delivery_count
        )
        for branch in contract.branches
    }
    assert next_controller_branch(contract, progress) is None
