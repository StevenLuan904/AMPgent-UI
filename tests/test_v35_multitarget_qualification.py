from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_multitarget_qualification_v35.yaml"


def test_v35_is_qualification_only_and_cannot_cherry_pick_targets() -> None:
    contract = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert contract["execution_status"] == (
        "typed_ledger_and_offline_replay_implemented_not_authorized"
    )
    assert contract["implementation_revision"] is None
    assert contract["scope"]["target_names_selected"] is False
    assert contract["scope"]["target_selection_authorized"] is False
    assert contract["scope"]["candidate_generation_authorized"] is False
    assert contract["scope"]["formal_run_authorized"] is False
    assert contract["selection_separation"]["qualification_before_any_candidate_generation"]
    assert contract["selection_separation"]["minimum_new_target_shortlist_size"] >= 8
    assert contract["selection_separation"]["all_shortlisted_targets_must_receive_audit_outcome"]
    assert contract["selection_separation"]["failed_targets_remain_in_failure_denominator"]
    assert set(contract["selection_separation"]["target_selection_forbids"]) >= {
        "Boltz_pose",
        "pair_ipTM",
        "Rosetta_REU",
        "generated_peptide_score",
    }


def test_v35_primary_targets_require_evidence_and_negative_controls() -> None:
    contract = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert contract["pocket_evidence_grades"]["primary_panel_requires_grade_A_or_B"]
    assert contract["pocket_evidence_grades"]["C"]["exploratory_only"]
    assert contract["pocket_evidence_grades"]["D"]["excluded"]
    assert set(contract["control_contract"]["per_target_required_controls"]) == {
        "native_functional_pocket",
        "same_target_wrong_or_decoy_pocket",
        "target_agnostic_AMP_lane",
    }
    assert contract["control_contract"]["wrong_pocket_must_be_selected_before_peptide_outputs"]
    assert contract["future_comparison_contract"]["weighted_total_score_forbidden"]


def test_v35_requires_database_replay_and_keeps_claims_scoped() -> None:
    contract = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    evidence = contract["database_evidence_contract"]
    assert evidence["PostgreSQL_is_authoritative"]
    assert evidence["persist_rejected_targets_and_reasons"]
    assert evidence["database_object_store_only_replay_required"]
    assert "complete_target_shortlist_and_order" in evidence["replay_must_reconstruct"]
    boundaries = contract["scientific_boundaries"]
    assert boundaries["no_binding_affinity_or_selectivity_claim"]
    assert boundaries["target_generalization_is_protocol_scoped"]
    assert boundaries["failed_targets_cannot_be_silently_removed"]


def test_v35_freezes_offline_replay_but_fails_closed_on_typed_persistence_gap() -> None:
    contract = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    replay = contract["qualification_replay_contract"]
    assert replay["schema_version"] == "v35.target-qualification-replay.1"
    assert replay["selection_method"] == "hard_gate_then_anchor_aware_maximin_v1"
    assert replay["complete_rejection_denominator_required"] is True
    assert replay["selected_primary_targets_require_grade_A_or_B"] is True
    gap = contract["typed_persistence_gap"]
    assert gap["typed_target_qualification_audit_entity_implemented"] is False
    assert gap["typed_panel_selection_witness_entity_implemented"] is False
    assert gap["migration_deployed_to_shared_PostgreSQL"] is False
    assert gap["target_audit_execution_forbidden_until_gap_closed_and_separately_authorized"]
