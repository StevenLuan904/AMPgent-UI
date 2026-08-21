from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.amp_likeness_contract import (
    acceptance_artifacts,
    validate_amp_likeness_contract,
)


def _frozen_contract() -> dict:
    root = Path(__file__).parents[1]
    return json.loads(
        (root / "config/enterprise/amp_likeness_reference_model_contract_v39.json").read_text(
            encoding="utf-8"
        )
    )


def test_frozen_amp_likeness_contract_is_valid() -> None:
    contract = _frozen_contract()
    assert validate_amp_likeness_contract(contract) == acceptance_artifacts(contract)


def test_contract_rejects_database_membership_random_negatives_and_candidate_leakage() -> None:
    contract = _frozen_contract()
    contract["endpoint_semantics"]["database_membership_alone_is_positive_label"] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="database membership"):
        validate_amp_likeness_contract(contract)

    contract = _frozen_contract()
    contract["endpoint_semantics"][
        "unreviewed_random_sequences_allowed_as_primary_negatives"
    ] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="random sequences"):
        validate_amp_likeness_contract(contract)

    contract = _frozen_contract()
    contract["split_contract"]["current_candidates_used_to_define_operating_point"] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="operating point"):
        validate_amp_likeness_contract(contract)


def test_contract_rejects_universal_threshold_and_completion_overclaim() -> None:
    contract = _frozen_contract()
    contract["endpoint_semantics"]["universal_MIC_threshold_allowed"] = True
    contract["acceptance_artifacts"] = acceptance_artifacts(contract)
    with pytest.raises(ValueError, match="universal MIC threshold"):
        validate_amp_likeness_contract(contract)

    contract = _frozen_contract()
    contract["decision"]["model_trained"] = True
    with pytest.raises(ValueError, match="overclaims completion"):
        validate_amp_likeness_contract(contract)
