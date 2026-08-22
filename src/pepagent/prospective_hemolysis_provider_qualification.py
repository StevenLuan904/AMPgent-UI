from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

SCHEMA_VERSION = "ampgent.prospective-hemolysis-provider-rfq.1"
CONTRACT_PATH = "config/enterprise/prospective_hemolysis_assay_contract_v39.json"
CONTRACT_SHA256 = "12cdba1c4b13bb861cb14a61f2f3463c415b096b28be20083897e29665c9eea2"


def acceptance_artifacts(witness: Mapping[str, Any]) -> dict[str, str]:
    return {
        "provider_and_human_RBC_source_gap_matrix_sha256": sha256_json(
            witness.get("provider_capability_screen")
        ),
        "frozen_human_defibrinated_RBC_protocol_and_raw_delivery_rfq_sha256": sha256_json(
            witness.get("mandatory_rfq_requirements")
        ),
        "blood_data_and_derivative_model_rights_addendum_sha256": sha256_json(
            witness.get("commercial_ethics_and_data_rights_acceptance")
        ),
        "blinded_reference_peptide_pilot_acceptance_plan_sha256": sha256_json(
            witness.get("reference_pilot")
        ),
    }


def validate_prospective_hemolysis_provider_qualification(
    witness: Mapping[str, Any],
) -> dict[str, str]:
    """Validate the fail-closed provider RFP for the frozen human-RBC assay."""

    if witness.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("prospective hemolysis provider RFQ schema is invalid")
    sources = witness.get("primary_sources")
    if not isinstance(sources, list) or len(sources) < 5:
        raise ValueError("provider RFQ lacks primary sources")
    if any(not str(source).startswith("https://") for source in sources):
        raise ValueError("provider RFQ primary source URL is invalid")

    binding = witness.get("frozen_contract_binding")
    if not isinstance(binding, Mapping):
        raise ValueError("frozen hemolysis contract binding is missing")
    if binding.get("contract_path") != CONTRACT_PATH:
        raise ValueError("hemolysis contract path drifted")
    if binding.get("contract_sha256") != CONTRACT_SHA256:
        raise ValueError("hemolysis contract SHA drifted")
    if binding.get("primary_matrix") != {
        "species": "Homo_sapiens",
        "blood_preparation": "defibrinated",
        "minimum_independent_donors": 3,
        "donor_pooling_allowed": False,
        "minimum_independent_assay_days": 2,
    }:
        raise ValueError("primary human-RBC matrix drifted")
    if binding.get("protocol_change_requires_new_version") is not True:
        raise ValueError("protocol drift could bypass versioning")

    screen = witness.get("provider_capability_screen")
    if not isinstance(screen, list):
        raise ValueError("provider capability screen is missing")
    providers = {
        item.get("provider_id"): item for item in screen if isinstance(item, Mapping)
    }
    if set(providers) != {"Nelson_Labs", "Pacific_BioLabs", "BioIVT"}:
        raise ValueError("provider screen set drifted")
    if providers["Nelson_Labs"].get("qualification_status") != (
        "rfq_required_exact_soluble_peptide_defibrinated_human_RBC_protocol_delivery_and_rights_unconfirmed"
    ):
        raise ValueError("Nelson Labs was claimed qualified without exact matrix evidence")
    if providers["Pacific_BioLabs"].get("qualification_status") != (
        "not_qualified_public_method_uses_rabbit_blood_exact_human_defibrinated_custom_method_unconfirmed"
    ):
        raise ValueError("Pacific BioLabs species mismatch was weakened")
    if providers["BioIVT"].get("provider_role") != (
        "prospective_human_blood_material_source_not_assay_provider"
    ):
        raise ValueError("BioIVT provider role drifted")
    if providers["BioIVT"].get("qualification_status") != (
        "material_source_only_defibrinated_processing_lot_ethics_transfer_and_data_rights_unconfirmed"
    ):
        raise ValueError("BioIVT material status was overclaimed")

    requirements = witness.get("mandatory_rfq_requirements")
    if not isinstance(requirements, Mapping):
        raise ValueError("mandatory RFQ requirements are missing")
    expected = {
        "final_RBC_volume_percent": 2.0,
        "buffer": "PBS_pH_7_4",
        "temperature_c": 37,
        "incubation_minutes": 60,
        "concentration_um": [1, 2, 4, 8, 16, 32, 64, 128, 256],
        "technical_replicates_per_donor_concentration": 3,
        "readout": "supernatant_absorbance_540_to_541_nm",
    }
    for field, value in expected.items():
        if requirements.get(field) != value:
            raise ValueError(f"{field} contract drifted")
    if requirements.get("raw_immutable_delivery_required") is not True:
        raise ValueError("raw immutable delivery is not mandatory")
    if requirements.get("donor_level_curves_retained") is not True:
        raise ValueError("donor-level evidence was collapsed")

    rights = witness.get("commercial_ethics_and_data_rights_acceptance")
    terms = rights.get("required_executed_agreement_terms") if isinstance(rights, Mapping) else None
    if not isinstance(terms, Mapping) or not terms:
        raise ValueError("commercial, ethics, and data-rights addendum is missing")
    if any(value != "contract_required_not_yet_confirmed" for value in terms.values()):
        raise ValueError("provider rights or ethics were overclaimed or weakened")

    pilot = witness.get("reference_pilot")
    if not isinstance(pilot, Mapping):
        raise ValueError("blinded reference pilot plan is missing")
    if pilot.get("reference_controls") != [
        "PBS_negative",
        "vehicle_matched_negative",
        "Triton_X100_1pct_complete_lysis",
        "melittin_reference_curve",
        "peptide_without_RBC_interference_blank",
    ]:
        raise ValueError("reference pilot controls drifted")
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
        "human_defibrinated_RBC_lots_qualified",
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
