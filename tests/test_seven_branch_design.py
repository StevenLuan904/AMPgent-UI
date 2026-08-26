from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from pepagent.seven_branch_design import (
    QUALITY_ARCHIVE_KEYS,
    SEQUENCE_METRICS,
    BranchDeliveryCandidate,
    BranchProgress,
    BranchQualityProgress,
    BranchQualityTopUpPlan,
    BranchTopUpPlan,
    DesignBranch,
    SevenBranchDesignContract,
    SevenBranchDesignSchedule,
    SevenBranchRoundRequest,
    SevenBranchTopUpEpochBranch,
    SevenBranchTopUpSchedule,
    build_seven_branch_round_execution_contract,
    delivery_eligible_candidate_ids,
    next_branch_action,
    next_controller_branch,
    next_quality_branch_action,
    next_quality_controller_branch,
    plan_branch_quality_top_up,
    plan_branch_top_up,
    select_branch_delivery,
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


def _quality(
    branch_key: str,
    quality_quota: int,
    quality_qualified_count: int = 0,
    *,
    underfilled_archives: tuple[str, ...] = (),
    archive_overrides: dict[str, int] | None = None,
) -> BranchQualityProgress:
    archives = {key: 0 for key in QUALITY_ARCHIVE_KEYS}
    archives.update(archive_overrides or {})
    return BranchQualityProgress(
        branch_key=branch_key,
        quality_quota=quality_quota,
        quality_qualified_count=quality_qualified_count,
        archive_counts=archives,
        underfilled_archives=underfilled_archives,
    )


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


def test_quality_controller_continues_after_row_quota_is_complete() -> None:
    branch = _contract().branches[0]
    progress = _progress(
        branch.branch_key,
        raw_count=600,
        valid_unique_count=150,
        fully_scored_count=150,
        target_sequence_scored_count=150,
        qualified_count=150,
        delivered_count=150,
        family_count=150,
    )
    quality = _quality(
        branch.branch_key,
        branch.requested_delivery_count,
        55,
        archive_overrides={"model_disagreement": 7, "amp_read_endpoint": 5},
    )
    assert next_branch_action(branch, progress) == "quota_complete"
    assert next_quality_branch_action(branch, progress, quality) == (
        "generate_or_refine_more"
    )
    plan = plan_branch_quality_top_up(
        branch,
        progress,
        quality,
        next_round_ordinal=1,
    )
    assert isinstance(plan, BranchQualityTopUpPlan)
    assert plan.action == "freeze_quality_successor_round"
    assert plan.remaining_quality_count == 95
    assert plan.recommended_raw_budget > 0


def test_quality_archives_preserve_overlapping_disagreement_endpoints() -> None:
    quality = _quality(
        "acea",
        150,
        55,
        archive_overrides={
            "activity_consensus": 20,
            "amp_read_endpoint": 12,
            "llamp_endpoint": 15,
            "macrel_endpoint": 11,
            "novel_family": 55,
            "model_disagreement": 40,
        },
    )
    assert quality.archive_counts["model_disagreement"] == 40
    assert sum(quality.archive_counts.values()) > quality.quality_qualified_count
    assert quality.sha256()


def test_quality_controller_prioritizes_largest_relative_quality_shortfall() -> None:
    contract = _contract()
    progress = {
        branch.branch_key: _progress(
            branch.branch_key,
            raw_count=branch.initial_raw_budget,
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
    quality = {
        branch.branch_key: _quality(
            branch.branch_key,
            branch.requested_delivery_count,
            branch.requested_delivery_count,
        )
        for branch in contract.branches
    }
    quality["acea"] = _quality("acea", 150, 55)
    quality["target_agnostic_amp"] = _quality("target_agnostic_amp", 1000, 165)
    assert next_quality_controller_branch(contract, progress, quality) == (
        "target_agnostic_amp",
        "generate_or_refine_more",
    )


def test_quality_controller_finishes_only_when_quality_quota_is_complete() -> None:
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
            family_count=branch.requested_delivery_count,
        )
        for branch in contract.branches
    }
    quality = {
        branch.branch_key: _quality(
            branch.branch_key,
            branch.requested_delivery_count,
            branch.requested_delivery_count,
        )
        for branch in contract.branches
    }
    assert next_quality_controller_branch(contract, progress, quality) is None


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


def test_later_top_up_round_uses_yield_adaptive_mix_with_generator_floor() -> None:
    _, target_execution = build_seven_branch_round_execution_contract(
        _contract(), branch_key="acea", round_ordinal=2, raw_budget=600
    )
    target_generators = [cell.generator_id for cell in target_execution.cells]
    assert target_generators.count("hydramp") == 3
    assert target_generators.count("ampgan_v2") == 2
    assert target_generators.count("amp_designer") == 1

    _, amp_execution = build_seven_branch_round_execution_contract(
        _contract(),
        branch_key="target_agnostic_amp",
        round_ordinal=2,
        raw_budget=3000,
    )
    amp_generators = [cell.generator_id for cell in amp_execution.cells]
    assert amp_generators.count("hydramp") == 15
    assert amp_generators.count("ampgan_v2") == 9
    assert amp_generators.count("amp_designer") == 6


def test_later_round_supports_explicit_safety_biased_generator_mix() -> None:
    _, execution = build_seven_branch_round_execution_contract(
        _contract(),
        branch_key="target_agnostic_amp",
        round_ordinal=9,
        raw_budget=3000,
        generator_allocation_policy="safety_biased_hydramp_v1",
    )
    generators = [cell.generator_id for cell in execution.cells]

    assert generators.count("hydramp") == 21
    assert generators.count("ampgan_v2") == 6
    assert generators.count("amp_designer") == 3


def test_top_up_plan_uses_observed_yield_and_balanced_budget() -> None:
    branch = _contract().branches[0]
    plan = plan_branch_top_up(
        branch,
        _progress(
            branch.branch_key,
            raw_count=600,
            valid_unique_count=510,
            fully_scored_count=510,
            target_sequence_scored_count=510,
            qualified_count=90,
            delivered_count=90,
            family_count=500,
        ),
        next_round_ordinal=1,
    )
    assert plan.observed_qualified_yield == pytest.approx(0.15)
    assert plan.remaining_delivery_count == 60
    assert plan.recommended_raw_budget == 600
    assert plan.action == "freeze_successor_round"
    _, execution = build_seven_branch_round_execution_contract(
        _contract(),
        branch_key=branch.branch_key,
        round_ordinal=plan.next_round_ordinal,
        raw_budget=plan.recommended_raw_budget,
    )
    assert len(execution.cells) == 6


def test_top_up_plan_repeats_initial_breadth_when_observed_yield_is_zero() -> None:
    branch = _contract().branches[0]
    plan = plan_branch_top_up(
        branch,
        _progress(branch.branch_key, raw_count=600),
        next_round_ordinal=1,
    )
    assert plan.observed_qualified_yield == 0
    assert plan.recommended_raw_budget == 900
    assert plan.action == "freeze_successor_round"


def test_top_up_plan_is_zero_after_quota_is_delivered() -> None:
    branch = _contract().branches[-1]
    plan = plan_branch_top_up(
        branch,
        _progress(
            branch.branch_key,
            raw_count=3000,
            qualified_count=1200,
            delivered_count=1000,
        ),
        next_round_ordinal=1,
    )
    assert plan.action == "quota_complete"
    assert plan.recommended_raw_budget == 0


def test_top_up_plan_caps_low_yield_target_agnostic_epoch() -> None:
    branch = _contract().branches[-1]
    plan = plan_branch_top_up(
        branch,
        _progress(
            branch.branch_key,
            raw_count=3000,
            qualified_count=48,
            delivered_count=48,
        ),
        next_round_ordinal=1,
    )
    assert plan.schema_version == "ampgent.seven-branch-top-up-plan.2"
    assert plan.uncapped_recommended_raw_budget == 89400
    assert plan.per_epoch_raw_budget_cap == 3000
    assert plan.budget_cap_applied is True
    assert plan.recommended_raw_budget == 3000


def test_v1_top_up_plan_remains_readable_without_v2_audit_fields() -> None:
    legacy = BranchTopUpPlan.model_validate(
        {
            "schema_version": "ampgent.seven-branch-top-up-plan.1",
            "branch_key": "acea",
            "next_round_ordinal": 1,
            "requested_delivery_count": 150,
            "delivered_count": 48,
            "remaining_delivery_count": 102,
            "observed_raw_count": 600,
            "observed_qualified_count": 48,
            "observed_qualified_yield": 0.08,
            "planning_yield": 0.08,
            "safety_factor": 1.5,
            "recommended_raw_budget": 2100,
            "action": "freeze_successor_round",
        }
    )
    assert legacy.uncapped_recommended_raw_budget is None
    assert legacy.per_epoch_raw_budget_cap is None


def test_delivery_eligibility_is_not_truncated_by_structure_budget() -> None:
    admission = {
        "decisions": [
            {"candidate_id": str(UUID(int=1)), "status": "mature_core"},
            {
                "candidate_id": str(UUID(int=2)),
                "status": "promising_uncertain",
            },
            {"candidate_id": str(UUID(int=3)), "status": "rejected"},
        ],
        "mature_core_candidate_ids": [str(UUID(int=1))],
        "exploration_candidate_ids": [],
    }
    assert delivery_eligible_candidate_ids(admission) == {
        UUID(int=1),
        UUID(int=2),
    }


def test_target_delivery_deduplicates_and_uses_target_score_with_family_first() -> None:
    branch = _contract().branches[0]
    records = (
        BranchDeliveryCandidate(
            candidate_id=UUID(int=1),
            sequence_sha256="1" * 64,
            family_key="family-a",
            admission_tier="mature_core",
            sequence_pareto_front=0,
            target_conditional_nll=3.0,
            target_conditional_ppl=20.0,
        ),
        BranchDeliveryCandidate(
            candidate_id=UUID(int=2),
            sequence_sha256="2" * 64,
            family_key="family-a",
            admission_tier="mature_core",
            sequence_pareto_front=0,
            target_conditional_nll=1.0,
            target_conditional_ppl=10.0,
        ),
        BranchDeliveryCandidate(
            candidate_id=UUID(int=3),
            sequence_sha256="3" * 64,
            family_key="family-b",
            admission_tier="exploration",
            target_conditional_nll=2.0,
            target_conditional_ppl=15.0,
        ),
        BranchDeliveryCandidate(
            candidate_id=UUID(int=4),
            sequence_sha256="2" * 64,
            family_key="family-c",
            admission_tier="mature_core",
            sequence_pareto_front=0,
            target_conditional_nll=0.5,
            target_conditional_ppl=5.0,
        ),
    )
    selection = select_branch_delivery(branch, records)
    assert selection.considered_candidate_ids == (
        UUID(int=2),
        UUID(int=1),
        UUID(int=3),
    )
    assert selection.selected_candidate_ids == (
        UUID(int=2),
        UUID(int=3),
        UUID(int=1),
    )
    assert selection.selected_family_count == 2
    assert selection.quota_complete is False


def test_target_delivery_rejects_missing_target_sequence_scores() -> None:
    branch = _contract().branches[0]
    with pytest.raises(ValueError, match="requires target sequence scores"):
        select_branch_delivery(
            branch,
            (
                BranchDeliveryCandidate(
                    candidate_id=UUID(int=1),
                    sequence_sha256="1" * 64,
                    family_key="family-a",
                    admission_tier="mature_core",
                ),
            ),
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


def test_top_up_schedule_freezes_prior_evidence_plan_and_new_child() -> None:
    contract = _contract()
    branch = contract.branches[0]
    progress = _progress(
        branch.branch_key,
        raw_count=600,
        valid_unique_count=515,
        fully_scored_count=515,
        target_sequence_scored_count=515,
        qualified_count=48,
        delivered_count=48,
        family_count=48,
    )
    plan = plan_branch_top_up(
        branch, progress, next_round_ordinal=1
    )
    binding, execution = build_seven_branch_round_execution_contract(
        contract,
        branch_key=branch.branch_key,
        round_ordinal=1,
        raw_budget=plan.recommended_raw_budget,
    )
    run_id = UUID(int=500)
    frozen = SevenBranchRoundRequest(
        run_id=run_id,
        workflow_id="seven-branch-acea-r1",
        request={
            "run_id": str(run_id),
            "seven_branch_round": binding.model_dump(mode="json"),
            "execution_contract": execution.model_dump(mode="json"),
            "task_queues": {"target_sequence": "target"},
        },
    )
    schedule = SevenBranchTopUpSchedule(
        controller_run_id=UUID(int=600),
        parent_controller_run_id=UUID(int=599),
        epoch_ordinal=1,
        design_contract=contract,
        target_runtime_by_key={
            target_branch.target_key: {
                "target_key": target_branch.target_key,
                "accession": f"TEST_{index}",
                "sequence": "A" * (index + 1),
                "sequence_sha256": target_branch.target_sequence_sha256,
            }
            for index, target_branch in enumerate(contract.branches[:6])
        },
        branches=(
            SevenBranchTopUpEpochBranch(
                branch_key=branch.branch_key,
                prior_source_run_ids=(UUID(int=100),),
                prior_evidence_snapshot_sha256="e" * 64,
                top_up_plan=plan,
                frozen_round=frozen,
            ),
        ),
    )
    assert schedule.sha256()
    assert schedule.branches[0].top_up_plan.recommended_raw_budget == 2100
