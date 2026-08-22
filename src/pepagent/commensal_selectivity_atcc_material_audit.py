from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.commensal-selectivity-atcc-material-source-audit.1"

FROZEN_PRODUCTS = {
    "Staphylococcus_aureus_ATCC_29213": "29213",
    "Staphylococcus_aureus_ATCC_43300_MRSA": "43300",
    "Escherichia_coli_ATCC_25922": "25922",
    "Staphylococcus_epidermidis_ATCC_35984_RP62A": "35984",
    "Staphylococcus_epidermidis_ATCC_12228": "12228",
    "Staphylococcus_hominis_ATCC_27844_DM122": "27844",
    "Cutibacterium_acnes_ATCC_6919_NCTC_737": "6919",
}


def acceptance_artifacts(witness: Mapping[str, Any]) -> dict[str, str]:
    return {
        "strain_catalog_identity_matrix_sha256": sha256_json(
            witness.get("strain_catalog_identity_matrix")
        ),
        "material_transfer_commercial_use_and_CRO_rights_gap_sha256": sha256_json(
            witness.get("material_and_data_rights_audit")
        ),
        "procurement_and_lot_qualification_checklist_sha256": sha256_json(
            witness.get("procurement_and_lot_qualification_checklist")
        ),
        "candidate_non_transfer_boundary_sha256": sha256_json(
            witness.get("candidate_and_execution_boundary")
        ),
    }


def validate_commensal_selectivity_atcc_material_audit(
    witness: Mapping[str, Any],
) -> dict[str, str]:
    """Validate catalog identity without overclaiming material or commercial rights."""

    if witness.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("ATCC material-source audit schema is invalid")
    sources = witness.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 11:
        raise ValueError("ATCC material-source audit lacks primary sources")
    if any(not str(source).startswith("https://www.atcc.org/") for source in sources):
        raise ValueError("ATCC material-source audit contains a non-ATCC source")

    matrix = witness.get("strain_catalog_identity_matrix")
    if not isinstance(matrix, list):
        raise ValueError("ATCC strain catalog identity matrix is missing")
    by_identity = {
        item.get("strain_identity"): item
        for item in matrix
        if isinstance(item, Mapping)
    }
    if set(by_identity) != set(FROZEN_PRODUCTS):
        raise ValueError("ATCC frozen seven-strain panel drifted")
    for strain_identity, catalog_number in FROZEN_PRODUCTS.items():
        item = by_identity[strain_identity]
        if item.get("catalog_number") != catalog_number:
            raise ValueError(f"ATCC catalog identity drifted for {strain_identity}")
        if item.get("catalog_identity_publicly_verified") is not True:
            raise ValueError(f"ATCC catalog identity is unverified for {strain_identity}")
        if item.get("genome_sequenced") is not True:
            raise ValueError(f"ATCC genome identity support drifted for {strain_identity}")
        if item.get("lot_CoA_sha256") != "required_after_procurement_not_yet_available":
            raise ValueError(f"ATCC lot CoA was overclaimed for {strain_identity}")
        if item.get("current_stock_shipping_and_export_status") != (
            "dynamic_not_qualified_by_static_catalog_audit"
        ):
            raise ValueError(f"ATCC logistics were overclaimed for {strain_identity}")

    rights = witness.get("material_and_data_rights_audit")
    if not isinstance(rights, Mapping):
        raise ValueError("ATCC material and data-rights audit is missing")
    if rights.get("MTA_version") != "7.1":
        raise ValueError("ATCC MTA version drifted")
    required_statuses = {
        "commercial_internal_material_use": (
            "written_ATCC_commercial_use_license_required_not_yet_confirmed"
        ),
        "fee_for_service_CRO_use": "ATCC_license_required_for_CRO_not_yet_confirmed",
        "original_material_or_progeny_transfer_to_CRO": (
            "not_authorized_by_default_CRO_should_obtain_directly_from_ATCC"
        ),
        "screening_use_for_for_profit_recipient": (
            "screening_addendum_and_annual_fee_may_apply_not_yet_executed"
        ),
        "screening_as_commercial_CRO_service": (
            "not_authorized_by_screening_addendum_commercial_license_required"
        ),
        "authenticated_ATCC_data_for_commercial_derivative_model": (
            "prior_written_ATCC_permission_required_not_yet_confirmed"
        ),
    }
    for field, expected in required_statuses.items():
        if rights.get(field) != expected:
            raise ValueError(f"ATCC rights status drifted for {field}")
    if rights.get("catalog_identity_verification_equals_use_rights") is not False:
        raise ValueError("ATCC catalog facts could be mistaken for use rights")

    checklist = witness.get("procurement_and_lot_qualification_checklist")
    if not isinstance(checklist, Mapping):
        raise ValueError("ATCC procurement checklist is missing")
    if checklist.get("required_before_material_order") != [
        "organization_and_project_use_classification",
        "ATCC_commercial_use_or_project_specific_written_rights_decision",
        "CRO_direct_purchase_or_ATCC_CRO_license_route",
        "product_specific_permits_restrictions_and_screening_addenda",
        "biosafety_import_export_and_shipping_authorizations",
        "current_quote_stock_and_lead_time",
    ]:
        raise ValueError("ATCC pre-order checklist drifted")
    if checklist.get("required_before_assay_start") != [
        "lot_specific_CoA_and_certificate_of_origin_when_applicable",
        "master_stock_identity_and_passage_ledger",
        "growth_purity_and_genome_or_orthogonal_identity_QC",
        "provider_raw_data_and_derivative_model_rights_addendum",
        "frozen_seven_strain_three_lane_protocol_receipt",
    ]:
        raise ValueError("ATCC pre-assay checklist drifted")

    boundary = witness.get("candidate_and_execution_boundary")
    if not isinstance(boundary, Mapping):
        raise ValueError("ATCC candidate and execution boundary is missing")
    for field in (
        "procurement_started",
        "purchase_or_license_committed",
        "material_received",
        "material_transferred_to_CRO",
        "reference_pilot_started",
        "current_773_candidate_sequences_disclosed",
        "current_773_candidate_samples_transferred",
        "formal_science_run_submitted",
    ):
        if boundary.get(field) is not False:
            raise ValueError("ATCC material audit overclaims execution or leaks candidates")

    decision = witness.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("ATCC material-source decision is missing")
    if decision.get("exact_catalog_identity_status") != "verified_for_all_seven_strains":
        raise ValueError("ATCC seven-strain catalog identity was not closed")
    for field in (
        "current_lot_qualified",
        "commercial_internal_material_use_qualified",
        "CRO_use_and_transfer_qualified",
        "authenticated_data_derivative_model_use_qualified",
        "provider_fully_qualified",
    ):
        if decision.get(field) is not False:
            raise ValueError("ATCC material-source decision overclaims qualification")

    computed = acceptance_artifacts(witness)
    if witness.get("acceptance_artifacts") != computed:
        raise ValueError("ATCC material-source acceptance hashes drifted")
    return computed
