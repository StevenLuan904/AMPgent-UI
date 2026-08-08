import hashlib

import pytest

from pepagent.model_workers.pepmlm_target_proxy_cli import (
    _require_sequence_sha256,
    summarize_stratified_target_specific_delta_nll,
    summarize_target_specific_delta_nll,
    target_panel_sha256,
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
    assert result["panel_sensitivity"] == {
        "method": "leave_one_decoy_out",
        "diagnostic_only": True,
        "target_specific_delta_nll_min": 1.5,
        "target_specific_delta_nll_max": 2.5,
        "target_specific_delta_nll_range": 1.0,
        "leave_one_decoy_out": [
            {
                "omitted_accession": None,
                "omitted_sequence_sha256": None,
                "omitted_control_type": "unrelated",
                "decoy_target_nll_median": 4.5,
                "target_specific_delta_nll": 2.5,
            },
            {
                "omitted_accession": None,
                "omitted_sequence_sha256": None,
                "omitted_control_type": "composition_shuffle",
                "decoy_target_nll_median": 4.0,
                "target_specific_delta_nll": 2.0,
            },
            {
                "omitted_accession": None,
                "omitted_sequence_sha256": None,
                "omitted_control_type": "composition_shuffle",
                "decoy_target_nll_median": 3.5,
                "target_specific_delta_nll": 1.5,
            },
        ],
    }
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


def test_stratified_target_specific_delta_nll_balances_control_types() -> None:
    peptide = {"sequence": "AAAA", "sequence_sha256": "peptide-sha"}
    target_scores = [
        {"control_type": "primary", "conditional_nll": 2.0},
        {"control_type": "unrelated", "conditional_nll": 3.0},
        {"control_type": "unrelated", "conditional_nll": 5.0},
        {"control_type": "composition_shuffle", "conditional_nll": 8.0},
        {"control_type": "composition_shuffle", "conditional_nll": 10.0},
        {"control_type": "composition_shuffle", "conditional_nll": 12.0},
    ]

    result = summarize_stratified_target_specific_delta_nll(peptide, target_scores)

    assert result["control_type_nll_medians"] == {
        "unrelated": 4.0,
        "composition_shuffle": 10.0,
    }
    assert result["stratified_control_target_nll"] == 7.0
    assert result["target_specific_delta_nll"] == 5.0
    assert result["pooled_v21_compatible_secondary"] == {
        "method": "median(all_control_target_nlls)-primary_target_nll",
        "secondary": True,
        "decoy_target_nll_median": 8.0,
        "target_specific_delta_nll": 6.0,
    }
    assert result["control_type_sensitivity"] == {
        "method": "control_type_median_minus_primary_target_nll",
        "diagnostic_only": True,
        "target_specific_delta_nll_by_control_type": {
            "unrelated": 2.0,
            "composition_shuffle": 8.0,
        },
        "target_specific_delta_nll_min": 2.0,
        "target_specific_delta_nll_max": 8.0,
        "target_specific_delta_nll_range": 6.0,
    }
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


@pytest.mark.parametrize(
    ("target_scores", "message"),
    [
        (
            [
                {"control_type": "primary", "conditional_nll": 2.0},
                {"control_type": "unrelated", "conditional_nll": 3.0},
                {"control_type": "unrelated", "conditional_nll": 4.0},
                {"control_type": "composition_shuffle", "conditional_nll": 5.0},
            ],
            "at least two composition_shuffle",
        ),
        (
            [
                {"control_type": "primary", "conditional_nll": 2.0},
                {"control_type": "unrelated", "conditional_nll": 3.0},
                {"control_type": "unrelated", "conditional_nll": 4.0},
                {"control_type": "composition_shuffle", "conditional_nll": 5.0},
                {"control_type": "composition_shuffle", "conditional_nll": 6.0},
                {"control_type": "hard_negative", "conditional_nll": 7.0},
            ],
            "unknown control_type",
        ),
        (
            [
                {"control_type": "primary", "conditional_nll": 2.0},
                {"control_type": "unrelated", "conditional_nll": 3.0},
                {"control_type": "unrelated", "conditional_nll": float("nan")},
                {"control_type": "composition_shuffle", "conditional_nll": 5.0},
                {"control_type": "composition_shuffle", "conditional_nll": 6.0},
            ],
            "conditional_nll must be finite",
        ),
    ],
)
def test_stratified_target_specific_delta_nll_rejects_invalid_panels(
    target_scores: list[dict[str, object]], message: str
) -> None:
    peptide = {"sequence": "AAAA", "sequence_sha256": "peptide-sha"}

    with pytest.raises(ValueError, match=message):
        summarize_stratified_target_specific_delta_nll(peptide, target_scores)


def test_proxy_rejects_sequence_hash_mismatch() -> None:
    with pytest.raises(ValueError, match="sequence_sha256 mismatch"):
        _require_sequence_sha256("AAAA", "0" * 64, field="peptide")


def test_target_panel_hash_is_order_and_content_sensitive() -> None:
    panel = [
        {
            "accession": "primary",
            "sequence": "AAAA",
            "sequence_sha256": hashlib.sha256(b"AAAA").hexdigest(),
            "control_type": "primary",
        },
        {
            "accession": "decoy",
            "sequence": "CCCC",
            "sequence_sha256": hashlib.sha256(b"CCCC").hexdigest(),
            "control_type": "unrelated",
        },
    ]

    assert target_panel_sha256(panel) != target_panel_sha256(list(reversed(panel)))
