from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.commensal_selectivity_contract import (
    acceptance_artifacts,
    validate_commensal_selectivity_contract,
)


def _frozen_contract() -> dict:
    root = Path(__file__).parents[1]
    return json.loads(
        (
            root
            / "config/enterprise/commensal_selectivity_assay_contract_v39.json"
        ).read_text(encoding="utf-8")
    )


def test_frozen_commensal_selectivity_contract_is_valid() -> None:
    contract = _frozen_contract()
    assert validate_commensal_selectivity_contract(contract) == acceptance_artifacts(
        contract
    )


def test_contract_rejects_species_wide_role_cross_medium_pooling_and_leakage() -> None:
    contract = _frozen_contract()
    contract["strain_and_condition_matrix"][
        "species_name_alone_defines_pathogen_or_commensal_role"
    ] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="species-wide"):
        validate_commensal_selectivity_contract(contract)

    contract = _frozen_contract()
    contract["strain_and_condition_matrix"]["cross_medium_MIC_ratio_or_pooling_allowed"] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="incomparable media"):
        validate_commensal_selectivity_contract(contract)

    contract = _frozen_contract()
    contract["reference_and_candidate_blinding_plan"][
        "current_773_candidates_used_for_operating_point"
    ] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="could influence"):
        validate_commensal_selectivity_contract(contract)


def test_contract_rejects_endpoint_collapse_and_completion_overclaim() -> None:
    contract = _frozen_contract()
    contract["raw_measurement_schema"]["single_weighted_selectivity_score_allowed"] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="weighted selectivity"):
        validate_commensal_selectivity_contract(contract)

    contract = _frozen_contract()
    contract["decision"]["measurements_collected"] = True
    with pytest.raises(ValueError, match="overclaims completion"):
        validate_commensal_selectivity_contract(contract)
