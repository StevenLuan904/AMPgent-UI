from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.prospective-hemolysis-assay-contract.1"


def acceptance_artifacts(contract: Mapping[str, Any]) -> dict[str, str]:
    return {
        "rbc_species_donor_and_assay_condition_matrix_sha256": sha256_json(
            contract.get("rbc_species_donor_and_assay_condition_matrix")
        ),
        "prospective_sequence_acquisition_and_overlap_exclusion_plan_sha256": sha256_json(
            contract.get("sequence_acquisition_and_overlap_exclusion")
        ),
        "raw_measurement_and_censoring_schema_sha256": sha256_json(
            contract.get("raw_measurement_and_censoring_schema")
        ),
        "train_calibration_ood_split_and_model_card_contract_sha256": sha256_json(
            {
                "split_contract": contract.get("split_contract"),
                "model_card_contract": contract.get("model_card_contract"),
            }
        ),
    }


def validate_prospective_hemolysis_assay_contract(
    contract: Mapping[str, Any],
) -> dict[str, str]:
    """Validate an acquisition contract without claiming measurements or model fitness."""

    if contract.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("prospective hemolysis contract schema is invalid")
    sources = contract.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 3:
        raise ValueError("prospective hemolysis contract lacks primary sources")
    if any(not str(source).startswith("https://") for source in sources):
        raise ValueError("prospective hemolysis source identity is invalid")

    matrix = contract.get("rbc_species_donor_and_assay_condition_matrix")
    if not isinstance(matrix, Mapping):
        raise ValueError("RBC assay matrix is missing")
    primary = matrix.get("primary_matrix")
    if not isinstance(primary, Mapping):
        raise ValueError("primary RBC matrix is missing")
    if primary.get("species") != "Homo sapiens":
        raise ValueError("primary RBC matrix must be human")
    if primary.get("blood_preparation") != "defibrinated":
        raise ValueError("primary RBC matrix must be defibrinated")
    if int(primary.get("minimum_independent_donors", 0)) < 3:
        raise ValueError("primary RBC matrix requires at least three donors")
    if primary.get("donor_pooling_allowed") is not False:
        raise ValueError("donor pooling must be forbidden")
    if int(primary.get("minimum_independent_assay_days", 0)) < 2:
        raise ValueError("RBC assay must span at least two independent days")
    conditions = matrix.get("frozen_assay_conditions")
    if not isinstance(conditions, Mapping):
        raise ValueError("frozen RBC assay conditions are missing")
    expected_conditions = {
        "final_rbc_volume_percent": 2.0,
        "buffer": "PBS_pH_7.4",
        "temperature_c": 37,
        "incubation_minutes": 60,
        "technical_replicates_per_donor_concentration": 3,
        "readout": "supernatant_absorbance_540_to_541_nm",
    }
    for key, expected in expected_conditions.items():
        if conditions.get(key) != expected:
            raise ValueError(f"RBC assay condition drifted: {key}")
    if conditions.get("concentration_um") != [1, 2, 4, 8, 16, 32, 64, 128, 256]:
        raise ValueError("RBC concentration series drifted")
    controls = set(conditions.get("required_controls", []))
    if not {
        "PBS_negative",
        "vehicle_matched_negative",
        "Triton_X100_1pct_complete_lysis",
        "melittin_reference_curve",
        "peptide_without_RBC_interference_blank",
    }.issubset(controls):
        raise ValueError("RBC assay controls are incomplete")

    acquisition = contract.get("sequence_acquisition_and_overlap_exclusion")
    if not isinstance(acquisition, Mapping):
        raise ValueError("sequence acquisition plan is missing")
    if acquisition.get("prospective_measurements_only") is not True:
        raise ValueError("prospective measurements are not required")
    if acquisition.get("existing_database_measurements_forbidden") is not True:
        raise ValueError("existing database measurements were not excluded")
    if acquisition.get("current_ampgent_candidates_excluded_from_fitting") is not True:
        raise ValueError("current AMPgent candidates could influence fitting")
    if acquisition.get("current_ampgent_candidates_reserved_for_blind_test_only") is not True:
        raise ValueError("current AMPgent candidates are not reserved for blind testing")
    rights = acquisition.get("required_data_rights")
    if not isinstance(rights, Mapping) or not all(rights.values()):
        raise ValueError("commercial data and derivative-model rights are incomplete")

    raw = contract.get("raw_measurement_and_censoring_schema")
    if not isinstance(raw, Mapping):
        raise ValueError("raw hemolysis schema is missing")
    required_fields = set(raw.get("required_observation_fields", []))
    required = {
        "sequence_sha256",
        "donor_id_pseudonym",
        "blood_preparation",
        "assay_day",
        "plate_id",
        "well_id",
        "concentration_um",
        "technical_replicate",
        "raw_sample_absorbance",
        "raw_negative_absorbance",
        "raw_positive_absorbance",
        "raw_interference_blank_absorbance",
        "normalized_percent_hemolysis_unclipped",
        "qc_status",
    }
    if not required.issubset(required_fields):
        raise ValueError("raw hemolysis observation context is incomplete")
    if raw.get("raw_values_are_immutable") is not True:
        raise ValueError("raw hemolysis values are not immutable")
    if raw.get("negative_or_above_100_values_are_preserved") is not True:
        raise ValueError("raw normalized hemolysis would be silently clipped")
    censoring = raw.get("censoring_rules")
    if not isinstance(censoring, Mapping):
        raise ValueError("hemolysis censoring rules are missing")
    if censoring.get("hc10_not_reached") != "right_censored_above_max_tested_um":
        raise ValueError("HC10 right-censoring semantics drifted")
    if censoring.get("hc50_not_reached") != "right_censored_above_max_tested_um":
        raise ValueError("HC50 right-censoring semantics drifted")
    if raw.get("failed_runs_and_retests_both_retained") is not True:
        raise ValueError("failed hemolysis runs would be lost")

    split = contract.get("split_contract")
    if not isinstance(split, Mapping):
        raise ValueError("hemolysis split contract is missing")
    if split.get("exact_sequence_groups_disjoint") is not True:
        raise ValueError("exact sequence leakage is not forbidden")
    if split.get("design_campaign_groups_disjoint") is not True:
        raise ValueError("design campaign leakage is not forbidden")
    if float(split.get("maximum_cross_split_global_identity", 1.0)) > 0.4:
        raise ValueError("cross-split sequence identity is too permissive")
    if split.get("donors_stratified_not_pooled") is not True:
        raise ValueError("donor effects would be pooled away")
    if split.get("current_candidate_batch_used_to_define_split") is not False:
        raise ValueError("current candidates influenced the split")
    if split.get("blind_candidate_results_may_refit_model") is not False:
        raise ValueError("blind candidate results could refit the model")

    model = contract.get("model_card_contract")
    if not isinstance(model, Mapping):
        raise ValueError("hemolysis model-card contract is missing")
    if model.get("primary_endpoint") != "donor_conditioned_concentration_response":
        raise ValueError("hemolysis endpoint was collapsed")
    if model.get("probability_or_interval_calibration_required") is not True:
        raise ValueError("hemolysis model calibration is not required")
    if model.get("content_addressed_runtime_required") is not True:
        raise ValueError("hemolysis model runtime is not reproducible")
    if model.get("single_global_binary_label_allowed") is not False:
        raise ValueError("conditioned hemolysis was collapsed to one label")

    decision = contract.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("hemolysis contract decision is missing")
    forbidden_true = (
        "measurements_collected",
        "model_trained",
        "candidate_safety_threshold_created",
        "formal_science_run_submitted",
        "safety_gate_lowered",
    )
    if any(decision.get(field) is not False for field in forbidden_true):
        raise ValueError("hemolysis acquisition contract overclaims completion")

    computed = acceptance_artifacts(contract)
    if contract.get("acceptance_artifacts") != computed:
        raise ValueError("prospective hemolysis acceptance hashes drifted")
    return computed
