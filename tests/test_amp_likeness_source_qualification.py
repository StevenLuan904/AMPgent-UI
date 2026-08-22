from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.amp_likeness_source_qualification import (
    acceptance_artifacts,
    validate_amp_likeness_source_qualification,
)


def _witness() -> dict:
    root = Path(__file__).parents[1]
    return json.loads(
        (
            root
            / "config/enterprise/amp_likeness_source_rights_endpoint_triage_v39.json"
        ).read_text(encoding="utf-8")
    )


def test_frozen_amp_likeness_source_qualification_is_valid() -> None:
    witness = _witness()
    assert validate_amp_likeness_source_qualification(witness) == acceptance_artifacts(
        witness
    )


def test_source_qualification_rejects_rights_and_endpoint_overclaims() -> None:
    witness = _witness()
    witness["source_qualification"][1][
        "written_commercial_derivative_model_permission_required"
    ] = False
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="written permission"):
        validate_amp_likeness_source_qualification(witness)

    witness = _witness()
    witness["source_qualification"][3]["primary_negative_endpoint_status"] = (
        "eligible_from_missing_annotation"
    )
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="negative label"):
        validate_amp_likeness_source_qualification(witness)


def test_source_qualification_rejects_candidate_leakage_and_completion_claims() -> None:
    witness = _witness()
    witness["candidate_exclusion_and_acquisition_boundary"][
        "current_773_candidates_used_for_source_selection"
    ] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="leak candidates"):
        validate_amp_likeness_source_qualification(witness)

    witness = _witness()
    witness["decision"]["model_trained"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="overclaims completion"):
        validate_amp_likeness_source_qualification(witness)
