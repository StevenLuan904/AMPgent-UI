from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from pepagent.seven_branch_design import (
    SEQUENCE_METRICS,
    BranchProgress,
    DesignBranch,
    SevenBranchDesignContract,
    SevenBranchDesignSchedule,
    SevenBranchRoundRequest,
    build_seven_branch_round_execution_contract,
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
            target_sequence_sha256=hashlib.sha256(
                ("A" * (index + 1)).encode("utf-8")
            ).hexdigest(),
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


def test_branch_round_projects_balanced_initial_generator_cells() -> None:
    contract = _contract()
    target_binding, target_execution = build_seven_branch_round_execution_contract(
        contract, branch_key="acea", round_ordinal=0
    )
    assert len(target_execution.cells) == 6
    assert target_execution.expected_raw_occurrences == 600
    assert {cell.generator_id for cell in target_execution.cells} == {
        "hydramp",
        "ampgan_v2",
        "amp_designer",
    }
    assert target_binding.execution_contract_sha256 == target_execution.sha256()

    amp_binding, amp_execution = build_seven_branch_round_execution_contract(
        contract, branch_key="target_agnostic_amp", round_ordinal=0
    )
    assert len(amp_execution.cells) == 30
    assert amp_execution.expected_raw_occurrences == 3000
    assert amp_binding.target_key is None


def test_branch_round_top_up_budget_must_keep_three_generator_balance() -> None:
    with pytest.raises(ValueError, match="multiple of 300"):
        build_seven_branch_round_execution_contract(
            _contract(), branch_key="acea", round_ordinal=1, raw_budget=500
        )


def test_schedule_rejects_identity_or_contract_drift() -> None:
    contract = _contract()
    round_requests = []
    for index, branch in enumerate(contract.branches):
        binding, execution = build_seven_branch_round_execution_contract(
            contract, branch_key=branch.branch_key, round_ordinal=0
        )
        run_id = UUID(int=index + 1)
        round_requests.append(
            SevenBranchRoundRequest(
                run_id=run_id,
                workflow_id=f"seven-branch-{branch.branch_key}-0",
                request={
                    "run_id": str(run_id),
                    "seven_branch_round": binding.model_dump(mode="json"),
                    "execution_contract": execution.model_dump(mode="json"),
                    "task_queues": {
                        "target_sequence": "pepagent-gpu-target-sequence-v39"
                    },
                },
            )
        )
    schedule = SevenBranchDesignSchedule(
        controller_run_id=UUID("22222222-2222-2222-2222-222222222222"),
        design_contract=contract,
        target_runtime_by_key={
            branch.target_key: {
                "target_key": branch.target_key,
                "accession": f"TEST_{index}",
                "sequence": "A" * (index + 1),
                "sequence_sha256": branch.target_sequence_sha256,
            }
            for index, branch in enumerate(contract.branches[:6])
        },
        rounds=tuple(round_requests),
    )
    assert schedule.sha256()

    drifted = round_requests[0].model_dump(mode="json")
    drifted["request"]["execution_contract"]["expected_raw_occurrences"] = 900
    with pytest.raises(ValueError, match="identity drifted"):
        SevenBranchRoundRequest.model_validate(drifted)
