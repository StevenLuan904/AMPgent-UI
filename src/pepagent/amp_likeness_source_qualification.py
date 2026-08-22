from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.amp-likeness-source-rights-endpoint-triage.1"


def acceptance_artifacts(witness: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source_rights_and_endpoint_audit_sha256": sha256_json(
            witness.get("source_qualification")
        ),
        "candidate_exclusion_and_acquisition_boundary_sha256": sha256_json(
            witness.get("candidate_exclusion_and_acquisition_boundary")
        ),
        "fail_closed_decision_sha256": sha256_json(witness.get("decision")),
    }


def validate_amp_likeness_source_qualification(
    witness: Mapping[str, Any],
) -> dict[str, str]:
    """Validate source triage without pretending that a training set was acquired."""

    if witness.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("AMP-likeness source qualification schema is invalid")
    sources = witness.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 5:
        raise ValueError("AMP-likeness source qualification lacks primary sources")
    if any(not str(source).startswith("https://") for source in sources):
        raise ValueError("AMP-likeness source URL is invalid")

    qualification = witness.get("source_qualification")
    if not isinstance(qualification, list):
        raise ValueError("AMP-likeness source qualification is missing")
    by_id = {
        item.get("source_id"): item for item in qualification if isinstance(item, Mapping)
    }
    if set(by_id) != {"DRAMP", "DBAASP", "APD6", "UniProtKB"}:
        raise ValueError("AMP-likeness source set drifted")

    dramp = by_id["DRAMP"]
    if dramp.get("database_license_status") != "CC_BY_4_0_source_level_eligible":
        raise ValueError("DRAMP source-level license status drifted")
    if dramp.get("record_level_training_status") != (
        "blocked_pending_release_snapshot_assay_field_and_third_party_rights_audit"
    ):
        raise ValueError("DRAMP could enter fitting before record qualification")

    dbaasp = by_id["DBAASP"]
    if dbaasp.get("database_license_status") != (
        "ambiguous_public_domain_statement_with_non_distribution_and_research_education_terms"
    ):
        raise ValueError("DBAASP rights ambiguity was hidden")
    if dbaasp.get("written_commercial_derivative_model_permission_required") is not True:
        raise ValueError("DBAASP could enter enterprise fitting without written permission")

    apd = by_id["APD6"]
    if apd.get("database_license_status") != (
        "copyright_protected_commercial_derivative_model_rights_not_explicit"
    ):
        raise ValueError("APD6 copyright status drifted")
    if apd.get("conditioned_MIC_export_status") != "official_export_not_yet_ready":
        raise ValueError("APD6 MIC export readiness was overclaimed")

    uniprot = by_id["UniProtKB"]
    if uniprot.get("database_license_status") != (
        "CC_BY_4_0_with_patent_and_third_party_rights_notice"
    ):
        raise ValueError("UniProt rights notice drifted")
    if uniprot.get("primary_negative_endpoint_status") != (
        "ineligible_without_matched_negative_antimicrobial_assay"
    ):
        raise ValueError("UniProt absence of annotation could become a negative label")

    boundary = witness.get("candidate_exclusion_and_acquisition_boundary")
    if not isinstance(boundary, Mapping):
        raise ValueError("AMP-likeness acquisition boundary is missing")
    required_false = (
        "current_773_candidates_downloaded_into_reference_corpus",
        "current_773_candidates_used_for_source_selection",
        "current_773_candidates_used_for_split_or_model_selection",
        "random_or_shuffled_sequences_used_as_primary_negatives",
        "database_membership_used_as_positive_endpoint",
    )
    if any(boundary.get(field) is not False for field in required_false):
        raise ValueError("AMP-likeness source qualification could leak candidates or labels")
    if boundary.get("qualified_training_records_acquired") != 0:
        raise ValueError("AMP-likeness training records were overclaimed")
    if boundary.get("matched_experimentally_inactive_records_acquired") != 0:
        raise ValueError("AMP-likeness hard negatives were overclaimed")

    decision = witness.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("AMP-likeness source decision is missing")
    if decision.get("task_status") != "blocked":
        raise ValueError("AMP-likeness qualification did not fail closed")
    if decision.get("stop_condition_triggered") != (
        "commercial_internal_use_or_matched_experimentally_inactive_records_not_confirmed"
    ):
        raise ValueError("AMP-likeness stop condition drifted")
    for field in (
        "licensed_assay_record_manifest_complete",
        "matched_experimentally_inactive_bundle_complete",
        "grouped_split_frozen",
        "model_trained",
        "formal_science_run_submitted",
    ):
        if decision.get(field) is not False:
            raise ValueError("AMP-likeness source triage overclaims completion")

    computed = acceptance_artifacts(witness)
    if witness.get("acceptance_artifacts") != computed:
        raise ValueError("AMP-likeness source qualification hashes drifted")
    return computed
