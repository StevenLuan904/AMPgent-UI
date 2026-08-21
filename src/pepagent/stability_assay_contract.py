from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.serum-protease-stability-contract.1"


def acceptance_artifacts(contract: Mapping[str, Any]) -> dict[str, str]:
    return {
        "serum_and_protease_condition_matrix_sha256": sha256_json(
            contract.get("serum_and_protease_condition_matrix")
        ),
        "timecourse_lcms_measurement_and_censoring_schema_sha256": sha256_json(
            contract.get("timecourse_lcms_measurement_and_censoring_schema")
        ),
        "leakage_safe_reference_and_candidate_blinding_plan_sha256": sha256_json(
            contract.get("reference_and_candidate_blinding_plan")
        ),
        "stability_model_card_and_qualification_contract_sha256": sha256_json(
            {
                "split_contract": contract.get("split_contract"),
                "model_card_contract": contract.get("model_card_contract"),
            }
        ),
    }


def validate_serum_protease_stability_contract(
    contract: Mapping[str, Any],
) -> dict[str, str]:
    """Validate a prospective stability contract without claiming measurements."""

    if contract.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("serum/protease stability contract schema is invalid")
    sources = contract.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 4:
        raise ValueError("stability contract lacks primary sources")
    if any(not str(source).startswith("https://") for source in sources):
        raise ValueError("stability source identity is invalid")

    matrix = contract.get("serum_and_protease_condition_matrix")
    if not isinstance(matrix, Mapping):
        raise ValueError("stability condition matrix is missing")
    serum = matrix.get("primary_human_serum_lane")
    if not isinstance(serum, Mapping):
        raise ValueError("primary serum lane is missing")
    expected_serum = {
        "species": "Homo sapiens",
        "serum_fraction_volume_percent": 50,
        "temperature_c": 37,
        "peptide_concentration_um": 10,
        "minimum_independent_donors": 3,
        "donor_pooling_allowed": False,
        "technical_replicates_per_donor_timepoint": 3,
        "primary_readout": "LCMS_intact_parent_relative_to_isotope_or_sequence_internal_standard",
    }
    for key, expected in expected_serum.items():
        if serum.get(key) != expected:
            raise ValueError(f"primary serum condition drifted: {key}")
    if serum.get("timepoints_minutes") != [0, 5, 15, 30, 60, 120, 240, 480, 1440]:
        raise ValueError("serum timepoint series drifted")
    expected_quench = (
        "ice_cold_acetonitrile_with_internal_standard_then_protein_precipitation"
    )
    if serum.get("quench") != expected_quench:
        raise ValueError("serum quench is not frozen")

    lanes = matrix.get("purified_protease_diagnostic_lanes")
    if not isinstance(lanes, list):
        raise ValueError("purified protease lanes are missing")
    enzymes = {lane.get("enzyme") for lane in lanes if isinstance(lane, Mapping)}
    if enzymes != {"trypsin", "chymotrypsin", "human_neutrophil_elastase"}:
        raise ValueError("purified protease diagnostic set drifted")
    for lane in lanes:
        if not isinstance(lane, Mapping):
            raise ValueError("protease lane is invalid")
        required = {
            "enzyme",
            "supplier",
            "lot",
            "activity_units_per_ml",
            "buffer",
            "pH",
            "temperature_c",
            "peptide_concentration_um",
            "timepoints_minutes",
        }
        if not required.issubset(lane):
            raise ValueError("protease lane context is incomplete")
        if lane.get("temperature_c") != 37:
            raise ValueError("protease lane temperature drifted")
    if matrix.get("serum_and_purified_protease_endpoints_may_be_pooled") is not False:
        raise ValueError("serum and purified-protease endpoints would be pooled")

    raw = contract.get("timecourse_lcms_measurement_and_censoring_schema")
    if not isinstance(raw, Mapping):
        raise ValueError("LC-MS observation schema is missing")
    required_fields = {
        "sequence_sha256",
        "material_lot",
        "purity_percent",
        "matrix_or_enzyme_lane",
        "donor_id_pseudonym",
        "enzyme_supplier_lot_and_activity",
        "assay_day",
        "timepoint_minutes",
        "technical_replicate",
        "raw_parent_peak_area",
        "raw_internal_standard_peak_area",
        "intact_parent_fraction_of_t0_unclipped",
        "fragment_mz_retention_time_and_assignment",
        "lloq",
        "qc_status",
        "qc_reason_codes",
    }
    if not required_fields.issubset(set(raw.get("required_observation_fields", []))):
        raise ValueError("LC-MS observation context is incomplete")
    if raw.get("intact_parent_and_fragments_stored_separately") is not True:
        raise ValueError("intact parent could be confused with fragments")
    if raw.get("fragment_signal_counts_as_intact_parent") is not False:
        raise ValueError("fragment signal could inflate intact parent")
    if raw.get("raw_values_are_immutable") is not True:
        raise ValueError("raw LC-MS values are not immutable")
    if raw.get("failed_runs_and_retests_both_retained") is not True:
        raise ValueError("failed stability runs would be lost")
    censoring = raw.get("censoring_rules")
    if not isinstance(censoring, Mapping):
        raise ValueError("stability censoring rules are missing")
    if censoring.get("parent_below_lloq") != "left_censored_at_lloq_not_zero":
        raise ValueError("below-LLOQ values would be coerced")
    if censoring.get("half_life_not_reached") != "right_censored_above_last_timepoint":
        raise ValueError("half-life right censoring drifted")

    blinding = contract.get("reference_and_candidate_blinding_plan")
    if not isinstance(blinding, Mapping):
        raise ValueError("stability blinding plan is missing")
    if blinding.get("current_773_candidates_excluded_from_fitting") is not True:
        raise ValueError("current candidates could influence fitting")
    if blinding.get("current_773_candidates_reserved_for_blind_test_only") is not True:
        raise ValueError("current candidates are not reserved for blind testing")
    if blinding.get("candidate_identity_unblinded_before_assay_and_model_lock") is not False:
        raise ValueError("candidate identity could be unblinded early")
    rights = blinding.get("required_data_rights")
    if not isinstance(rights, Mapping) or not all(rights.values()):
        raise ValueError("stability data and derivative-model rights are incomplete")

    split = contract.get("split_contract")
    if not isinstance(split, Mapping):
        raise ValueError("stability split contract is missing")
    if split.get("exact_sequence_groups_disjoint") is not True:
        raise ValueError("exact sequence leakage is not forbidden")
    if split.get("design_campaign_groups_disjoint") is not True:
        raise ValueError("design campaign leakage is not forbidden")
    if float(split.get("maximum_cross_split_global_identity", 1.0)) > 0.4:
        raise ValueError("cross-split sequence identity is too permissive")
    if split.get("donors_stratified_not_pooled") is not True:
        raise ValueError("donor effects would be pooled away")
    if split.get("current_candidates_used_to_define_operating_point") is not False:
        raise ValueError("current candidates could define the operating point")

    model = contract.get("model_card_contract")
    if not isinstance(model, Mapping):
        raise ValueError("stability model-card contract is missing")
    if model.get("primary_endpoint") != "conditioned_intact_parent_survival_curve":
        raise ValueError("stability endpoint was collapsed")
    if model.get("single_global_stable_unstable_label_allowed") is not False:
        raise ValueError("conditioned stability was collapsed to one label")
    if model.get("censoring_aware_estimation_required") is not True:
        raise ValueError("stability model ignores censoring")
    if model.get("content_addressed_runtime_required") is not True:
        raise ValueError("stability runtime is not reproducible")

    decision = contract.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("stability contract decision is missing")
    forbidden_true = (
        "measurements_collected",
        "model_trained",
        "candidate_operating_point_created",
        "formal_science_run_submitted",
    )
    if any(decision.get(field) is not False for field in forbidden_true):
        raise ValueError("stability acquisition contract overclaims completion")

    computed = acceptance_artifacts(contract)
    if contract.get("acceptance_artifacts") != computed:
        raise ValueError("stability acceptance hashes drifted")
    return computed
