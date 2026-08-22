from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.mammalian-cytotoxicity-provider-rfq.1"
CONTRACT_PATH = "config/enterprise/prospective_mammalian_cytotoxicity_contract_v39.json"
CONTRACT_SHA256 = "64d16f8b93c6f7e15a9f670296acf9f76c4420dee150ba4f5f16c8428adaceb3"
FROZEN_CELLS = {
    "adult_primary_human_epidermal_keratinocytes_HEKa_ATCC_PCS_200_011": 4,
    "adult_primary_human_dermal_fibroblasts_HDFa_ATCC_PCS_201_012": 6,
}


def acceptance_artifacts(witness: Mapping[str, Any]) -> dict[str, str]:
    return {
        "primary_skin_cell_provider_capability_gap_matrix_sha256": sha256_json(
            witness.get("provider_capability_screen")
        ),
        "frozen_cell_panel_protocol_and_raw_delivery_rfq_sha256": sha256_json(
            witness.get("mandatory_rfq_requirements")
        ),
        "cell_data_image_and_derivative_model_rights_addendum_sha256": sha256_json(
            witness.get("commercial_and_data_rights_acceptance")
        ),
        "blinded_reference_pilot_acceptance_plan_sha256": sha256_json(
            witness.get("reference_pilot")
        ),
    }


def validate_mammalian_cytotoxicity_provider_qualification(
    witness: Mapping[str, Any],
) -> dict[str, str]:
    """Validate a fail-closed primary-skin-cell provider RFP package."""

    if witness.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("mammalian cytotoxicity provider RFQ schema is invalid")
    sources = witness.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 5:
        raise ValueError("provider RFQ lacks primary sources")
    if any(not str(source).startswith("https://") for source in sources):
        raise ValueError("provider RFQ primary source URL is invalid")

    binding = witness.get("frozen_contract_binding")
    if not isinstance(binding, Mapping):
        raise ValueError("frozen cytotoxicity contract binding is missing")
    if binding.get("contract_path") != CONTRACT_PATH:
        raise ValueError("cytotoxicity contract path drifted")
    if binding.get("contract_sha256") != CONTRACT_SHA256:
        raise ValueError("cytotoxicity contract SHA drifted")
    cells = binding.get("primary_cell_models")
    if not isinstance(cells, list):
        raise ValueError("primary cell panel is missing")
    observed = {
        cell.get("cell_identity"): cell.get("maximum_passage_number")
        for cell in cells
        if isinstance(cell, Mapping)
    }
    if observed != FROZEN_CELLS:
        raise ValueError("primary cell panel or passage contract drifted")
    if binding.get("minimum_independent_donor_lots_per_cell_type") != 3:
        raise ValueError("donor-lot contract drifted")
    if binding.get("protocol_change_requires_new_version") is not True:
        raise ValueError("protocol drift could bypass versioning")

    screen = witness.get("provider_capability_screen")
    if not isinstance(screen, list):
        raise ValueError("provider capability screen is missing")
    providers = {
        item.get("provider_id"): item for item in screen if isinstance(item, Mapping)
    }
    if set(providers) != {"Evotec_Cyprotex", "ATCC"}:
        raise ValueError("provider screen set drifted")
    if providers["Evotec_Cyprotex"].get("qualification_status") != (
        "rfq_required_exact_skin_panel_protocol_delivery_and_rights_unconfirmed"
    ):
        raise ValueError("Evotec/Cyprotex was claimed qualified without evidence")
    if providers["ATCC"].get("provider_role") != (
        "authenticated_primary_skin_cell_supplier_not_full_assay_provider"
    ):
        raise ValueError("ATCC provider role drifted")
    if providers["ATCC"].get("qualification_status") != (
        "material_source_only_pending_lot_availability_MTA_and_data_rights_review"
    ):
        raise ValueError("ATCC material-rights status was overclaimed")

    requirements = witness.get("mandatory_rfq_requirements")
    if not isinstance(requirements, Mapping):
        raise ValueError("mandatory RFQ requirements are missing")
    expected = {
        "minimum_independent_experiment_days": 2,
        "technical_replicates_per_condition": 3,
        "peptide_concentration_um": [0.5, 1, 2, 4, 8, 16, 32, 64, 128, 256],
        "exposure_time_hours": [1, 4, 24, 72],
    }
    for field, value in expected.items():
        if requirements.get(field) != value:
            raise ValueError(f"{field} contract drifted")
    if requirements.get("primary_readouts") != [
        "intracellular_ATP_viable_cell_signal",
        "extracellular_LDH_membrane_damage",
        "automated_live_dead_cell_count_and_morphology",
    ]:
        raise ValueError("orthogonal readout contract drifted")
    if requirements.get("raw_immutable_delivery_required") is not True:
        raise ValueError("raw immutable delivery is not mandatory")

    rights = witness.get("commercial_and_data_rights_acceptance")
    terms = rights.get("required_executed_agreement_terms") if isinstance(rights, Mapping) else None
    if not isinstance(terms, Mapping) or not terms:
        raise ValueError("commercial and data-rights addendum is missing")
    if any(value != "contract_required_not_yet_confirmed" for value in terms.values()):
        raise ValueError("provider rights were overclaimed or weakened")

    pilot = witness.get("reference_pilot")
    if not isinstance(pilot, Mapping):
        raise ValueError("blinded reference pilot plan is missing")
    if pilot.get("reference_agents") != ["LL_37", "melittin", "mupirocin", "chlorhexidine"]:
        raise ValueError("reference pilot control panel drifted")
    for field in (
        "current_773_candidates_included",
        "candidate_samples_allowed_before_pilot_acceptance",
        "candidate_identity_unblinded",
    ):
        if pilot.get(field) is not False:
            raise ValueError("current candidates could leak into the reference pilot")

    decision = witness.get("decision")
    if not isinstance(decision, Mapping) or decision.get("task_status") != "in_progress":
        raise ValueError("provider qualification task status drifted")
    for field in (
        "provider_qualified",
        "rfq_sent",
        "purchase_or_contract_committed",
        "reference_pilot_started",
        "candidate_unblinding_authorized",
        "formal_science_run_submitted",
    ):
        if decision.get(field) is not False:
            raise ValueError("provider qualification overclaims execution or authorization")

    computed = acceptance_artifacts(witness)
    if witness.get("acceptance_artifacts") != computed:
        raise ValueError("provider RFQ acceptance hashes drifted")
    return computed
