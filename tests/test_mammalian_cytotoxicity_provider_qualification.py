from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.mammalian_cytotoxicity_provider_qualification import (
    acceptance_artifacts,
    validate_mammalian_cytotoxicity_provider_qualification,
)


def _witness() -> dict:
    return json.loads(
        (
            Path(__file__).parents[1]
            / "config/enterprise/mammalian_cytotoxicity_provider_rfq_v39.json"
        ).read_text(encoding="utf-8")
    )


def test_frozen_mammalian_cytotoxicity_provider_rfq_is_valid() -> None:
    witness = _witness()
    assert validate_mammalian_cytotoxicity_provider_qualification(
        witness
    ) == acceptance_artifacts(witness)


def test_provider_rfq_rejects_cell_donor_and_protocol_drift() -> None:
    witness = _witness()
    witness["frozen_contract_binding"]["primary_cell_models"][0][
        "maximum_passage_number"
    ] = 5
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="cell panel or passage"):
        validate_mammalian_cytotoxicity_provider_qualification(witness)

    witness = _witness()
    witness["mandatory_rfq_requirements"]["exposure_time_hours"] = [24, 72]
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="exposure_time_hours"):
        validate_mammalian_cytotoxicity_provider_qualification(witness)


def test_provider_rfq_rejects_rights_and_provider_overclaim() -> None:
    witness = _witness()
    witness["commercial_and_data_rights_acceptance"][
        "required_executed_agreement_terms"
    ]["derivative_model_training_and_internal_weights"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="rights were overclaimed"):
        validate_mammalian_cytotoxicity_provider_qualification(witness)

    witness = _witness()
    witness["decision"]["provider_qualified"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="overclaims execution"):
        validate_mammalian_cytotoxicity_provider_qualification(witness)


def test_provider_rfq_rejects_candidate_leakage_or_premature_pilot() -> None:
    witness = _witness()
    witness["reference_pilot"]["current_773_candidates_included"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="leak"):
        validate_mammalian_cytotoxicity_provider_qualification(witness)

    witness = _witness()
    witness["decision"]["reference_pilot_started"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="overclaims execution"):
        validate_mammalian_cytotoxicity_provider_qualification(witness)
