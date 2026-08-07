from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pepagent.developability import sequence_developability_metrics
from pepagent.domain.schemas import ExperimentSpec
from pepagent.selection import (
    cheap_diverse_selection,
    diagnostic_representative_selection,
    diversity_constrained_elites,
    hard_qualification_violations,
    progressive_evaluation_plan,
    qualification_violations,
    research_iteration_directive,
    sequence_distance,
)
from pepagent.structures.interface import (
    audit_protein_peptide_interface,
    classify_structure_support,
    pose_cluster_fraction,
    reconcile_ensemble_structure_support,
)


def test_progressive_evaluation_ladder_holds_expensive_tiers_until_justified() -> None:
    manual = progressive_evaluation_plan(
        {
            "evaluation_ladder_mode": "lightweight_first",
            "structure_escalation_policy": "manual",
            "rosetta_escalation_policy": "manual",
            "rosetta_enabled": False,
        },
        generation=5,
        final_generation=True,
        qualified_elite_count=4,
    )
    assert manual["run_structure"] is False
    assert manual["run_rosetta"] is False

    agent = progressive_evaluation_plan(
        {
            "evaluation_ladder_mode": "lightweight_first",
            "structure_escalation_policy": "agent_triggered",
            "structure_start_generation": 2,
            "structure_trigger_min_qualified_elites": 2,
            "rosetta_escalation_policy": "final_elite",
            "rosetta_start_generation": 3,
            "rosetta_enabled": True,
        },
        generation=2,
        final_generation=False,
        qualified_elite_count=2,
    )
    assert agent["run_structure"] is True
    assert agent["run_rosetta"] is False


def test_persistent_harness_advances_an_imperfect_final_frontier() -> None:
    directive = research_iteration_directive(
        {
            "research_iteration_policy": "evidence_driven_continue",
            "imperfect_frontier_action": "escalate_representatives",
        },
        final_generation=True,
        selected_count=4,
        qualified_elite_count=0,
    )
    assert directive["continuation_required"] is True
    assert directive["versioned_continuation_required"] is True
    assert directive["advance_imperfect_frontier"] is True
    assert directive["next_action"] == "escalate_representatives"
    assert directive["preserve_negative_evidence"] is True


def test_legacy_harness_remains_budget_terminal() -> None:
    directive = research_iteration_directive(
        {}, final_generation=True, selected_count=4, qualified_elite_count=0
    )
    assert directive["continuation_required"] is False
    assert directive["next_action"] == "no_qualified_hit"


def test_acea_v11_is_lightweight_first_and_uses_multiple_soft_amp_signals() -> None:
    spec_path = (
        Path(__file__).parents[1]
        / "config"
        / "experiments"
        / "acea_autoresearch_v11_lightweight.yaml"
    )
    spec = ExperimentSpec.model_validate(yaml.safe_load(spec_path.read_text(encoding="utf-8")))
    rules = {rule.metric_name: rule for rule in spec.metric_policy}
    assert spec.evaluation_ladder_mode == "lightweight_first"
    assert spec.structure_escalation_policy == "manual"
    assert spec.rosetta_escalation_policy == "manual"
    assert spec.bulk_rosetta_all_qualified is False
    for metric_name in (
        "macrel_amp_probability",
        "amplify_probability",
        "llamp_log10_mic_um",
        "amp_read_log10_mic_um",
        "toxinpred3_ml_score",
        "macrel_hemolysis_probability",
    ):
        assert rules[metric_name].role == "qualification"
        assert rules[metric_name].hard is False


def test_acea_v12_structure_validation_is_persistent_and_bounded() -> None:
    spec_path = (
        Path(__file__).parents[1]
        / "config"
        / "experiments"
        / "acea_structure_validation_v12.yaml"
    )
    spec = ExperimentSpec.model_validate(yaml.safe_load(spec_path.read_text(encoding="utf-8")))
    assert spec.research_iteration_policy == "evidence_driven_continue"
    assert spec.imperfect_frontier_action == "escalate_representatives"
    assert spec.structure_protocol == "diagnostic_fast"
    assert spec.boltz_seeds_per_candidate == 1
    assert spec.rosetta_enabled is True
    assert spec.rosetta_nstruct == 8
    assert spec.bulk_evaluation_concurrency == 4


