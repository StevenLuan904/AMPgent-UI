from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.mammalian-cytotoxicity-assay-model-contract.1"


def acceptance_artifacts(contract: Mapping[str, Any]) -> dict[str, str]:
    return {
        "cell_line_exposure_concentration_and_control_matrix_sha256": sha256_json(
            contract.get("cell_exposure_matrix")
        ),
        "raw_viability_membrane_damage_and_censoring_schema_sha256": sha256_json(
            contract.get("raw_measurement_schema")
        ),
        "leakage_safe_reference_and_candidate_blinding_plan_sha256": sha256_json(
            contract.get("reference_and_candidate_blinding_plan")
        ),
        "cytotoxicity_model_card_and_qualification_contract_sha256": sha256_json(
            {
                "split_contract": contract.get("split_contract"),
                "model_card_contract": contract.get("model_card_contract"),
            }
        ),
    }


def validate_mammalian_cytotoxicity_contract(
    contract: Mapping[str, Any],
) -> dict[str, str]:
    """Validate a prospective cytotoxicity contract without claiming cell measurements."""

    if contract.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("mammalian-cytotoxicity contract schema is invalid")
    sources = contract.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 6:
        raise ValueError("mammalian-cytotoxicity contract lacks primary sources")
    if any(not str(source).startswith("https://") for source in sources):
        raise ValueError("mammalian-cytotoxicity source identity is invalid")

    matrix = contract.get("cell_exposure_matrix")
    if not isinstance(matrix, Mapping):
        raise ValueError("cytotoxicity cell/exposure matrix is missing")
    cells = matrix.get("primary_cell_models")
    if not isinstance(cells, list):
        raise ValueError("primary skin cell models are missing")
    identities = {item.get("cell_identity") for item in cells if isinstance(item, Mapping)}
    if identities != {
        "adult_primary_human_epidermal_keratinocytes_HEKa_ATCC_PCS_200_011",
        "adult_primary_human_dermal_fibroblasts_HDFa_ATCC_PCS_201_012",
    }:
        raise ValueError("primary skin cell identities drifted")
    if any(item.get("minimum_independent_donor_lots") != 3 for item in cells):
        raise ValueError("cytotoxicity donor-lot replication drifted")
    if matrix.get("minimum_independent_experiment_days") != 2:
        raise ValueError("cytotoxicity experiment-day replication drifted")
    if matrix.get("technical_replicates_per_condition") != 3:
        raise ValueError("cytotoxicity technical replication drifted")
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
        raise ValueError("cytotoxicity concentration ladder drifted")
    if matrix.get("exposure_time_hours") != [1, 4, 24, 72]:
        raise ValueError("cytotoxicity exposure times drifted")
    if matrix.get("primary_readouts") != [
        "intracellular_ATP_viable_cell_signal",
        "extracellular_LDH_membrane_damage",
        "automated_live_dead_cell_count_and_morphology",
    ]:
        raise ValueError("cytotoxicity orthogonal readouts drifted")
    if matrix.get("viability_and_membrane_damage_may_be_collapsed") is not False:
        raise ValueError("viability and membrane damage could be collapsed")
    controls = set(matrix.get("required_controls", []))
    if not {
        "untreated_cells",
        "vehicle_matched_cells",
        "no_cell_peptide_interference_blank_each_concentration",
        "maximum_lysis_control_for_LDH",
        "staurosporine_apoptosis_control",
        "medium_only_background",
    }.issubset(controls):
        raise ValueError("cytotoxicity controls are incomplete")
    if matrix.get("immortalized_or_cancer_cell_line_may_replace_primary_panel") is not False:
        raise ValueError("a surrogate line could replace the primary skin panel")
    if matrix.get("serum_or_matrix_bridge_pooled_with_primary_endpoint") is not False:
        raise ValueError("cytotoxicity matrix bridge could be pooled with the primary endpoint")

    raw = contract.get("raw_measurement_schema")
    if not isinstance(raw, Mapping):
        raise ValueError("cytotoxicity raw schema is missing")
    required_fields = {
        "candidate_or_control_blind_id",
        "exact_sequence_and_modification_sha256",
        "cell_identity_and_donor_lot",
        "passage_number_and_population_doublings",
        "experiment_day_plate_and_well",
        "medium_serum_and_matrix_identity",
        "peptide_concentration_um",
        "exposure_time_hours",
        "ATP_raw_luminescence",
        "LDH_raw_signal",
        "live_dead_cell_counts",
        "morphology_image_artifact_sha256",
        "peptide_only_interference_signal",
        "control_raw_values",
        "QC_status_and_reason_codes",
    }
    if not required_fields.issubset(set(raw.get("required_observation_fields", []))):
        raise ValueError("cytotoxicity observation context is incomplete")
    if raw.get("raw_values_are_immutable") is not True:
        raise ValueError("raw cytotoxicity values are not immutable")
    if raw.get("failed_runs_and_assignable_cause_retests_both_retained") is not True:
        raise ValueError("failed cytotoxicity runs could be discarded")
    if raw.get("metabolic_signal_alone_proves_viability") is not False:
        raise ValueError("metabolic signal could be overinterpreted")
    if raw.get("peptide_assay_interference_requires_orthogonal_resolution") is not True:
        raise ValueError("peptide assay interference is not fail-closed")
    censoring = raw.get("censoring_rules")
    if not isinstance(censoring, Mapping):
        raise ValueError("cytotoxicity censoring rules are missing")
    if censoring.get("CC50_above_maximum_tested") != "right_censored_above_256_um":
        raise ValueError("high CC50 censoring drifted")
    if censoring.get("CC10_below_minimum_tested") != "left_censored_at_or_below_0_5_um":
        raise ValueError("low CC10 censoring drifted")
    if censoring.get("interfering_readout") != "invalid_for_that_readout_not_imputed_safe":
        raise ValueError("interfering readout could be imputed safe")

    blinding = contract.get("reference_and_candidate_blinding_plan")
    if not isinstance(blinding, Mapping):
        raise ValueError("cytotoxicity blinding plan is missing")
    required_false = (
        "current_773_candidates_used_for_training",
        "current_773_candidates_used_for_calibration",
        "current_773_candidates_used_for_model_selection",
        "current_773_candidates_used_for_operating_point",
        "candidate_identity_unblinded_before_protocol_assay_and_model_lock",
    )
    if any(blinding.get(field) is not False for field in required_false):
        raise ValueError("current candidates could influence cytotoxicity qualification")
    if blinding.get("current_773_candidates_reserved_for_blind_test_only") is not True:
        raise ValueError("current candidates are not reserved for blind testing")
    rights = blinding.get("required_data_and_cell_rights")
    if not isinstance(rights, Mapping) or not all(rights.values()):
        raise ValueError("cytotoxicity data, cell and derivative-model rights are incomplete")

    split = contract.get("split_contract")
    if not isinstance(split, Mapping):
        raise ValueError("cytotoxicity split contract is missing")
    if split.get("exact_sequence_groups_disjoint") is not True:
        raise ValueError("exact sequence leakage is not forbidden")
    if split.get("publication_and_design_campaign_groups_disjoint") is not True:
        raise ValueError("publication/design-campaign leakage is not forbidden")
    if float(split.get("maximum_cross_split_global_identity", 1.0)) > 0.4:
        raise ValueError("cytotoxicity cross-split identity is too permissive")
    if split.get("donor_lots_may_cross_model_evaluation_partitions") is not False:
        raise ValueError("donor-lot leakage is not forbidden")
    if split.get("current_candidates_used_to_define_operating_point") is not False:
        raise ValueError("current candidates could define the cytotoxicity operating point")

    model = contract.get("model_card_contract")
    if not isinstance(model, Mapping):
        raise ValueError("cytotoxicity model-card contract is missing")
    if model.get("primary_endpoint") != "cell_type_time_and_concentration_conditioned_cytotoxicity":
        raise ValueError("cytotoxicity endpoint was collapsed")
    if model.get("single_global_toxic_non_toxic_label_allowed") is not False:
        raise ValueError("cytotoxicity could be collapsed to one label")
    if model.get("orthogonal_endpoint_disagreement_report_required") is not True:
        raise ValueError("cytotoxicity endpoint disagreement could be hidden")
    if model.get("content_addressed_runtime_required") is not True:
        raise ValueError("cytotoxicity runtime is not reproducible")
    if model.get("ToxinPred_or_generic_sequence_label_counts_as_cell_assay") is not False:
        raise ValueError("generic toxicity prediction could masquerade as cell evidence")

    decision = contract.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("cytotoxicity contract decision is missing")
    forbidden_true = (
        "measurements_collected",
        "model_trained",
        "candidate_operating_point_created",
        "formal_science_run_submitted",
    )
    if any(decision.get(field) is not False for field in forbidden_true):
        raise ValueError("cytotoxicity contract overclaims completion")

    computed = acceptance_artifacts(contract)
    if contract.get("acceptance_artifacts") != computed:
        raise ValueError("cytotoxicity acceptance hashes drifted")
    return computed
