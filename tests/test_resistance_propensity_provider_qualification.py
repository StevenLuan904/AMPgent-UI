from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.resistance_propensity_provider_qualification import (
    acceptance_artifacts,
    validate_resistance_propensity_provider_qualification,
)


def _witness() -> dict:
    return json.loads(
        (
            Path(__file__).parents[1]
            / "config/enterprise/resistance_propensity_provider_rfq_v39.json"
        ).read_text(encoding="utf-8")
    )


def test_frozen_resistance_propensity_provider_rfq_is_valid() -> None:
    witness = _witness()
    assert validate_resistance_propensity_provider_qualification(
        witness
    ) == acceptance_artifacts(witness)


def test_resistance_rfq_rejects_organism_lineage_or_passage_drift() -> None:
    witness = _witness()
    witness["frozen_contract_binding"]["organism_strains"][0] = "Escherichia_coli_K12"
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="organism matrix"):
        validate_resistance_propensity_provider_qualification(witness)

    witness = _witness()
    witness["frozen_contract_binding"]["total_passages"] = 20
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="total_passages"):
        validate_resistance_propensity_provider_qualification(witness)


def test_resistance_rfq_rejects_provider_or_rights_overclaim() -> None:
    witness = _witness()
    witness["provider_capability_screen"][0]["qualification_status"] = "qualified"
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="Evotec"):
        validate_resistance_propensity_provider_qualification(witness)

    witness = _witness()
    witness["biosafety_genomic_data_and_commercial_rights_acceptance"][
        "required_executed_agreement_terms"
    ]["derivative_model_training_and_internal_weights"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="rights"):
        validate_resistance_propensity_provider_qualification(witness)


def test_resistance_rfq_rejects_candidate_leakage_or_premature_pilot() -> None:
    witness = _witness()
    witness["reference_pilot"]["current_773_candidates_included"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="leak"):
        validate_resistance_propensity_provider_qualification(witness)

    witness = _witness()
    witness["decision"]["reference_pilot_started"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="overclaims execution"):
        validate_resistance_propensity_provider_qualification(witness)
