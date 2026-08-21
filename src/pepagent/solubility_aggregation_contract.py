from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.solubility-aggregation-contract.1"


def acceptance_artifacts(contract: Mapping[str, Any]) -> dict[str, str]:
    return {
        "pH_ionic_strength_concentration_condition_matrix_sha256": sha256_json(
            contract.get("condition_matrix")
        ),
        "raw_solubility_turbidity_and_particle_measurement_schema_sha256": sha256_json(
            contract.get("raw_measurement_schema")
        ),
        "leakage_safe_reference_and_candidate_blinding_plan_sha256": sha256_json(
            contract.get("reference_and_candidate_blinding_plan")
        ),
        "aggregation_model_card_and_qualification_contract_sha256": sha256_json(
            {
                "split_contract": contract.get("split_contract"),
                "model_card_contract": contract.get("model_card_contract"),
            }
        ),
    }


def validate_solubility_aggregation_contract(
    contract: Mapping[str, Any],
) -> dict[str, str]:
    """Validate a prospective developability contract without claiming measurements."""

    if contract.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("solubility/aggregation contract schema is invalid")
    sources = contract.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 4:
        raise ValueError("solubility/aggregation contract lacks primary sources")
    if any(not str(source).startswith("https://") for source in sources):
        raise ValueError("solubility/aggregation source identity is invalid")

    matrix = contract.get("condition_matrix")
    if not isinstance(matrix, Mapping):
        raise ValueError("solubility/aggregation condition matrix is missing")
    expected = {
        "pH_values": [5.5, 7.4, 8.5],
        "nacl_mM": [0, 150, 300],
        "peptide_concentration_um": [10, 50, 100, 250, 500],
        "temperature_c": [25, 37],
        "timepoints_hours": [0, 1, 4, 24],
        "technical_replicates_per_condition": 3,
    }
    for key, value in expected.items():
        if matrix.get(key) != value:
            raise ValueError(f"solubility/aggregation condition drifted: {key}")
    if matrix.get("primary_buffer_system") != "matched_low_absorbance_buffers":
        raise ValueError("buffer system is not frozen")
    if matrix.get("buffer_identity_and_concentration_required") is not True:
        raise ValueError("buffer context is incomplete")
    if matrix.get("premeasurement_filtration_allowed") is not False:
        raise ValueError("filtration could hide aggregates")
    if matrix.get("serum_or_broth_results_may_be_pooled_with_buffer") is not False:
        raise ValueError("matrix-specific aggregation would be pooled")
    if matrix.get("reversibility_dilution_challenge_required") is not True:
        raise ValueError("aggregate reversibility is not measured")

    raw = contract.get("raw_measurement_schema")
    if not isinstance(raw, Mapping):
        raise ValueError("solubility/aggregation raw schema is missing")
    required_fields = {
        "sequence_sha256",
        "material_lot",
        "purity_percent",
        "buffer_identity",
        "pH_measured",
        "nacl_mM",
        "nominal_concentration_um",
        "temperature_c",
        "timepoint_hours",
        "technical_replicate",
        "unfiltered_turbidity_absorbance",
        "dls_count_rate",
        "dls_z_average_nm",
        "dls_pdi",
        "dls_size_distribution_raw",
        "centrifugation_g_time_minutes",
        "supernatant_parent_concentration_um",
        "pellet_or_visible_precipitate_status",
        "post_dilution_recovery_measurements",
        "qc_status",
        "qc_reason_codes",
    }
    if not required_fields.issubset(set(raw.get("required_observation_fields", []))):
        raise ValueError("solubility/aggregation observation context is incomplete")
    if raw.get("raw_values_are_immutable") is not True:
        raise ValueError("raw developability values are not immutable")
    if raw.get("turbidity_dls_and_soluble_mass_stored_separately") is not True:
        raise ValueError("orthogonal developability endpoints were collapsed")
    if raw.get("dls_non_detection_means_no_aggregation") is not False:
        raise ValueError("DLS non-detection could be overinterpreted")
    if raw.get("visible_precipitate_means_zero_solubility") is not False:
        raise ValueError("visible precipitate could erase soluble mass")
    if raw.get("failed_runs_and_retests_both_retained") is not True:
        raise ValueError("failed developability runs would be lost")
    censoring = raw.get("censoring_rules")
    if not isinstance(censoring, Mapping):
        raise ValueError("developability censoring rules are missing")
    if censoring.get("soluble_parent_below_lloq") != "left_censored_at_lloq_not_zero":
        raise ValueError("below-LLOQ soluble parent would be coerced")
    if censoring.get("particle_size_outside_validated_range") != (
        "interval_censored_with_instrument_range_and_raw_count_rate"
    ):
        raise ValueError("particle-size censoring drifted")

    blinding = contract.get("reference_and_candidate_blinding_plan")
    if not isinstance(blinding, Mapping):
        raise ValueError("developability blinding plan is missing")
    if blinding.get("current_773_candidates_excluded_from_fitting") is not True:
        raise ValueError("current candidates could influence fitting")
    if blinding.get("current_773_candidates_reserved_for_blind_test_only") is not True:
        raise ValueError("current candidates are not reserved for blind testing")
    if blinding.get("candidate_identity_unblinded_before_assay_and_model_lock") is not False:
        raise ValueError("candidate identity could be unblinded early")
    rights = blinding.get("required_data_rights")
    if not isinstance(rights, Mapping) or not all(rights.values()):
        raise ValueError("developability data and derivative-model rights are incomplete")

    split = contract.get("split_contract")
    if not isinstance(split, Mapping):
        raise ValueError("developability split contract is missing")
    if split.get("exact_sequence_groups_disjoint") is not True:
        raise ValueError("exact sequence leakage is not forbidden")
    if split.get("design_campaign_groups_disjoint") is not True:
        raise ValueError("design campaign leakage is not forbidden")
    if float(split.get("maximum_cross_split_global_identity", 1.0)) > 0.4:
        raise ValueError("cross-split sequence identity is too permissive")
    if split.get("current_candidates_used_to_define_operating_point") is not False:
        raise ValueError("current candidates could define the operating point")

    model = contract.get("model_card_contract")
    if not isinstance(model, Mapping):
        raise ValueError("developability model-card contract is missing")
    if model.get("primary_endpoint") != "conditioned_soluble_parent_and_particle_state":
        raise ValueError("developability endpoint was collapsed")
    if model.get("single_global_soluble_insoluble_label_allowed") is not False:
        raise ValueError("conditioned developability was collapsed to one label")
    if model.get("multi_endpoint_uncertainty_required") is not True:
        raise ValueError("developability uncertainty is not required")
    if model.get("content_addressed_runtime_required") is not True:
        raise ValueError("developability runtime is not reproducible")

    decision = contract.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("developability contract decision is missing")
    forbidden_true = (
        "measurements_collected",
        "model_trained",
        "candidate_operating_point_created",
        "formal_science_run_submitted",
    )
    if any(decision.get(field) is not False for field in forbidden_true):
        raise ValueError("developability acquisition contract overclaims completion")

    computed = acceptance_artifacts(contract)
    if contract.get("acceptance_artifacts") != computed:
        raise ValueError("solubility/aggregation acceptance hashes drifted")
    return computed
