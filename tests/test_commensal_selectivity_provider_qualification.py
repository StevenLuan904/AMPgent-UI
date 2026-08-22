from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.commensal_selectivity_provider_qualification import (
    acceptance_artifacts,
    validate_commensal_selectivity_provider_qualification,
)


def _witness() -> dict:
    root = Path(__file__).parents[1]
    return json.loads(
        (
            root
            / "config/enterprise/commensal_selectivity_provider_rfq_v39.json"
        ).read_text(encoding="utf-8")
    )


def test_frozen_commensal_selectivity_provider_rfq_is_valid() -> None:
    witness = _witness()
    assert validate_commensal_selectivity_provider_qualification(
        witness
    ) == acceptance_artifacts(witness)


def test_provider_rfq_rejects_strain_and_lane_drift() -> None:
    witness = _witness()
    witness["frozen_contract_binding"]["strain_identities"].pop()
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="strain panel"):
        validate_commensal_selectivity_provider_qualification(witness)

    witness = _witness()
    witness["frozen_contract_binding"]["assay_lane_ids"][0] = "generic_MIC"
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="assay lanes"):
        validate_commensal_selectivity_provider_qualification(witness)


def test_provider_rfq_rejects_rights_and_execution_overclaims() -> None:
    witness = _witness()
    witness["commercial_and_data_rights_acceptance"][
        "required_executed_agreement_terms"
    ]["derivative_model_training_and_internal_weights"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="rights were overclaimed"):
        validate_commensal_selectivity_provider_qualification(witness)

    witness = _witness()
    witness["decision"]["provider_qualified"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="overclaims execution"):
        validate_commensal_selectivity_provider_qualification(witness)


def test_provider_rfq_rejects_candidate_leakage_or_premature_pilot() -> None:
    witness = _witness()
    witness["reference_pilot"]["current_773_candidates_included"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="leak"):
        validate_commensal_selectivity_provider_qualification(witness)

    witness = _witness()
    witness["decision"]["reference_pilot_started"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="overclaims execution"):
        validate_commensal_selectivity_provider_qualification(witness)
