from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_rapid_champion_generation_v37.yaml"


def _load() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_v37_is_single_arm_fixed_budget_direction_authorized_only() -> None:
    manifest = _load()

    assert manifest["benchmark_id"] == "amp_rapid_champion_generation_v37"
    assert manifest["execution_status"] == ("direction_authorized_pending_preexecution_gates")
    assert manifest["design"]["arms"] == 1
    assert manifest["design"]["ablation_or_tool_effect_comparison"] is False
    assert manifest["design"]["fixed_full_budget_required"] is True
    assert manifest["design"]["adaptive_early_stopping"] is False
    assert manifest["design"]["weighted_total_score_forbidden"] is True
    assert manifest["formal_run"] == {
        "direction_authorized": True,
        "execution_authorized": False,
        "submitted": False,
        "implementation_revision": None,
        "run_id": None,
        "workflow_id": None,
    }


def test_v37_budget_is_exact_and_internally_consistent() -> None:
    manifest = _load()
    generators = manifest["generators"]
    structure = manifest["stage_2_structure_confirmation"]

    engines = generators["engines"]
    assert [item["generator_id"] for item in engines] == [
        "hydramp",
        "ampgan_v2",
        "amp_designer",
    ]
    all_seeds = [seed for item in engines for seed in item["seeds"]]
    assert len(all_seeds) == len(set(all_seeds)) == 9
    assert generators["raw_proposals_per_generator_seed"] == 1000
    assert generators["evaluated_valid_unique_per_generator_seed"] == 100
    assert generators["expected_candidate_count"] == 900
    assert manifest["stage_1_sequence_evaluation"]["expected_candidate_count"] == 900
    assert manifest["stage_1_sequence_evaluation"]["shortlist"]["total_quota"] == 48
    assert sum(manifest["stage_1_sequence_evaluation"]["shortlist"]["lane_quotas"].values()) == 48
    assert structure["boltz_seeds"] == [20270380, 20270381, 20270382]
    assert structure["poses_per_candidate"] == len(structure["boltz_seeds"]) == 3
    assert structure["expected_maximum_poses"] == 48 * 3
    assert structure["rosetta_decoys_per_pose"] == 16
    assert structure["expected_maximum_rosetta_decoys"] == 2304
    assert structure["expected_maximum_rosetta_decoys"] == (
        structure["expected_maximum_poses"] * structure["rosetta_decoys_per_pose"]
    )
    assert manifest["final_portfolio"]["total_quota"] == 16
    assert sum(lane["quota"] for lane in manifest["final_portfolio"]["lanes"]) == 16


def test_v37_keeps_endpoint_families_separate_and_charge_observational() -> None:
    manifest = _load()
    endpoints = manifest["stage_1_sequence_evaluation"]["endpoint_families"]

    assert set(endpoints) == {"membrane", "activity_mic", "soft_risk"}
    assert manifest["stage_2_structure_confirmation"]["required_metric_families"]
    assert manifest["final_portfolio"]["method"][0] == (
        "compute_lane_local_nondominated_layers_without_cross_family_weighting"
    )
    assert manifest["scientific_boundaries"]["weighted_total_used"] is False
    assert manifest["charge_policy"]["mode"] == "observe_only_not_an_optimization_axis"
    assert "Pareto_objective" in manifest["charge_policy"]["forbidden_uses"]
    assert manifest["scientific_boundaries"]["explicit_positive_charge_optimization_used"] is False


def test_v37_requires_verified_auxiliaries_without_claiming_effectiveness() -> None:
    manifest = _load()
    auxiliaries = manifest["verified_auxiliaries"]

    assert auxiliaries["knowledge"]["required"] is True
    assert auxiliaries["knowledge"]["provider_task_id"] == ("019fad3e-76b8-7e32-8455-d2e9b31d33e5")
    assert auxiliaries["knowledge"]["positive_support_is_not_a_selection_score"] is True
    assert auxiliaries["pepshot"]["required_for_every_structural_shortlist_candidate"] is True
    assert auxiliaries["pepshot"]["provider_task_id"] == ("019fb910-f2dd-7be1-a7e6-bfe381512c25")
    assert auxiliaries["pepshot"]["candidate_revision_or_extra_generation_forbidden"] is True
    assert auxiliaries["provider_failure_policy"]["fail_closed_without_consumer_adaptation"] is True
    assert auxiliaries["effectiveness_claim_allowed"] is False


def test_v37_requires_database_object_replay_and_preserves_scientific_boundaries() -> None:
    manifest = _load()
    evidence = manifest["database_evidence_contract"]
    boundaries = manifest["scientific_boundaries"]

    assert evidence["PostgreSQL_is_authoritative"] is True
    assert evidence["object_store_is_content_addressed"] is True
    assert evidence["database_object_store_only_replay_required"] is True
    knowledge_key = "persist_knowledge_query_pack_trace_cards_passages_policy_and_adoption_edges"
    assert evidence[knowledge_key] is True
    assert (
        evidence[
            "persist_PepShot_request_bundle_images_read_order_review_validation_and_decision_edges"
        ]
        is True
    )
    assert evidence["CSV_JSON_and_Markdown_are_exports_only"] is True
    assert boundaries["predictions_are_not_experiments"] is True
    assert boundaries["no_AceA_binding_affinity_or_selectivity_claim"] is True
    assert boundaries["PepMLM_used"] is False
    assert boundaries["AMPlify_used"] is False
    assert boundaries["v22_through_v36_backwrite_forbidden"] is True


def test_v37_balanced_risk_lane_requires_two_low_risk_soft_labels() -> None:
    manifest = _load()
    lanes = {lane["name"]: lane for lane in manifest["final_portfolio"]["lanes"]}
    assert lanes["balanced_risk"]["required_soft_labels"] == {
        "macrel_hemolysis_label": "low",
        "toxinpred3_label": "Non-Toxin",
    }
    assert "experimental_safety" in lanes["balanced_risk"]["interpretation"]
