from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.mammalian_cytotoxicity_contract import (
    acceptance_artifacts,
    validate_mammalian_cytotoxicity_contract,
)


def _frozen_contract() -> dict:
    root = Path(__file__).parents[1]
    return json.loads(
        (
            root
            / "config/enterprise/prospective_mammalian_cytotoxicity_contract_v39.json"
        ).read_text(encoding="utf-8")
    )


def test_frozen_mammalian_cytotoxicity_contract_is_valid() -> None:
    contract = _frozen_contract()
    assert validate_mammalian_cytotoxicity_contract(contract) == acceptance_artifacts(
        contract
    )


def test_contract_rejects_surrogate_panel_endpoint_collapse_and_candidate_leakage() -> None:
    contract = _frozen_contract()
    contract["cell_exposure_matrix"][
        "immortalized_or_cancer_cell_line_may_replace_primary_panel"
    ] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="surrogate line"):
        validate_mammalian_cytotoxicity_contract(contract)

    contract = _frozen_contract()
    contract["cell_exposure_matrix"][
        "viability_and_membrane_damage_may_be_collapsed"
    ] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="could be collapsed"):
        validate_mammalian_cytotoxicity_contract(contract)

    contract = _frozen_contract()
    contract["reference_and_candidate_blinding_plan"][
        "current_773_candidates_used_for_operating_point"
    ] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="could influence"):
        validate_mammalian_cytotoxicity_contract(contract)


def test_contract_rejects_interference_as_safe_and_completion_overclaim() -> None:
    contract = _frozen_contract()
    contract["raw_measurement_schema"]["censoring_rules"][
        "interfering_readout"
    ] = "imputed_safe"
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="imputed safe"):
        validate_mammalian_cytotoxicity_contract(contract)

    contract = _frozen_contract()
    contract["decision"]["measurements_collected"] = True
    with pytest.raises(ValueError, match="overclaims completion"):
        validate_mammalian_cytotoxicity_contract(contract)
