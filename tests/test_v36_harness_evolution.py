from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_harness_evolution_v36.yaml"


def _contract() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_v36_is_governance_only_and_cannot_run_or_generate() -> None:
    contract = _contract()
    assert contract["execution_status"] == (
        "typed_schema_and_offline_verifier_implemented_not_deployed_not_authorized"
    )
    scope = contract["scope"]
    assert scope["harness_schema_implementation_authorized"]
    assert scope["harness_schema_implementation_completed_in_repository"]
    assert scope["migration_deployed_to_shared_PostgreSQL"] is False
    assert scope["historical_replay_authorized"] is False
    assert scope["shadow_challenger_authorized"] is False
    assert scope["prospective_champion_challenger_authorized"] is False
    assert scope["candidate_generation_authorized"] is False
    assert scope["formal_run_authorized"] is False
    assert scope["frozen_runs_may_be_mutated"] is False


def test_v36_separates_history_and_requires_minimal_attributable_change() -> None:
    contract = _contract()
    history = contract["history_partition_contract"]
    assert history["chronological_partition_required"]
    assert set(history["partitions"]) == {
        "proposal_history",
        "counterfactual_replay",
        "shadow",
        "prospective_holdout",
    }
    assert history["no_episode_may_cross_partitions"]
    assert history["holdout_outcome_or_final_decision_leakage_fails_closed"]
    assert contract["philosophy"]["one_minimal_attributable_change_per_challenger"]
    assert contract["change_proposal_contract"]["multiple_unattributable_changes_forbidden"]
    assert "posthoc_threshold_search" in contract["change_proposal_contract"][
        "forbidden_change_sources"
    ]


def test_v36_requires_all_five_gates_before_scoped_promotion() -> None:
    contract = _contract()
    cycle = contract["five_gate_evolution_cycle"]
    assert list(cycle) == [
        "gate_1_failure_pattern_mining",
        "gate_2_counterfactual_replay",
        "gate_3_shadow_challenger",
        "gate_4_prospective_equal_budget_trial",
        "gate_5_promotion_or_rollback",
    ]
    assert all(gate["status"] == "not_authorized" for gate in cycle.values())
    assert cycle["gate_3_shadow_challenger"]["champion_controls_formal_actions"]
    assert cycle["gate_4_prospective_equal_budget_trial"][
        "same_inputs_seeds_resource_class_and_stopping_rules"
    ]
    assert cycle["gate_5_promotion_or_rollback"]["global_promotion_by_default_forbidden"]


def test_v36_promotion_is_multi_endpoint_and_not_self_scored() -> None:
    contract = _contract()
    evaluation = contract["evaluation_contract"]
    assert set(evaluation["independent_endpoint_families"]) == {
        "discovery_quality",
        "error_control",
        "stability",
        "efficiency",
        "evidence_quality",
    }
    assert "improvement_on_at_least_one_preregistered_practical_endpoint" in evaluation[
        "promotion_requires"
    ]
    assert "no_unacceptable_degradation_in_any_protected_endpoint_family" in evaluation[
        "promotion_requires"
    ]
    assert evaluation[
        "generation_or_selection_metrics_cannot_be_the_only_acceptance_endpoint"
    ]
    assert evaluation["no_single_hypervolume_or_weighted_utility_promotion"]


def test_v36_fails_closed_until_typed_lineage_is_migrated_and_accepted() -> None:
    contract = _contract()
    evidence = contract["typed_database_contract"]
    assert evidence["JSON_only_lineage_is_insufficient"]
    assert set(evidence["required_typed_entities"]) == {
        "HarnessRelease",
        "HarnessLineageEdge",
        "HarnessTrial",
        "HarnessAssignment",
        "HarnessOutcome",
        "HarnessPromotionDecision",
    }
    gap = evidence["current_schema_gap"]
    assert gap["typed_harness_entities_implemented_in_repository"]
    assert gap["offline_replay_verifier_implemented_in_repository"]
    assert gap["migration_deployed_to_shared_PostgreSQL"] is False
    assert gap["synthetic_database_replay_acceptance_completed"] is False
    assert gap["execution_forbidden_until_migrated_accepted_and_separately_authorized"]
    assert evidence["database_object_store_only_replay_required"]
    assert "complete_harness_lineage_and_immutable_footprints" in evidence[
        "replay_must_reconstruct"
    ]
    assert contract["rollback_contract"]["rollback_is_append_only_event_not_history_rewrite"]


def test_v36_keeps_provider_defects_out_of_ampgent() -> None:
    contract = _contract()
    providers = contract["provider_ownership"]
    assert providers["PepShot_task"] == "019fb910-f2dd-7be1-a7e6-bfe381512c25"
    assert providers["provider_defects_are_fixed_by_provider_not_AMPgent"]
    assert providers["AMPgent_provider_specific_adaptation_forbidden"]