def _atom(serial: int, atom: str, residue: str, chain: str, index: int, x: float) -> str:
    return (
        f"ATOM  {serial:5d} {atom:^4s} {residue:>3s} {chain}{index:4d}    "
        f"{x:8.3f}{0.0:8.3f}{0.0:8.3f}{1.0:6.2f}{20.0:6.2f}          C"
    )


def _interface_pdb(path: Path) -> None:
    lines = [
        _atom(1, "CA", "ALA", "A", 1, 0.0),
        _atom(2, "CA", "GLY", "A", 2, 4.0),
        _atom(3, "CA", "SER", "A", 3, 20.0),
        _atom(4, "CA", "LYS", "B", 1, 2.0),
        _atom(5, "N", "LYS", "B", 1, 2.5),
        _atom(6, "C", "LYS", "B", 1, 3.0),
        _atom(7, "O", "LYS", "B", 1, 3.5),
        "END",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def test_diversity_constrained_selection_keeps_distinct_sequences() -> None:
    proposals = [
        {"sequence": "AAAAAAAAAA", "conditional_ppl": 1.0},
        {"sequence": "AAAAAAAATA", "conditional_ppl": 1.1},
        {"sequence": "RRRRRRRRRR", "conditional_ppl": 2.0},
    ]
    selected = cheap_diverse_selection(proposals, 2, maximum_similarity=0.8)
    assert [item["sequence"] for item in selected] == ["AAAAAAAAAA", "RRRRRRRRRR"]
    assert sequence_distance("AAAA", "AAAT") == 1


def test_elite_order_requires_structure_gate_before_rosetta() -> None:
    candidates = [
        {
            "id": "failed-gate",
            "sequence": "AAAAAAAAAA",
            "metrics": {
                "interface_gate_pass": 0,
                "rosetta_dg_separated_reu": -100,
                "conditional_ppl": 1,
            },
        },
        {
            "id": "passed-gate",
            "sequence": "RRRRRRRRRR",
            "metrics": {
                "interface_gate_pass": 1,
                "pocket_contact_consistency": 0.8,
                "boltz2_pair_iptm_median": 0.3,
                "conditional_ppl": 5,
            },
        },
    ]
    assert diversity_constrained_elites(candidates, 1, 0.8)[0]["id"] == "passed-gate"


def test_unfavorable_rosetta_result_does_not_receive_an_evidence_presence_bonus() -> None:
    candidates = [
        {
            "id": "expensive-negative-result",
            "sequence": "AAAAAAAAAA",
            "metrics": {
                "interface_gate_pass": 0,
                "rosetta_dg_separated_reu": 17.4,
                "pocket_contact_consistency": 1.0,
                "boltz2_pair_iptm_median": 0.20,
                "conditional_ppl": 4.7,
            },
        },
        {
            "id": "stronger-structure-without-rosetta",
            "sequence": "RRRRRRRRRR",
            "metrics": {
                "interface_gate_pass": 0,
                "pocket_contact_consistency": 1.0,
                "boltz2_pair_iptm_median": 0.24,
                "conditional_ppl": 5.0,
            },
        },
    ]
    selected = diversity_constrained_elites(candidates, 1, 0.8)
    assert selected[0]["id"] == "stronger-structure-without-rosetta"


def test_polyvaline_sequence_fails_non_compensatory_developability_rules() -> None:
    metrics = sequence_developability_metrics("KSAVVVVVVNGA")
    assert metrics["instability_index"] == pytest.approx(-10.433333333333334)
    assert metrics["maximum_identical_residue_run"] == 6
    assert metrics["maximum_hydrophobic_run"] == 7
    assert metrics["hydrophobic_fraction"] == pytest.approx(8 / 12)
    candidate = {"sequence": "KSAVVVVVVNGA", "metrics": metrics}
    rules = [
        {
            "metric_name": "maximum_hydrophobic_run",
            "role": "qualification",
            "maximum": 4,
            "hard": True,
            "stages": ["proposal"],
        },
        {
            "metric_name": "hydrophobic_fraction",
            "role": "qualification",
            "maximum": 0.60,
            "hard": True,
            "stages": ["proposal"],
        },
    ]
    violations = qualification_violations(candidate, rules, "proposal")
    assert {item["metric_name"] for item in violations} == {
        "maximum_hydrophobic_run",
        "hydrophobic_fraction",
    }


def test_instability_index_matches_919_biopython_reference_and_is_a_qualification() -> None:
    metrics = sequence_developability_metrics("KASVNVSPRA")
    assert metrics["instability_index"] == pytest.approx(45.4)
    assert metrics["instability_method"].startswith("Guruprasad-Reddy-Pandit-1990")
    candidate = {"sequence": "KASVNVSPRA", "metrics": metrics}
    violations = qualification_violations(
        candidate,
        [
            {
                "metric_name": "instability_index",
                "role": "qualification",
                "maximum": 40.0,
                "hard": True,
                "stages": ["proposal"],
            }
        ],
        "proposal",
    )
    assert [item["metric_name"] for item in violations] == ["instability_index"]


def test_developability_rejects_noncanonical_residues_before_protparam() -> None:
    with pytest.raises(ValueError, match="non-canonical"):
        sequence_developability_metrics("KASX")


def test_canonical_amp_descriptors_are_recorded_in_the_proposal_lane() -> None:
    metrics = sequence_developability_metrics("KWKLFKKIGAVLKVL")
    assert metrics["molecular_weight_da"] == pytest.approx(1771.2821)
    assert metrics["net_charge_ph7_4"] == pytest.approx(4.5449249042)
    assert metrics["isoelectric_point"] == pytest.approx(10.6024866104)
    assert metrics["gravy"] == pytest.approx(0.54)
    assert metrics["hydrophobic_moment_eisenberg"] == pytest.approx(0.4139307319)
    assert metrics["cationic_residue_fraction"] == pytest.approx(1 / 3)
    assert "alpha-helical projection" in " ".join(metrics["limitations"])


def test_soft_amp_window_is_preferred_without_rejecting_a_candidate() -> None:
    policy = [
        {
            "metric_name": "net_charge_ph7_4",
            "role": "qualification",
            "minimum": 1.0,
            "hard": False,
            "missing_policy": "fail",
            "stages": ["proposal"],
        },
        {
            "metric_name": "conditional_ppl",
            "role": "objective",
            "direction": "minimize",
            "priority": 10,
            "stages": ["proposal"],
        },
    ]
    proposals = [
        {
            "sequence": "DDAAALLVVV",
            "conditional_ppl": 1.0,
            "metrics": {"net_charge_ph7_4": -2.0, "conditional_ppl": 1.0},
        },
        {
            "sequence": "KASVNVSPRA",
            "conditional_ppl": 6.0,
            "metrics": {"net_charge_ph7_4": 1.5, "conditional_ppl": 6.0},
        },
    ]
    selected = cheap_diverse_selection(proposals, 2, 1.0, policy)
    assert [item["sequence"] for item in selected] == [
        "KASVNVSPRA",
        "DDAAALLVVV",
    ]


def test_v10_uses_amp_descriptors_and_parallel_soft_predictors() -> None:
    config_path = (
        Path(__file__).parents[1] / "config" / "experiments" / "acea_autoresearch_v10.yaml"
    )
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    spec = ExperimentSpec.model_validate(payload)
    enabled = {metric.name for metric in spec.optional_metrics if metric.enabled}
    assert enabled == {
        "hemolysis_risk",
        "amp_likeness",
        "toxicity_risk",
        "mic_potency",
        "mic_potency_amp_read",
    }
    rules = {rule.metric_name: rule for rule in spec.metric_policy}
    assert rules["maximum_identical_residue_run"].hard is True
    assert rules["hydrophobic_fraction"].role == "diagnostic"
    assert rules["maximum_hydrophobic_run"].role == "diagnostic"
    assert rules["instability_index"].hard is False
    assert rules["net_charge_ph7_4"].hard is False
    assert rules["gravy"].hard is False
    assert rules["hydrophobic_moment_eisenberg"].hard is False


def test_8ahs_melittin_control_is_profiled_but_not_rejected() -> None:
    sequence = "GIGAVLKVLTTGLPALISWIKRKRQQ"
    metrics = sequence_developability_metrics(sequence)
    config_path = (
        Path(__file__).parents[1] / "config" / "experiments" / "acea_autoresearch_v10.yaml"
    )
    policy = yaml.safe_load(config_path.read_text(encoding="utf-8"))["metric_policy"]
    candidate = {"sequence": sequence, "metrics": metrics}
    assert metrics["net_charge_ph7_4"] == pytest.approx(4.5498858531)
    assert metrics["gravy"] == pytest.approx(0.2730769231)
    assert metrics["hydrophobic_moment_eisenberg"] == pytest.approx(0.3589698816)
    assert metrics["instability_index"] > 40.0
    assert hard_qualification_violations(candidate, policy, "proposal") == []


def test_proposal_gate_rejects_polyvaline_despite_better_ppl() -> None:
    policy = [
        {
            "metric_name": "maximum_identical_residue_run",
            "role": "qualification",
            "maximum": 4,
            "hard": True,
            "stages": ["proposal"],
        },
        {
            "metric_name": "conditional_ppl",
            "role": "objective",
            "direction": "minimize",
            "priority": 10,
            "stages": ["proposal"],
        },
    ]
    proposals = [
        {
            "sequence": "KSAVVVVVVNGA",
            "conditional_ppl": 1.0,
            "metrics": {"maximum_identical_residue_run": 6, "conditional_ppl": 1.0},
        },
        {
            "sequence": "KASVNVSPRA",
            "conditional_ppl": 6.0,
            "metrics": {"maximum_identical_residue_run": 1, "conditional_ppl": 6.0},
        },
    ]
    selected = cheap_diverse_selection(proposals, 1, 0.8, policy)
    assert selected[0]["sequence"] == "KASVNVSPRA"


def test_qualification_cannot_be_compensated_by_better_objective() -> None:
    policy = [
        {
            "metric_name": "maximum_hydrophobic_run",
            "role": "qualification",
            "maximum": 4,
            "hard": True,
            "stages": ["research"],
        },
        {
            "metric_name": "affinity_proxy",
            "role": "objective",
            "direction": "minimize",
            "priority": 10,
            "stages": ["research"],
        },
    ]
    candidates = [
        {
            "id": "infeasible",
            "sequence": "VVVVVVAAAA",
            "metrics": {"maximum_hydrophobic_run": 6, "affinity_proxy": -100},
        },
        {
            "id": "feasible",
            "sequence": "KASVNVSPRA",
            "metrics": {"maximum_hydrophobic_run": 2, "affinity_proxy": -1},
        },
    ]
    selected = diversity_constrained_elites(candidates, 1, 0.8, policy)
    assert selected[0]["id"] == "feasible"


def test_coordinate_interface_audit_and_pose_consistency(tmp_path: Path) -> None:
    first = tmp_path / "first.pdb"
    second = tmp_path / "second.pdb"
    _interface_pdb(first)
    _interface_pdb(second)
    audit = audit_protein_peptide_interface(first, [1, 2])
    assert audit["pocket_contact_count"] == 2
    assert audit["pocket_coverage_fraction"] == 1.0
    assert audit["off_pocket_contact_fraction"] == 0.0
    consistency = pose_cluster_fraction([first, second], threshold_angstrom=0.1)
    assert consistency["largest_cluster_fraction"] == 1.0


def test_autoresearch_contract_rejects_single_seed() -> None:
    with pytest.raises(ValidationError, match="multiple independent Boltz seeds"):
        ExperimentSpec.model_validate(
            {
                "target": {
                    "name": "target",
                    "sequence": "ACDEFGHIKLMNPQRSTVWY",
                    "pocket_residues": [1],
                },
                "autoresearch_enabled": True,
                "generations": 3,
                "boltz_seeds_per_candidate": 1,
                "rosetta_enabled": True,
            }
        )


def test_fast_protocol_admits_one_seed_and_eight_shadow_decoys() -> None:
    spec = ExperimentSpec.model_validate(
        {
            "target": {
                "name": "target",
                "sequence": "ACDEFGHIKLMNPQRSTVWY",
                "pocket_residues": [1],
            },
            "autoresearch_enabled": True,
            "structure_protocol": "diagnostic_fast",
            "generations": 3,
            "boltz_seeds_per_candidate": 1,
            "rosetta_enabled": True,
            "rosetta_nstruct": 8,
        }
    )
    assert spec.structure_protocol == "diagnostic_fast"
    assert spec.rosetta_nstruct == 8


def test_bulk_rosetta_requires_autoresearch_and_caps_decoys() -> None:
    base = {
        "target": {
            "name": "target",
            "sequence": "ACDEFGHIKLMNPQRSTVWY",
            "pocket_residues": [1],
        },
        "structure_protocol": "diagnostic_fast",
        "boltz_seeds_per_candidate": 1,
        "generations": 4,
        "bulk_rosetta_all_qualified": True,
        "bulk_rosetta_candidate_limit": 250,
        "bulk_csv_report_threshold": 200,
    }
    with pytest.raises(ValidationError, match="requires Auto Research"):
        ExperimentSpec.model_validate(base)
    with pytest.raises(ValidationError, match="at most eight decoys"):
        ExperimentSpec.model_validate(
            {**base, "autoresearch_enabled": True, "rosetta_nstruct": 9}
        )
    spec = ExperimentSpec.model_validate(
        {**base, "autoresearch_enabled": True, "rosetta_nstruct": 8}
    )
    assert spec.bulk_rosetta_all_qualified is True
    assert spec.bulk_rosetta_candidate_limit == 250
    assert spec.bulk_csv_report_threshold == 200
    assert spec.bulk_evaluation_concurrency == 4


def test_fast_structure_support_distinguishes_weak_conflict_and_unavailable() -> None:
    weak = classify_structure_support(
        structure_available=True,
        pair_iptm=0.10,
        pocket_contact_count=0,
        clash_count=0,
        severe_clash_count=25,
        minimum_pair_iptm=0.15,
        minimum_pocket_contacts=2,
    )
    conflict = classify_structure_support(
        structure_available=True,
        pair_iptm=0.10,
        pocket_contact_count=0,
        clash_count=0,
        severe_clash_count=25,
        minimum_pair_iptm=0.15,
        minimum_pocket_contacts=2,
        rosetta_dg=-5.018,
    )
    unavailable = classify_structure_support(
        structure_available=False,
        pair_iptm=None,
        pocket_contact_count=None,
        clash_count=None,
        severe_clash_count=25,
        minimum_pair_iptm=0.15,
        minimum_pocket_contacts=2,
    )
    assert weak["label"] == "weak"
    assert conflict["label"] == "conflicting"
    assert unavailable["label"] == "unavailable"


def test_ensemble_gate_failure_overrides_positive_representative_label() -> None:
    support = reconcile_ensemble_structure_support(
        {"label": "positive", "reasons": ["single_pose_geometry_support"]},
        {
            "pocket_contact_consistency": True,
            "pair_iptm_median": True,
            "pose_cluster_fraction": False,
            "no_cross_chain_clash": True,
        },
    )
    assert support["label"] == "conflicting"
    assert "failed_pose_cluster_fraction" in support["reasons"]


def test_ensemble_gate_failure_records_failed_checks_for_weak_support() -> None:
    support = reconcile_ensemble_structure_support(
        {"label": "weak", "reasons": ["pair_iptm_below_threshold"]},
        {
            "pocket_contact_consistency": True,
            "pair_iptm_median": False,
            "pose_cluster_fraction": False,
            "no_cross_chain_clash": True,
        },
    )
    assert support["label"] == "conflicting"
    assert "pair_iptm_below_threshold" in support["reasons"]
    assert "failed_pair_iptm_median" in support["reasons"]
    assert "failed_pose_cluster_fraction" in support["reasons"]


def test_fast_search_representatives_mix_property_leaders_and_diversity() -> None:
    candidates = [
        {"sequence": "AAAAAAAAAA", "conditional_ppl": 1.0},
        {"sequence": "AAAAAAAATA", "conditional_ppl": 1.1},
        {"sequence": "RRRRRRRRRR", "conditional_ppl": 2.0},
        {"sequence": "GGGGGGGGGG", "conditional_ppl": 3.0},
    ]
    selected = diagnostic_representative_selection(candidates, 2, 2, 0.90)
    assert selected[0]["sequence"] == "AAAAAAAAAA"
    assert {item["sequence"] for item in selected} >= {"RRRRRRRRRR", "GGGGGGGGGG"}


def test_acea_v3_is_an_exploitation_biased_four_generation_run() -> None:
    spec_path = (
        Path(__file__).parents[1] / "config" / "experiments" / "acea_autoresearch_v3.yaml"
    )
    spec = ExperimentSpec.model_validate(yaml.safe_load(spec_path.read_text(encoding="utf-8")))
    assert spec.generations == 4
    assert spec.mutation_children_per_parent == 4
    assert spec.exploration_candidates_per_length == 1
    assert spec.boltz_force_pocket is True
    assert spec.interface_min_pose_cluster_fraction == pytest.approx(2 / 3)
    assert spec.rosetta_nstruct == 200


def test_acea_v4_declares_metric_roles_and_stability_qualification() -> None:
    spec_path = (
        Path(__file__).parents[1] / "config" / "experiments" / "acea_autoresearch_v4.yaml"
    )
    spec = ExperimentSpec.model_validate(yaml.safe_load(spec_path.read_text(encoding="utf-8")))
    roles = {rule.metric_name: rule.role for rule in spec.metric_policy}
    assert roles["maximum_hydrophobic_run"] == "qualification"
    assert roles["maximum_identical_residue_run"] == "qualification"
    assert roles["hydrophobic_fraction"] == "qualification"
    assert roles["rosetta_dg_separated_reu"] == "objective"
    assert roles["sequence_similarity"] == "diversity"


def test_acea_v5_adds_guruprasad_instability_as_a_hard_qualification() -> None:
    spec_path = (
        Path(__file__).parents[1] / "config" / "experiments" / "acea_autoresearch_v5.yaml"
    )
    spec = ExperimentSpec.model_validate(yaml.safe_load(spec_path.read_text(encoding="utf-8")))
    rules = {rule.metric_name: rule for rule in spec.metric_policy}
    instability = rules["instability_index"]
    assert instability.role == "qualification"
    assert instability.maximum == pytest.approx(40.0)
    assert instability.hard is True
    assert instability.stages == ["proposal", "research", "final"]
    assert spec.search_regime == "E1"
    assert spec.pepmlm_de_novo_top_k == 10
    assert spec.pepmlm_mutation_top_k == 10
    assert spec.pepmlm_temperature == pytest.approx(1.35)
    assert spec.mutation_count_max == 6
    assert spec.exploration_candidates_per_length == 3


def test_acea_v6_uses_fast_diagnostic_structure_budget() -> None:
    spec_path = (
        Path(__file__).parents[1] / "config" / "experiments" / "acea_autoresearch_v6.yaml"
    )
    spec = ExperimentSpec.model_validate(yaml.safe_load(spec_path.read_text(encoding="utf-8")))
    roles = {rule.metric_name: rule.role for rule in spec.metric_policy}
    assert spec.structure_protocol == "diagnostic_fast"
    assert spec.search_structure_comprehensive_count == 2
    assert spec.search_structure_diversity_count == 2
    assert spec.final_structure_candidate_count == 8
    assert spec.boltz_seeds_per_candidate == 1
    assert spec.rosetta_top_k == 3
    assert spec.rosetta_nstruct == 8
    assert "interface_gate_pass" not in roles
    assert roles["rosetta_dg_separated_reu"] == "diagnostic"


def test_acea_v8_uses_natural_search_budget_and_reporting_threshold() -> None:
    spec_path = (
        Path(__file__).parents[1] / "config" / "experiments" / "acea_autoresearch_v8.yaml"
    )
    spec = ExperimentSpec.model_validate(yaml.safe_load(spec_path.read_text(encoding="utf-8")))
    assert spec.candidates_per_length == 14
    assert spec.elite_parent_count == 4
    assert spec.mutation_children_per_parent == 5
    assert spec.exploration_candidates_per_length == 4
    assert spec.bulk_rosetta_all_qualified is True
    assert spec.bulk_rosetta_candidate_limit == 250
    assert spec.bulk_csv_report_threshold == 200
