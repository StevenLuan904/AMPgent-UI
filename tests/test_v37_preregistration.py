from pathlib import Path

import pytest
import yaml

from pepagent.v37_preregistration import V37FormalRun

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_rapid_champion_generation_v37.yaml"


def _load() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_v37_is_single_arm_fixed_budget_and_execution_authorized() -> None:
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
        "execution_authorized": True,
        "submitted": False,
        "implementation_revision": "723823b5e64b37233fc2f41b8803b596c5039111",
        "run_id": None,
        "workflow_id": None,
    }


def test_v37_formal_run_accepts_only_the_three_monotonic_states() -> None:
    revision = "a" * 40
    unauthorized = V37FormalRun(
        direction_authorized=True,
        execution_authorized=False,
        submitted=False,
        implementation_revision=None,
        run_id=None,
        workflow_id=None,
    )
    authorized = V37FormalRun(
        direction_authorized=True,
        execution_authorized=True,
        submitted=False,
        implementation_revision=revision,
        run_id=None,
        workflow_id=None,
    )
    submitted = V37FormalRun(
        direction_authorized=True,
        execution_authorized=True,
        submitted=True,
        implementation_revision=revision,
        run_id="run-1",
        workflow_id="workflow-1",
    )
    assert unauthorized.implementation_revision is None
    assert authorized.implementation_revision == revision
    assert (submitted.run_id, submitted.workflow_id) == ("run-1", "workflow-1")


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"implementation_revision": "a" * 40}, "must be pristine"),
        ({"submitted": True}, "must be pristine"),
        (
            {"execution_authorized": True},
            "requires a frozen revision",
        ),
        (
            {
                "execution_authorized": True,
                "implementation_revision": "a" * 40,
                "run_id": "premature",
            },
            "cannot have run identities",
        ),
        (
            {
                "execution_authorized": True,
                "submitted": True,
                "implementation_revision": "a" * 40,
            },
            "requires run_id",
        ),
        (
            {
                "execution_authorized": True,
                "submitted": True,
                "implementation_revision": "a" * 40,
                "run_id": "run-1",
            },
            "requires workflow_id",
        ),
    ],
)
def test_v37_formal_run_rejects_cross_state_fields(
    overrides: dict[str, object], match: str
) -> None:
    payload = {
        "direction_authorized": True,
        "execution_authorized": False,
        "submitted": False,
        "implementation_revision": None,
        "run_id": None,
        "workflow_id": None,
        **overrides,
    }
    with pytest.raises(ValueError, match=match):
        V37FormalRun.model_validate(payload)


def test_v37_formal_run_rejects_non_commit_revision() -> None:
    with pytest.raises(ValueError, match="String should match pattern"):
        V37FormalRun.model_validate(
            {
                "direction_authorized": True,
                "execution_authorized": True,
                "submitted": False,
                "implementation_revision": "not-a-commit",
                "run_id": None,
                "workflow_id": None,
            }
        )


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


def test_v37_freezes_five_metric_calls_and_eleven_observations() -> None:
    manifest = _load()
    stage = manifest["stage_1_sequence_evaluation"]
    plugins = stage["metric_plugins"]
    assert [item["name"] for item in plugins] == [
        "physicochemical_developability",
        "hemolysis_risk",
        "mic_potency",
        "mic_potency_amp_read",
        "toxicity_risk",
    ]
    observations = [name for item in plugins for name in item["observation_names"]]
    assert set(observations) == set(stage["required_metric_names"])
    assert len(observations) == len(set(observations)) == 11
    assert manifest["execution"] == {
        "capacity_contract_path": "../experiments/acea_v37_rapid_champion_capacity.yaml",
        "capacity_contract_sha256": (
            "34f83c5a6df92a1d07779014c407211daefc80210581a840b7cea19cea46c3f0"
        ),
        "task_queues": {
            "workflow_and_control": "pepagent-control-v37",
            "generator": "pepagent-generator-v37",
            "provider": "pepagent-provider-v37",
            "sequence_metrics": "pepagent-cpu-metrics",
            "boltz": "pepagent-gpu-boltz2",
            "rosetta": "pepagent-cpu-rosetta",
        },
        "generation_concurrency": 8,
        "metric_concurrency": 5,
        "boltz_concurrency": 3,
        "rosetta_concurrency": 16,
        "ordered_collection_key": "source_ordinal",
    }


def test_v37_requires_verified_auxiliaries_without_claiming_effectiveness() -> None:
    manifest = _load()
    auxiliaries = manifest["verified_auxiliaries"]

    assert auxiliaries["knowledge"]["required"] is True
    assert auxiliaries["knowledge"]["provider_task_id"] == ("019fad3e-76b8-7e32-8455-d2e9b31d33e5")
    assert auxiliaries["knowledge"]["positive_support_is_not_a_selection_score"] is True
    assert auxiliaries["knowledge"]["query_path"] == (
        "../experiments/acea_v37_knowledge_query.json"
    )
    assert auxiliaries["knowledge"]["query_sha256"] == (
        "53e133ec3079681e66420c24789b76be42f8cc33a74e123b979e8fe8a838df44"
    )
    assert auxiliaries["pepshot"]["required_for_every_structural_shortlist_candidate"] is True
    assert auxiliaries["pepshot"]["provider_task_id"] == ("019fb910-f2dd-7be1-a7e6-bfe381512c25")
    assert auxiliaries["pepshot"]["candidate_revision_or_extra_generation_forbidden"] is True
    assert auxiliaries["pepshot"]["required_route"] == "deterministic_inspect"
    assert auxiliaries["pepshot"]["fallback_allowed"] is False
    structure = manifest["stage_2_structure_confirmation"]
    assert structure["PepShot_pose_selection"]["inspected_poses_per_candidate"] == 1
    assert structure["structural_eligibility"]["PepShot_inspect_verdict_required"] == "PASS"
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
            "persist_PepShot_inspect_request_contract_receipt_structure_SHA_findings_and_decision_edges"
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
