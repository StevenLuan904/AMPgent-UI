from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.solubility_aggregation_contract import (
    acceptance_artifacts,
    validate_solubility_aggregation_contract,
)


def _frozen_contract() -> dict:
    root = Path(__file__).parents[1]
    return json.loads(
        (
            root
            / "config/enterprise/solubility_aggregation_contract_v39.json"
        ).read_text(encoding="utf-8")
    )


def test_frozen_solubility_aggregation_contract_is_valid() -> None:
    contract = _frozen_contract()
    assert validate_solubility_aggregation_contract(contract) == acceptance_artifacts(
        contract
    )


def test_contract_rejects_candidate_leakage_and_filtration() -> None:
    contract = _frozen_contract()
    contract["reference_and_candidate_blinding_plan"][
        "current_773_candidates_excluded_from_fitting"
    ] = False
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="could influence fitting"):
        validate_solubility_aggregation_contract(contract)

    contract = _frozen_contract()
    contract["condition_matrix"]["premeasurement_filtration_allowed"] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="hide aggregates"):
        validate_solubility_aggregation_contract(contract)


def test_contract_rejects_endpoint_collapse_and_overclaiming() -> None:
    contract = _frozen_contract()
    contract["raw_measurement_schema"][
        "turbidity_dls_and_soluble_mass_stored_separately"
    ] = False
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="endpoints were collapsed"):
        validate_solubility_aggregation_contract(contract)

    contract = _frozen_contract()
    contract["decision"]["measurements_collected"] = True
    with pytest.raises(ValueError, match="overclaims completion"):
        validate_solubility_aggregation_contract(contract)
