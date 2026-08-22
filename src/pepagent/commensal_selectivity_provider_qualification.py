from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.commensal-selectivity-provider-rfq.1"
CONTRACT_PATH = "config/enterprise/commensal_selectivity_assay_contract_v39.json"
CONTRACT_SHA256 = "9861ee2c4cb47e5c9ace7fe0714fe5b7d4827f18df7b251a9ee9ca9223868741"

FROZEN_STRAINS = {
    "Staphylococcus_aureus_ATCC_29213",
    "Staphylococcus_aureus_ATCC_43300_MRSA",
    "Escherichia_coli_ATCC_25922",
    "Staphylococcus_epidermidis_ATCC_35984_RP62A",
    "Staphylococcus_epidermidis_ATCC_12228",
    "Staphylococcus_hominis_ATCC_27844_DM122",
    "Cutibacterium_acnes_ATCC_6919_NCTC_737",
}
FROZEN_LANES = {
    "aerobic_reference_MIC_lane",
    "anaerobic_C_acnes_context_lane",
    "common_skin_mimetic_bridge_lane",
}


def acceptance_artifacts(witness: Mapping[str, Any]) -> dict[str, str]:
    return {
        "provider_capability_and_gap_matrix_sha256": sha256_json(
            witness.get("provider_capability_screen")
        ),
        "frozen_protocol_and_raw_delivery_rfq_sha256": sha256_json(
            witness.get("mandatory_rfq_requirements")
        ),
        "commercial_data_and_derivative_model_rights_addendum_sha256": sha256_json(
            witness.get("commercial_and_data_rights_acceptance")
        ),
        "blinded_reference_pilot_acceptance_plan_sha256": sha256_json(
            witness.get("reference_pilot")
        ),
    }


def validate_commensal_selectivity_provider_qualification(
    witness: Mapping[str, Any],
) -> dict[str, str]:
    """Validate a fail-closed RFQ package without claiming provider qualification."""

    if witness.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("commensal-selectivity provider RFQ schema is invalid")
    sources = witness.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 5:
        raise ValueError("provider RFQ lacks primary sources")
    if any(not str(source).startswith("https://") for source in sources):
        raise ValueError("provider RFQ primary source URL is invalid")

    binding = witness.get("frozen_contract_binding")
    if not isinstance(binding, Mapping):
        raise ValueError("frozen selectivity contract binding is missing")
    if binding.get("contract_path") != CONTRACT_PATH:
        raise ValueError("selectivity contract path drifted")
    if binding.get("contract_sha256") != CONTRACT_SHA256:
        raise ValueError("selectivity contract SHA drifted")
    if set(binding.get("strain_identities") or []) != FROZEN_STRAINS:
        raise ValueError("frozen strain panel drifted")
    if set(binding.get("assay_lane_ids") or []) != FROZEN_LANES:
        raise ValueError("frozen assay lanes drifted")
    if binding.get("protocol_change_requires_new_version") is not True:
        raise ValueError("protocol drift could bypass versioning")

    screen = witness.get("provider_capability_screen")
    if not isinstance(screen, list):
        raise ValueError("provider capability screen is missing")
    providers = {
        item.get("provider_id"): item
        for item in screen
        if isinstance(item, Mapping)
    }
    if set(providers) != {"Evotec", "Charles_River", "ATCC"}:
        raise ValueError("provider screen set drifted")
    for provider_id in ("Evotec", "Charles_River"):
        provider = providers[provider_id]
        if provider.get("public_capability_status") != (
            "core_microbiology_capability_publicly_demonstrated"
        ):
            raise ValueError(f"{provider_id} public capability status drifted")
        if provider.get("qualification_status") != (
            "rfq_required_exact_panel_protocol_delivery_and_rights_unconfirmed"
        ):
            raise ValueError(f"{provider_id} was claimed qualified without evidence")
    if providers["ATCC"].get("provider_role") != (
        "authenticated_strain_supplier_and_QC_source_not_full_assay_provider"
    ):
        raise ValueError("ATCC provider role drifted")
    if providers["ATCC"].get("qualification_status") != (
        "material_source_only_pending_exact_catalog_and_MTA_review"
    ):
        raise ValueError("ATCC material-rights status was overclaimed")

    requirements = witness.get("mandatory_rfq_requirements")
    if not isinstance(requirements, Mapping):
        raise ValueError("mandatory RFQ requirements are missing")
    if requirements.get("minimum_biological_replicates_per_strain_condition") != 3:
        raise ValueError("biological replicate contract drifted")
    if requirements.get("minimum_independent_experiment_days") != 2:
        raise ValueError("experiment-day contract drifted")
    if requirements.get("technical_replicates_per_biological_replicate") != 2:
        raise ValueError("technical replicate contract drifted")
    if requirements.get("peptide_concentration_um") != [
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
        raise ValueError("concentration series drifted")
    if requirements.get("time_kill_hours") != [0, 1, 2, 4, 8, 24]:
        raise ValueError("time-kill schedule drifted")
    if requirements.get("time_kill_absolute_concentrations_um") != [1, 4, 16, 64]:
        raise ValueError("absolute-concentration time-kill contract drifted")
    if requirements.get("raw_immutable_delivery_required") is not True:
        raise ValueError("raw immutable delivery is not mandatory")
    if requirements.get("failed_runs_and_assignable_cause_retests_retained") is not True:
        raise ValueError("failed-run retention contract drifted")

    rights = witness.get("commercial_and_data_rights_acceptance")
    if not isinstance(rights, Mapping):
        raise ValueError("commercial and data-rights addendum is missing")
    required_rights = rights.get("required_executed_agreement_terms")
    if not isinstance(required_rights, Mapping) or not required_rights:
        raise ValueError("required executed agreement terms are missing")
    if any(
        value != "contract_required_not_yet_confirmed"
        for value in required_rights.values()
    ):
        raise ValueError("provider rights were overclaimed or weakened")

    pilot = witness.get("reference_pilot")
    if not isinstance(pilot, Mapping):
        raise ValueError("blinded reference pilot plan is missing")
    if pilot.get("current_773_candidates_included") is not False:
        raise ValueError("current candidates could leak into the reference pilot")
    if pilot.get("candidate_samples_allowed_before_pilot_acceptance") is not False:
        raise ValueError("candidate samples could be sent before pilot acceptance")
    if pilot.get("candidate_identity_unblinded") is not False:
        raise ValueError("candidate identity was prematurely unblinded")
    if pilot.get("reference_agents") != [
        "LL_37",
        "mupirocin",
        "chlorhexidine",
        "species_appropriate_antibiotic_QC_controls",
    ]:
        raise ValueError("reference pilot control panel drifted")

    decision = witness.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("provider qualification decision is missing")
    if decision.get("task_status") != "in_progress":
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
