from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.resistance_propensity_contract import (
    acceptance_artifacts,
    validate_resistance_propensity_contract,
)


def _frozen_contract() -> dict:
    root = Path(__file__).parents[1]
    return json.loads(
        (
            root / "config/enterprise/resistance_propensity_contract_v39.json"
        ).read_text(encoding="utf-8")
    )


def test_frozen_resistance_propensity_contract_is_valid() -> None:
    contract = _frozen_contract()
    assert validate_resistance_propensity_contract(contract) == acceptance_artifacts(
        contract
    )


def test_contract_rejects_candidate_leakage_and_extinction_revival() -> None:
    contract = _frozen_contract()
    contract["reference_and_candidate_blinding_plan"][
        "current_773_candidates_excluded_from_model_fitting"
    ] = False
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="could influence fitting"):
        validate_resistance_propensity_contract(contract)

    contract = _frozen_contract()
    contract["serial_passage_matrix"][
        "extinct_lineages_may_be_resurrected_for_primary_analysis"
    ] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="silently resurrected"):
        validate_resistance_propensity_contract(contract)


def test_contract_rejects_endpoint_collapse_and_overclaiming() -> None:
    contract = _frozen_contract()
    contract["raw_measurement_schema"][
        "MIC_shift_and_population_extinction_stored_separately"
    ] = False
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="collapsed"):
        validate_resistance_propensity_contract(contract)

    contract = _frozen_contract()
    contract["decision"]["measurements_collected"] = True
    with pytest.raises(ValueError, match="overclaims completion"):
        validate_resistance_propensity_contract(contract)
