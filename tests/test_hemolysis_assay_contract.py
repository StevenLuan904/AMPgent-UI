from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.hemolysis_assay_contract import (
    acceptance_artifacts,
    validate_prospective_hemolysis_assay_contract,
)


def _frozen_contract() -> dict:
    root = Path(__file__).parents[1]
    return json.loads(
        (
            root
            / "config/enterprise/prospective_hemolysis_assay_contract_v39.json"
        ).read_text(encoding="utf-8")
    )


def test_frozen_prospective_hemolysis_contract_is_valid() -> None:
    contract = _frozen_contract()
    assert validate_prospective_hemolysis_assay_contract(contract) == acceptance_artifacts(
        contract
    )


def test_contract_rejects_candidate_leakage_and_condition_drift() -> None:
    contract = _frozen_contract()
    contract["sequence_acquisition_and_overlap_exclusion"][
        "current_ampgent_candidates_excluded_from_fitting"
    ] = False
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="could influence fitting"):
        validate_prospective_hemolysis_assay_contract(contract)

    contract = _frozen_contract()
    contract["rbc_species_donor_and_assay_condition_matrix"][
        "frozen_assay_conditions"
    ]["incubation_minutes"] = 30
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="condition drifted"):
        validate_prospective_hemolysis_assay_contract(contract)


def test_contract_rejects_silent_clipping_and_overclaiming_measurements() -> None:
    contract = _frozen_contract()
    contract["raw_measurement_and_censoring_schema"][
        "negative_or_above_100_values_are_preserved"
    ] = False
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="silently clipped"):
        validate_prospective_hemolysis_assay_contract(contract)

    contract = _frozen_contract()
    contract["decision"]["measurements_collected"] = True
    with pytest.raises(ValueError, match="overclaims completion"):
        validate_prospective_hemolysis_assay_contract(contract)
