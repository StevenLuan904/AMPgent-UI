import json
from pathlib import Path

import pytest

from pepagent.hemolysis_provider_triage import (
    acceptance_artifacts,
    validate_hemolysis_provider_triage,
)


def _witness() -> dict:
    return {
        "schema_version": "ampgent.hemolysis-second-family-triage.1",
        "candidates": [
            {
                "candidate_id": "model",
                "primary_sources": ["https://example.test/model"],
                "training_sources": ["shared"],
                "independence_status": "failed_shared_public_source_family",
                "commercial_internal_execution_confirmed": True,
                "immutable_weights_available": True,
                "probability_semantics_reproducible": True,
                "sequence_first_compatible": True,
                "qualification_status": "rejected",
                "reason_codes": ["same_evidence_family"],
            }
        ],
        "decision": {
            "outcome": "no_public_candidate_qualified",
            "selected_provider_id": None,
            "safety_gate_lowered": False,
        },
    }


def test_acceptance_hashes_are_deterministic_and_fail_closed() -> None:
    witness = _witness()
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    assert validate_hemolysis_provider_triage(witness) == witness["acceptance_artifacts"]
    witness["candidates"][0]["qualification_status"] = "qualified"
    with pytest.raises(ValueError, match="not rejected"):
        validate_hemolysis_provider_triage(witness)


def test_frozen_second_family_triage_witness_is_valid() -> None:
    root = Path(__file__).parents[1]
    witness = json.loads(
        (
            root
            / "config/enterprise/independent_hemolysis_second_family_triage_v39.json"
        ).read_text(encoding="utf-8")
    )
    validate_hemolysis_provider_triage(witness)
