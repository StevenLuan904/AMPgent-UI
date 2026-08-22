from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.resistance-propensity-provider-rfq.1"
CONTRACT_PATH = "config/enterprise/resistance_propensity_contract_v39.json"
CONTRACT_SHA256 = "e05e79123cff488ee0f5c7a06b5545c34ef9c37151f998be10d638ead65518e7"


def acceptance_artifacts(witness: Mapping[str, Any]) -> dict[str, str]:
    return {
        "provider_serial_passage_and_WGS_gap_matrix_sha256": sha256_json(
            witness.get("provider_capability_screen")
        ),
        "frozen_lineage_exposure_archive_and_raw_delivery_rfq_sha256": sha256_json(
            witness.get("mandatory_rfq_requirements")
        ),
        "biosafety_genomic_data_and_derivative_model_rights_addendum_sha256": sha256_json(
            witness.get("biosafety_genomic_data_and_commercial_rights_acceptance")
        ),
        "blinded_reference_serial_passage_pilot_acceptance_plan_sha256": sha256_json(
            witness.get("reference_pilot")
        ),
    }


def validate_resistance_propensity_provider_qualification(
    witness: Mapping[str, Any],
) -> dict[str, str]:
    """Validate a fail-closed RFP for the frozen resistance-evolution assay."""

    if witness.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("resistance provider RFQ schema is invalid")
    sources = witness.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 5:
        raise ValueError("resistance provider RFQ lacks primary sources")
    if any(not str(source).startswith("https://") for source in sources):
        raise ValueError("resistance provider RFQ primary source URL is invalid")

    binding = witness.get("frozen_contract_binding")
    if not isinstance(binding, Mapping):
        raise ValueError("frozen resistance contract binding is missing")
    if binding.get("contract_path") != CONTRACT_PATH:
        raise ValueError("resistance contract path drifted")
    if binding.get("contract_sha256") != CONTRACT_SHA256:
        raise ValueError("resistance contract SHA drifted")
    if binding.get("organism_strains") != [
        "Escherichia_coli_ATCC_25922",
        "Staphylococcus_epidermidis_ATCC_35984_RP62A",
    ]:
        raise ValueError("resistance organism matrix drifted")
    if binding.get("target_branches") != ["GyrA_8QQI", "PBP2a_3ZFZ"]:
        raise ValueError("resistance target branches drifted")
    exact = {
        "independent_selected_lineages_per_candidate_organism": 8,
        "independent_no_drug_control_lineages_per_organism": 4,
        "total_passages": 30,
        "archive_every_n_passages": 5,
        "MIC_remeasurement_every_n_passages": 5,
        "post_selection_drug_free_passages": 5,
    }
    for field, value in exact.items():
        if binding.get(field) != value:
            raise ValueError(f"{field} contract drifted")
    if binding.get("protocol_change_requires_new_version") is not True:
        raise ValueError("resistance protocol drift could bypass versioning")

    screen = witness.get("provider_capability_screen")
    if not isinstance(screen, list):
        raise ValueError("resistance provider capability screen is missing")
    providers = {
        item.get("provider_id"): item for item in screen if isinstance(item, Mapping)
    }
    if set(providers) != {"Evotec", "Eurofins"}:
        raise ValueError("resistance provider screen set drifted")
    if providers["Evotec"].get("qualification_status") != (
        "rfq_required_exact_serial_passage_lineage_archive_cross_resistance_delivery_and_rights_unconfirmed"
    ):
        raise ValueError("Evotec was claimed qualified without exact protocol evidence")
    if providers["Eurofins"].get("provider_role") != (
        "candidate_modular_MIC_MBC_custom_method_and_WGS_network"
    ):
        raise ValueError("Eurofins modular provider role drifted")
    if providers["Eurofins"].get("qualification_status") != (
        "not_qualified_integrated_serial_passage_lineage_and_resistance_WGS_contract_unconfirmed"
    ):
        raise ValueError("Eurofins integrated capability was overclaimed")

    requirements = witness.get("mandatory_rfq_requirements")
    if not isinstance(requirements, Mapping):
        raise ValueError("mandatory resistance RFQ requirements are missing")
    required_values = {
        "medium": (
            "cation_adjusted_Mueller_Hinton_broth_with_candidate_specific_"
            "recovery_and_adsorption_QC"
        ),
        "temperature_c": 37,
        "passage_interval_hours": 24,
        "transfer_fraction": 0.01,
        "exposure_ladder_relative_to_ancestral_MIC": [
            0,
            0.125,
            0.25,
            0.5,
            1,
            2,
            4,
            8,
            16,
            32,
        ],
        "transfer_source_rule": (
            "highest_concentration_with_at_least_20_percent_growth_relative_"
            "to_no_drug_control"
        ),
    }
    for field, value in required_values.items():
        if requirements.get(field) != value:
            raise ValueError(f"{field} contract drifted")
    for field in (
        "population_extinction_retained_as_competing_outcome",
        "raw_immutable_delivery_required",
        "failed_runs_retests_and_lineage_edges_retained",
        "WGS_FASTQ_BAM_VCF_and_QC_delivery_required",
    ):
        if requirements.get(field) is not True:
            raise ValueError(f"{field} is not mandatory")

    rights = witness.get("biosafety_genomic_data_and_commercial_rights_acceptance")
    terms = rights.get("required_executed_agreement_terms") if isinstance(rights, Mapping) else None
    if not isinstance(terms, Mapping) or not terms:
        raise ValueError("biosafety, genomic-data, and commercial-rights addendum is missing")
    if any(value != "contract_required_not_yet_confirmed" for value in terms.values()):
        raise ValueError("provider biosafety or rights were overclaimed or weakened")

    pilot = witness.get("reference_pilot")
    if not isinstance(pilot, Mapping):
        raise ValueError("blinded resistance reference pilot is missing")
    if pilot.get("reference_agents") != [
        "pexiganan",
        "melittin",
        "colistin_or_daptomycin_species_appropriate",
    ]:
        raise ValueError("reference agent panel drifted")
    for field in (
        "current_773_candidates_included",
        "candidate_samples_allowed_before_pilot_acceptance",
        "candidate_identity_unblinded",
    ):
        if pilot.get(field) is not False:
            raise ValueError("current candidates could leak into the resistance pilot")

    decision = witness.get("decision")
    if not isinstance(decision, Mapping) or decision.get("task_status") != "in_progress":
        raise ValueError("resistance provider qualification task status drifted")
    for field in (
        "integrated_provider_qualified",
        "biosafety_and_strain_use_authorized",
        "rfq_sent",
        "purchase_or_contract_committed",
        "reference_pilot_started",
        "candidate_unblinding_authorized",
        "formal_science_run_submitted",
    ):
        if decision.get(field) is not False:
            raise ValueError("resistance provider qualification overclaims execution")

    computed = acceptance_artifacts(witness)
    if witness.get("acceptance_artifacts") != computed:
        raise ValueError("resistance provider RFQ acceptance hashes drifted")
    return computed
