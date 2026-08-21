from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.stability_assay_contract import (
    acceptance_artifacts,
    validate_serum_protease_stability_contract,
)


def _frozen_contract() -> dict:
    root = Path(__file__).parents[1]
    return json.loads(
        (root / "config/enterprise/serum_protease_stability_contract_v39.json").read_text(
            encoding="utf-8"
        )
    )


def test_frozen_stability_contract_is_valid() -> None:
    contract = _frozen_contract()
    assert validate_serum_protease_stability_contract(contract) == acceptance_artifacts(
        contract
    )


def test_contract_rejects_candidate_leakage_and_serum_drift() -> None:
    contract = _frozen_contract()
    contract["reference_and_candidate_blinding_plan"][
        "current_773_candidates_excluded_from_fitting"
    ] = False
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="could influence fitting"):
        validate_serum_protease_stability_contract(contract)

    contract = _frozen_contract()
    contract["serum_and_protease_condition_matrix"]["primary_human_serum_lane"][
        "temperature_c"
    ] = 25
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="condition drifted"):
        validate_serum_protease_stability_contract(contract)


def test_contract_rejects_fragment_inflation_and_overclaiming() -> None:
    contract = _frozen_contract()
    contract["timecourse_lcms_measurement_and_censoring_schema"][
        "fragment_signal_counts_as_intact_parent"
    ] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="inflate intact parent"):
        validate_serum_protease_stability_contract(contract)

    contract = _frozen_contract()
    contract["decision"]["measurements_collected"] = True
    with pytest.raises(ValueError, match="overclaims completion"):
        validate_serum_protease_stability_contract(contract)
