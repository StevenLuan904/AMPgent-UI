from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.amp-likeness-reference-model-contract.1"


def acceptance_artifacts(contract: Mapping[str, Any]) -> dict[str, str]:
    return {
        "positive_and_hard_negative_endpoint_semantics_sha256": sha256_json(
            contract.get("endpoint_semantics")
        ),
        "training_lineage_license_and_overlap_witness_sha256": sha256_json(
            contract.get("lineage_license_and_overlap_contract")
        ),
        "leakage_safe_train_calibration_ood_split_sha256": sha256_json(
            contract.get("split_contract")
        ),
        "transparent_runtime_and_model_card_contract_sha256": sha256_json(
            contract.get("model_card_contract")
        ),
    }


def validate_amp_likeness_contract(contract: Mapping[str, Any]) -> dict[str, str]:
    """Validate an assay-grounded AMP reference/model contract without claiming a model."""

    if contract.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("AMP-likeness contract schema is invalid")
    sources = contract.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 5:
        raise ValueError("AMP-likeness contract lacks primary sources")
    if any(not str(source).startswith("https://") for source in sources):
        raise ValueError("AMP-likeness source identity is invalid")

    endpoint = contract.get("endpoint_semantics")
    if not isinstance(endpoint, Mapping):
        raise ValueError("AMP endpoint semantics are missing")
    if endpoint.get("database_membership_alone_is_positive_label") is not False:
        raise ValueError("database membership could become an AMP positive label")
    if endpoint.get("primary_endpoint") != "assay_conditioned_antibacterial_activity":
        raise ValueError("AMP activity was collapsed to database membership")
    required_context = {
        "sequence_and_modification_identity",
        "organism_species_and_strain",
        "assay_method",
        "medium_buffer_and_matrix",
        "temperature_and_exposure_time",
        "inoculum_or_cell_density",
        "concentration_series_and_unit",
        "raw_or_censored_activity_endpoint",
        "publication_and_source_record_id",
    }
    if not required_context.issubset(set(endpoint.get("required_positive_fields", []))):
        raise ValueError("positive activity assay context is incomplete")
    if endpoint.get("MIC_is_conditioned_continuous_or_interval_endpoint") is not True:
        raise ValueError("MIC endpoint could be reduced to a universal label")
    if endpoint.get("universal_MIC_threshold_allowed") is not False:
        raise ValueError("a universal MIC threshold could empty or bias the pool")
    negatives = endpoint.get("negative_evidence_classes")
    if not isinstance(negatives, list) or len(negatives) < 2:
        raise ValueError("hard-negative evidence classes are incomplete")
    classes = {item.get("class") for item in negatives if isinstance(item, Mapping)}
    if "matched_experimentally_inactive" not in classes:
        raise ValueError("matched experimentally inactive hard negatives are missing")
    if endpoint.get("unreviewed_random_sequences_allowed_as_primary_negatives") is not False:
        raise ValueError("unreviewed random sequences could define the negative class")
    if endpoint.get("shuffled_positive_decoys_allowed_as_primary_negatives") is not False:
        raise ValueError("shuffled positives could define the negative class")
    if endpoint.get("negative_matching_axes") != [
        "length",
        "net_charge",
        "hydrophobic_fraction",
        "modification_class",
        "source_or_design_campaign",
        "assay_context",
    ]:
        raise ValueError("hard-negative matching axes drifted")
    if endpoint.get("conflicting_activity_records_retained") is not True:
        raise ValueError("conflicting assay evidence could be silently discarded")

    lineage = contract.get("lineage_license_and_overlap_contract")
    if not isinstance(lineage, Mapping):
        raise ValueError("training lineage/license contract is missing")
    if lineage.get("exact_source_records_content_addressed") is not True:
        raise ValueError("source records are not content addressed")
    if lineage.get("publication_and_database_record_lineage_required") is not True:
        raise ValueError("assay lineage is not traceable")
    if lineage.get("commercial_internal_use_must_be_confirmed_per_source") is not True:
        raise ValueError("commercial internal use is not fail-closed")
    if lineage.get("unknown_license_records_may_enter_training") is not False:
        raise ValueError("unknown-license records could enter training")
    if lineage.get("exact_sequence_conflicts_preserved_as_conditioned_records") is not True:
        raise ValueError("conflicting sequence records could be collapsed")
    overlap = lineage.get("overlap_exclusions")
    if not isinstance(overlap, Mapping):
        raise ValueError("training overlap exclusions are missing")
    required_exclusions = {
        "current_773_candidates",
        "current_candidates_parents_and_refinements",
        "prospective_candidate_blind_test",
    }
    if not required_exclusions.issubset(set(overlap.get("excluded_from_all_fitting", []))):
        raise ValueError("current candidate lineage could influence AMP fitting")
    rights = lineage.get("required_rights")
    if not isinstance(rights, Mapping) or not all(rights.values()):
        raise ValueError("AMP data and derivative-model rights are incomplete")

    split = contract.get("split_contract")
    if not isinstance(split, Mapping):
        raise ValueError("AMP split contract is missing")
    if split.get("partition_roles") != [
        "train",
        "calibration",
        "sequence_family_OOD",
        "publication_campaign_OOD",
        "organism_assay_OOD",
        "prospective_candidate_blind_test",
    ]:
        raise ValueError("AMP split roles drifted")
    if split.get("exact_sequence_groups_disjoint") is not True:
        raise ValueError("exact sequence leakage is not forbidden")
    if split.get("publication_database_and_design_campaign_groups_disjoint") is not True:
        raise ValueError("publication or design-campaign leakage is not forbidden")
    if float(split.get("maximum_cross_split_global_identity", 1.0)) > 0.4:
        raise ValueError("cross-split sequence identity is too permissive")
    if float(split.get("minimum_bidirectional_alignment_coverage", 0.0)) < 0.8:
        raise ValueError("cross-split alignment coverage is too permissive")
    if split.get("current_candidates_used_to_define_split") is not False:
        raise ValueError("current candidates could define the split")
    if split.get("current_candidates_used_to_define_operating_point") is not False:
        raise ValueError("current candidates could define the operating point")
    if split.get("blind_candidate_results_may_refit_model") is not False:
        raise ValueError("blind candidate results could leak back into fitting")

    model = contract.get("model_card_contract")
    if not isinstance(model, Mapping):
        raise ValueError("AMP model-card contract is missing")
    if model.get("primary_task") != "conditioned_activity_and_potency_estimation":
        raise ValueError("AMP model task was collapsed to membership classification")
    if model.get("single_global_AMP_non_AMP_classifier_as_hard_gate_allowed") is not False:
        raise ValueError("a global AMP classifier could become a hard gate")
    if model.get("transparent_descriptor_baseline_required") is not True:
        raise ValueError("transparent AMP baseline is missing")
    if model.get("content_addressed_runtime_required") is not True:
        raise ValueError("AMP runtime is not reproducible")
    if model.get("calibration_and_all_OOD_reports_required") is not True:
        raise ValueError("AMP calibration/OOD evidence is incomplete")
    if model.get("candidate_use") != "non_weighted_Pareto_axis_not_safety_hard_gate":
        raise ValueError("AMP-likeness could improperly become a safety hard gate")

    decision = contract.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("AMP contract decision is missing")
    forbidden_true = (
        "training_records_acquired",
        "model_trained",
        "operating_point_created",
        "formal_science_run_submitted",
    )
    if any(decision.get(field) is not False for field in forbidden_true):
        raise ValueError("AMP reference/model contract overclaims completion")

    computed = acceptance_artifacts(contract)
    if contract.get("acceptance_artifacts") != computed:
        raise ValueError("AMP acceptance hashes drifted")
    return computed
