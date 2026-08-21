from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.resistance-propensity-contract.1"


def acceptance_artifacts(contract: Mapping[str, Any]) -> dict[str, str]:
    return {
        "serial_passage_organism_drug_and_control_matrix_sha256": sha256_json(
            contract.get("serial_passage_matrix")
        ),
        "raw_MIC_population_and_timecourse_measurement_schema_sha256": sha256_json(
            contract.get("raw_measurement_schema")
        ),
        "cross_resistance_and_genotype_endpoint_contract_sha256": sha256_json(
            contract.get("cross_resistance_and_genotype_contract")
        ),
        "leakage_safe_model_card_and_candidate_blinding_plan_sha256": sha256_json(
            {
                "reference_and_candidate_blinding_plan": contract.get(
                    "reference_and_candidate_blinding_plan"
                ),
                "split_contract": contract.get("split_contract"),
                "model_card_contract": contract.get("model_card_contract"),
            }
        ),
    }


def validate_resistance_propensity_contract(
    contract: Mapping[str, Any],
) -> dict[str, str]:
    """Validate a prospective evolution contract without claiming resistance data."""

    if contract.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("resistance-propensity contract schema is invalid")
    sources = contract.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 5:
        raise ValueError("resistance-propensity contract lacks primary sources")
    if any(not str(source).startswith("https://") for source in sources):
        raise ValueError("resistance-propensity source identity is invalid")

    matrix = contract.get("serial_passage_matrix")
    if not isinstance(matrix, Mapping):
        raise ValueError("serial-passage matrix is missing")
    organisms = matrix.get("organisms")
    if not isinstance(organisms, list):
        raise ValueError("serial-passage organisms are missing")
    identities = {item.get("organism_strain") for item in organisms if isinstance(item, Mapping)}
    if identities != {
        "Escherichia_coli_ATCC_25922",
        "Staphylococcus_epidermidis_ATCC_35984_RP62A",
    }:
        raise ValueError("serial-passage organism identities drifted")
    expected = {
        "independent_selected_lineages_per_candidate_organism": 8,
        "independent_no_drug_control_lineages_per_organism": 4,
        "passage_interval_hours": 24,
        "total_passages": 30,
        "transfer_fraction": 0.01,
        "archive_every_n_passages": 5,
        "MIC_remeasurement_every_n_passages": 5,
    }
    for key, value in expected.items():
        if matrix.get(key) != value:
            raise ValueError(f"serial-passage condition drifted: {key}")
    if matrix.get("exposure_ladder_relative_to_ancestral_MIC") != [
        0,
        0.125,
        0.25,
        0.5,
        1,
        2,
        4,
        8,
        16,
        32,
    ]:
        raise ValueError("serial-passage exposure ladder drifted")
    if matrix.get("transfer_source_rule") != (
        "highest_concentration_with_at_least_20_percent_growth_relative_to_no_drug_control"
    ):
        raise ValueError("serial-passage transfer rule drifted")
    if matrix.get("no_growth_is_recorded_as_population_extinction") is not True:
        raise ValueError("population extinction could be discarded")
    if matrix.get("extinct_lineages_may_be_resurrected_for_primary_analysis") is not False:
        raise ValueError("extinct lineages could be silently resurrected")
    if matrix.get("post_selection_drug_free_passages") != 5:
        raise ValueError("resistance stability challenge drifted")

    raw = contract.get("raw_measurement_schema")
    if not isinstance(raw, Mapping):
        raise ValueError("resistance raw schema is missing")
    required_fields = {
        "candidate_or_control_blind_id",
        "organism_strain",
        "ancestor_stock_sha256",
        "lineage_id",
        "passage_number",
        "exposure_concentration_um",
        "growth_OD600_raw",
        "no_drug_control_OD600_raw",
        "transfer_source_well",
        "transfer_volume_and_fraction",
        "viable_count_CFU_ml_when_scheduled",
        "MIC_dilution_series_raw",
        "MIC_endpoint_um",
        "MIC_fold_change_vs_ancestor",
        "population_status",
        "archive_artifact_sha256",
        "contamination_QC_status",
        "qc_reason_codes",
    }
    if not required_fields.issubset(set(raw.get("required_observation_fields", []))):
        raise ValueError("resistance observation context is incomplete")
    if raw.get("raw_values_are_immutable") is not True:
        raise ValueError("raw resistance values are not immutable")
    if raw.get("MIC_shift_and_population_extinction_stored_separately") is not True:
        raise ValueError("MIC shift and extinction were collapsed")
    if raw.get("failed_runs_and_retests_both_retained") is not True:
        raise ValueError("failed evolution runs would be lost")
    censoring = raw.get("censoring_rules")
    if not isinstance(censoring, Mapping):
        raise ValueError("resistance censoring rules are missing")
    if censoring.get("MIC_above_highest_tested") != "right_censored_above_max_tested_um":
        raise ValueError("high MIC censoring drifted")
    if censoring.get("lineage_extinction") != "competing_outcome_not_zero_resistance":
        raise ValueError("lineage extinction would be misclassified")

    cross = contract.get("cross_resistance_and_genotype_contract")
    if not isinstance(cross, Mapping):
        raise ValueError("cross-resistance/genotype contract is missing")
    required_agents = set(cross.get("cross_resistance_agents", []))
    if not {
        "LL_37",
        "colistin",
        "ciprofloxacin",
        "cefoxitin",
        "vancomycin",
        "daptomycin",
    }.issubset(required_agents):
        raise ValueError("cross-resistance agent panel is incomplete")
    if cross.get("species_inapplicable_agents_reported_as_not_applicable") is not True:
        raise ValueError("species-inapplicable cross-resistance could be pooled")
    if cross.get("ancestor_population_and_clone_WGS_required") is not True:
        raise ValueError("genotype lineage evidence is incomplete")
    if cross.get("variant_alone_proves_causality") is not False:
        raise ValueError("genotype could be overinterpreted")

    blinding = contract.get("reference_and_candidate_blinding_plan")
    if not isinstance(blinding, Mapping):
        raise ValueError("resistance blinding plan is missing")
    if blinding.get("current_773_candidates_excluded_from_model_fitting") is not True:
        raise ValueError("current candidates could influence fitting")
    if blinding.get("current_773_candidates_reserved_for_blind_test_only") is not True:
        raise ValueError("current candidates are not reserved for blind testing")
    if blinding.get("candidate_identity_unblinded_before_protocol_and_model_lock") is not False:
        raise ValueError("candidate identity could be unblinded early")
    rights = blinding.get("required_data_rights")
    if not isinstance(rights, Mapping) or not all(rights.values()):
        raise ValueError("resistance data and derivative-model rights are incomplete")

    split = contract.get("split_contract")
    if not isinstance(split, Mapping):
        raise ValueError("resistance split contract is missing")
    if split.get("exact_sequence_groups_disjoint") is not True:
        raise ValueError("exact sequence leakage is not forbidden")
    if split.get("design_campaign_groups_disjoint") is not True:
        raise ValueError("design campaign leakage is not forbidden")
    if float(split.get("maximum_cross_split_global_identity", 1.0)) > 0.4:
        raise ValueError("cross-split sequence identity is too permissive")
    if split.get("lineages_from_one_ancestor_may_cross_partitions") is not False:
        raise ValueError("evolution lineage leakage is not forbidden")
    if split.get("current_candidates_used_to_define_operating_point") is not False:
        raise ValueError("current candidates could define the operating point")

    model = contract.get("model_card_contract")
    if not isinstance(model, Mapping):
        raise ValueError("resistance model-card contract is missing")
    if model.get("primary_endpoint") != "organism_conditioned_resistance_trajectory":
        raise ValueError("resistance endpoint was collapsed")
    if model.get("single_global_low_high_resistance_label_allowed") is not False:
        raise ValueError("conditioned resistance was collapsed to one label")
    if model.get("competing_outcome_modeling_required") is not True:
        raise ValueError("population extinction is not modeled")
    if model.get("content_addressed_runtime_required") is not True:
        raise ValueError("resistance runtime is not reproducible")

    decision = contract.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("resistance contract decision is missing")
    forbidden_true = (
        "measurements_collected",
        "model_trained",
        "candidate_operating_point_created",
        "formal_science_run_submitted",
    )
    if any(decision.get(field) is not False for field in forbidden_true):
        raise ValueError("resistance acquisition contract overclaims completion")

    computed = acceptance_artifacts(contract)
    if contract.get("acceptance_artifacts") != computed:
        raise ValueError("resistance acceptance hashes drifted")
    return computed
