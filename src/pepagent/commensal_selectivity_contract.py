from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.commensal-selectivity-assay-model-contract.1"


def acceptance_artifacts(contract: Mapping[str, Any]) -> dict[str, str]:
    return {
        "pathogen_commensal_strain_and_condition_matrix_sha256": sha256_json(
            contract.get("strain_and_condition_matrix")
        ),
        "raw_growth_kill_and_selectivity_measurement_schema_sha256": sha256_json(
            contract.get("raw_measurement_schema")
        ),
        "leakage_safe_reference_and_candidate_blinding_plan_sha256": sha256_json(
            contract.get("reference_and_candidate_blinding_plan")
        ),
        "commensal_selectivity_model_card_and_qualification_contract_sha256": (
            sha256_json(
                {
                    "split_contract": contract.get("split_contract"),
                    "model_card_contract": contract.get("model_card_contract"),
                }
            )
        ),
    }


def validate_commensal_selectivity_contract(
    contract: Mapping[str, Any],
) -> dict[str, str]:
    """Validate a prospective strain-conditioned selectivity contract."""

    if contract.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("commensal-selectivity contract schema is invalid")
    sources = contract.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 9:
        raise ValueError("commensal-selectivity contract lacks primary sources")
    if any(not str(source).startswith("https://") for source in sources):
        raise ValueError("commensal-selectivity source identity is invalid")

    matrix = contract.get("strain_and_condition_matrix")
    if not isinstance(matrix, Mapping):
        raise ValueError("strain and condition matrix is missing")
    strains = matrix.get("strain_roles")
    if not isinstance(strains, list):
        raise ValueError("strain roles are missing")
    identities = {
        item.get("strain_identity") for item in strains if isinstance(item, Mapping)
    }
    if identities != {
        "Staphylococcus_aureus_ATCC_29213",
        "Staphylococcus_aureus_ATCC_43300_MRSA",
        "Escherichia_coli_ATCC_25922",
        "Staphylococcus_epidermidis_ATCC_35984_RP62A",
        "Staphylococcus_epidermidis_ATCC_12228",
        "Staphylococcus_hominis_ATCC_27844_DM122",
        "Cutibacterium_acnes_ATCC_6919_NCTC_737",
    }:
        raise ValueError("commensal-selectivity strain identities drifted")
    roles = {item.get("strain_identity"): item.get("evidence_role") for item in strains}
    if roles.get("Staphylococcus_epidermidis_ATCC_35984_RP62A") != (
        "opportunistic_infection_and_biofilm_pathogen_context"
    ):
        raise ValueError("S. epidermidis infection strain role drifted")
    if roles.get("Staphylococcus_epidermidis_ATCC_12228") != (
        "lower_virulence_non_biofilm_carriage_comparator"
    ):
        raise ValueError("S. epidermidis carriage comparator role drifted")
    if matrix.get("species_name_alone_defines_pathogen_or_commensal_role") is not False:
        raise ValueError("species-wide pathogen or commensal labels are allowed")
    if matrix.get("strain_role_is_contextual_not_universal_benefit_claim") is not True:
        raise ValueError("strain roles could be overgeneralized")
    if matrix.get("minimum_independent_experiment_days") != 2:
        raise ValueError("selectivity experiment-day replication drifted")
    if matrix.get("minimum_biological_replicates_per_strain_condition") != 3:
        raise ValueError("selectivity biological replication drifted")
    if matrix.get("technical_replicates_per_biological_replicate") != 2:
        raise ValueError("selectivity technical replication drifted")
    if matrix.get("peptide_concentration_um") != [
        0.5,
        1,
        2,
        4,
        8,
        16,
        32,
        64,
        128,
        256,
    ]:
        raise ValueError("selectivity concentration ladder drifted")
    if matrix.get("absolute_concentration_time_kill_hours") != [0, 1, 2, 4, 8, 24]:
        raise ValueError("selectivity time-kill schedule drifted")
    if matrix.get("absolute_concentration_time_kill_um") != [1, 4, 16, 64]:
        raise ValueError("selectivity time-kill concentration schedule drifted")
    lanes = matrix.get("assay_lanes")
    if not isinstance(lanes, list) or {lane.get("lane_id") for lane in lanes} != {
        "aerobic_reference_MIC_lane",
        "anaerobic_C_acnes_context_lane",
        "common_skin_mimetic_bridge_lane",
    }:
        raise ValueError("selectivity assay lanes drifted")
    if matrix.get("cross_medium_MIC_ratio_or_pooling_allowed") is not False:
        raise ValueError("incomparable media could be pooled")
    if matrix.get("biofilm_endpoint_pooled_with_planktonic_endpoint") is not False:
        raise ValueError("biofilm and planktonic endpoints could be collapsed")
    controls = set(matrix.get("required_controls", []))
    if not {
        "growth_control_each_strain_condition",
        "sterility_control_each_medium",
        "vehicle_control",
        "peptide_free_matrix_control",
        "peptide_recovery_and_adsorption_control",
        "mupirocin_reference",
        "chlorhexidine_reference",
        "LL_37_reference_peptide",
        "species_appropriate_antibiotic_QC_control",
    }.issubset(controls):
        raise ValueError("selectivity controls are incomplete")

    raw = contract.get("raw_measurement_schema")
    if not isinstance(raw, Mapping):
        raise ValueError("selectivity raw schema is missing")
    required_fields = {
        "candidate_or_control_blind_id",
        "exact_sequence_and_modification_sha256",
        "strain_identity_genome_and_master_stock_sha256",
        "evidence_role",
        "assay_lane_medium_lot_pH_atmosphere_temperature",
        "inoculum_CFU_per_mL_and_preparation",
        "experiment_day_plate_well_and_randomization",
        "peptide_concentration_um_and_exposure_time_hours",
        "raw_OD_or_growth_signal",
        "raw_colony_counts_dilutions_plating_volume_and_LOD",
        "MIC_interval_and_censoring",
        "MBC_interval_and_censoring",
        "biofilm_readout_when_scheduled",
        "peptide_recovery_fraction_when_scheduled",
        "control_raw_values",
        "QC_status_and_reason_codes",
    }
    if not required_fields.issubset(set(raw.get("required_observation_fields", []))):
        raise ValueError("selectivity observation context is incomplete")
    if raw.get("raw_values_are_immutable") is not True:
        raise ValueError("raw selectivity values are not immutable")
    if raw.get("failed_runs_and_assignable_cause_retests_both_retained") is not True:
        raise ValueError("failed selectivity runs could be discarded")
    if raw.get("pathogen_potency_and_resident_preservation_stored_separately") is not True:
        raise ValueError("potency and preservation could be collapsed")
    if raw.get("single_weighted_selectivity_score_allowed") is not False:
        raise ValueError("a weighted selectivity score is allowed")
    if raw.get("no_growth_or_no_kill_may_be_imputed_as_exact_value") is not False:
        raise ValueError("censored selectivity observations could be imputed")
    censoring = raw.get("censoring_rules")
    if not isinstance(censoring, Mapping):
        raise ValueError("selectivity censoring rules are missing")
    if censoring.get("MIC_above_maximum_tested") != "right_censored_above_256_um":
        raise ValueError("high MIC censoring drifted")
    if censoring.get("MIC_at_or_below_minimum_tested") != (
        "left_censored_at_or_below_0_5_um"
    ):
        raise ValueError("low MIC censoring drifted")
    if censoring.get("CFU_below_quantification") != (
        "interval_censored_between_zero_and_assay_LOQ_not_zero"
    ):
        raise ValueError("CFU censoring could be interpreted as sterile")

    blinding = contract.get("reference_and_candidate_blinding_plan")
    if not isinstance(blinding, Mapping):
        raise ValueError("selectivity blinding plan is missing")
    required_false = (
        "current_773_candidates_used_for_training",
        "current_773_candidates_used_for_calibration",
        "current_773_candidates_used_for_model_selection",
        "current_773_candidates_used_for_operating_point",
        "candidate_identity_unblinded_before_protocol_assay_and_model_lock",
    )
    if any(blinding.get(field) is not False for field in required_false):
        raise ValueError("current candidates could influence selectivity qualification")
    if blinding.get("current_773_candidates_reserved_for_blind_test_only") is not True:
        raise ValueError("current candidates are not reserved for blind testing")
    rights = blinding.get("required_data_and_strain_rights")
    if not isinstance(rights, Mapping) or not all(rights.values()):
        raise ValueError("selectivity data, strain or derivative-model rights are incomplete")

    split = contract.get("split_contract")
    if not isinstance(split, Mapping):
        raise ValueError("selectivity split contract is missing")
    if split.get("exact_sequence_groups_disjoint") is not True:
        raise ValueError("exact sequence leakage is not forbidden")
    if split.get("publication_database_and_design_campaign_groups_disjoint") is not True:
        raise ValueError("publication/database/design-campaign leakage is not forbidden")
    if float(split.get("maximum_cross_split_global_identity", 1.0)) > 0.4:
        raise ValueError("selectivity cross-split identity is too permissive")
    if split.get("all_strains_and_conditions_for_one_sequence_stay_in_one_partition") is not True:
        raise ValueError("one sequence could leak across condition partitions")
    if split.get("current_candidates_used_to_define_operating_point") is not False:
        raise ValueError("current candidates could define the selectivity operating point")

    model = contract.get("model_card_contract")
    if not isinstance(model, Mapping):
        raise ValueError("selectivity model-card contract is missing")
    if model.get("primary_endpoint") != (
        "strain_and_condition_specific_pathogen_potency_and_skin_resident_preservation"
    ):
        raise ValueError("selectivity endpoint was collapsed")
    if model.get("global_species_pathogen_or_commensal_label_allowed") is not False:
        raise ValueError("global species roles could be learned")
    if model.get("single_weighted_selectivity_score_allowed") is not False:
        raise ValueError("model could collapse selectivity to one score")
    if model.get("common_condition_only_selectivity_join_required") is not True:
        raise ValueError("model could compare incompatible assay conditions")
    if model.get("content_addressed_runtime_required") is not True:
        raise ValueError("selectivity runtime is not reproducible")
    if model.get("primary_decision_use") != "non_weighted_Pareto_with_interval_uncertainty":
        raise ValueError("selectivity decision use drifted")

    decision = contract.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("selectivity contract decision is missing")
    forbidden_true = (
        "measurements_collected",
        "model_trained",
        "candidate_operating_point_created",
        "formal_science_run_submitted",
    )
    if any(decision.get(field) is not False for field in forbidden_true):
        raise ValueError("selectivity contract overclaims completion")

    computed = acceptance_artifacts(contract)
    if contract.get("acceptance_artifacts") != computed:
        raise ValueError("selectivity acceptance hashes drifted")
    return computed
