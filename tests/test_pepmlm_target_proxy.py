import hashlib

import pytest

from pepagent.model_workers.pepmlm_target_proxy_cli import (
    _require_sequence_sha256,
    summarize_target_specific_delta_nll,
)


def test_target_specific_delta_nll_is_decoy_median_minus_primary() -> None:
    peptide = {
        "sequence": "WKLVDIAGRVARNHG",
        "sequence_sha256": hashlib.sha256(b"WKLVDIAGRVARNHG").hexdigest(),
    }
    result = summarize_target_specific_delta_nll(
        peptide,
        [
            {"control_type": "primary", "conditional_nll": 2.0},
            {"control_type": "unrelated", "conditional_nll": 3.0},
            {"control_type": "composition_shuffle", "conditional_nll": 4.0},
            {"control_type": "composition_shuffle", "conditional_nll": 5.0},
        ],
    )

    assert result["decoy_target_nll_median"] == 4.0
    assert result["target_specific_delta_nll"] == 2.0
    assert result["interpretation"] == {
        "direction": "higher_values_rank_as_more_primary_target_conditioned",
        "confidence": "low",
        "admission_status": "out_of_domain",
        "evidence_kind": "sequence_binding_proxy",
        "rank_only": True,
        "is_binding_probability": False,
        "is_affinity": False,
        "may_override_structure_evidence": False,
        "independence": "not_independent_from_pepmlm_generation_or_ppl",
    }


def test_target_specific_delta_nll_requires_controls() -> None:
    peptide = {"sequence": "AAAA", "sequence_sha256": "ignored"}
    with pytest.raises(ValueError, match="at least two decoy"):
        summarize_target_specific_delta_nll(
            peptide,
            [
                {"control_type": "primary", "conditional_nll": 2.0},
                {"control_type": "unrelated", "conditional_nll": 3.0},
            ],
        )


def test_proxy_rejects_sequence_hash_mismatch() -> None:
    with pytest.raises(ValueError, match="sequence_sha256 mismatch"):
        _require_sequence_sha256("AAAA", "0" * 64, field="peptide")
