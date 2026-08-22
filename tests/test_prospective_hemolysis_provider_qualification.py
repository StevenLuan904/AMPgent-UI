from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.prospective_hemolysis_provider_qualification import (
    acceptance_artifacts,
    validate_prospective_hemolysis_provider_qualification,
)


def _witness() -> dict:
    return json.loads(
        (
            Path(__file__).parents[1]
            / "config/enterprise/prospective_hemolysis_provider_rfq_v39.json"
        ).read_text(encoding="utf-8")
    )


def test_frozen_prospective_hemolysis_provider_rfq_is_valid() -> None:
    witness = _witness()
    assert validate_prospective_hemolysis_provider_qualification(
        witness
    ) == acceptance_artifacts(witness)


def test_provider_rfq_rejects_matrix_and_protocol_drift() -> None:
    witness = _witness()
    witness["frozen_contract_binding"]["primary_matrix"]["blood_preparation"] = "citrated"
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="human-RBC matrix"):
        validate_prospective_hemolysis_provider_qualification(witness)

    witness = _witness()
    witness["mandatory_rfq_requirements"]["incubation_minutes"] = 30
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="incubation_minutes"):
        validate_prospective_hemolysis_provider_qualification(witness)


def test_provider_rfq_rejects_species_and_rights_overclaims() -> None:
    witness = _witness()
    witness["provider_capability_screen"][1]["qualification_status"] = "qualified"
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="species mismatch"):
        validate_prospective_hemolysis_provider_qualification(witness)

    witness = _witness()
    witness["commercial_ethics_and_data_rights_acceptance"][
        "required_executed_agreement_terms"
    ]["derivative_model_training_and_internal_weights"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="rights or ethics"):
        validate_prospective_hemolysis_provider_qualification(witness)


def test_provider_rfq_rejects_candidate_leakage_or_premature_pilot() -> None:
    witness = _witness()
    witness["reference_pilot"]["current_773_candidates_included"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="leak"):
        validate_prospective_hemolysis_provider_qualification(witness)

    witness = _witness()
    witness["decision"]["reference_pilot_started"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="overclaims execution"):
        validate_prospective_hemolysis_provider_qualification(witness)
