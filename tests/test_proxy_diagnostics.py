import pytest

from pepagent.proxy_diagnostics import summarize_composition_pair_panel_robustness


def _result(sequence_sha256: str, score: float, loo_scores: list[float]) -> dict:
    return {
        "sequence_sha256": sequence_sha256,
        "target_specific_delta_nll": score,
        "stratified_panel_sensitivity": {
            "diagnostic_only": True,
            "leave_one_control_out": [
                {
                    "omitted_control_type": "unrelated",
                    "omitted_accession": f"decoy-{index}",
                    "omitted_sequence_sha256": f"decoy-sha-{index}",
                    "target_specific_delta_nll": value,
                }
                for index, value in enumerate(loo_scores)
            ],
        },
    }


def test_pair_robustness_uses_matching_omission_panels() -> None:
    result = summarize_composition_pair_panel_robustness(
        [
            {"sequence": "AACC", "sequence_sha256": "parent"},
            {
                "sequence": "CACA",
                "sequence_sha256": "scramble",
                "composition_reference_sequence_sha256": "parent",
            },
        ],
        [
            _result("parent", 0.7, [0.8, 0.6]),
            _result("scramble", 0.4, [0.5, 0.7]),
        ],
    )

    assert result["diagnostic_only"] is True
    assert result["independent_evidence"] is False
    assert result["pair_count"] == 1
    assert result["robust_positive_pair_count"] == 0
    assert result["pairs"][0]["primary_target_specific_delta_nll_gap"] == pytest.approx(
        0.3
    )
    assert result["pairs"][0]["same_omission_gap_min"] == pytest.approx(-0.1)
    assert result["pairs"][0]["same_omission_gap_max"] == pytest.approx(0.3)


def test_pair_robustness_rejects_non_composition_matched_scramble() -> None:
    with pytest.raises(ValueError, match="not composition-matched"):
        summarize_composition_pair_panel_robustness(
            [
                {"sequence": "AACC", "sequence_sha256": "parent"},
                {
                    "sequence": "AAAA",
                    "sequence_sha256": "scramble",
                    "composition_reference_sequence_sha256": "parent",
                },
            ],
            [
                _result("parent", 0.7, [0.8, 0.6]),
                _result("scramble", 0.4, [0.5, 0.3]),
            ],
        )


def test_pair_robustness_requires_matching_omission_panels() -> None:
    parent = _result("parent", 0.7, [0.8, 0.6])
    scramble = _result("scramble", 0.4, [0.5, 0.3])
    scramble["stratified_panel_sensitivity"]["leave_one_control_out"][0][
        "omitted_accession"
    ] = "different-decoy"

    with pytest.raises(ValueError, match="same omitted-control panels"):
        summarize_composition_pair_panel_robustness(
            [
                {"sequence": "AACC", "sequence_sha256": "parent"},
                {
                    "sequence": "CACA",
                    "sequence_sha256": "scramble",
                    "composition_reference_sequence_sha256": "parent",
                },
            ],
            [parent, scramble],
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_pair_robustness_rejects_non_finite_primary_scores(value: float) -> None:
    parent = _result("parent", value, [0.8, 0.6])
    scramble = _result("scramble", 0.4, [0.5, 0.3])

    with pytest.raises(ValueError, match="scores must be finite"):
        summarize_composition_pair_panel_robustness(
            [
                {"sequence": "AACC", "sequence_sha256": "parent"},
                {
                    "sequence": "CACA",
                    "sequence_sha256": "scramble",
                    "composition_reference_sequence_sha256": "parent",
                },
            ],
            [parent, scramble],
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_pair_robustness_rejects_non_finite_loo_scores(value: float) -> None:
    parent = _result("parent", 0.7, [0.8, value])
    scramble = _result("scramble", 0.4, [0.5, 0.3])

    with pytest.raises(ValueError, match="scores must be finite"):
        summarize_composition_pair_panel_robustness(
            [
                {"sequence": "AACC", "sequence_sha256": "parent"},
                {
                    "sequence": "CACA",
                    "sequence_sha256": "scramble",
                    "composition_reference_sequence_sha256": "parent",
                },
            ],
            [parent, scramble],
        )


def test_pair_robustness_rejects_self_referencing_scramble() -> None:
    with pytest.raises(ValueError, match="cannot reference itself"):
        summarize_composition_pair_panel_robustness(
            [
                {
                    "sequence": "AACC",
                    "sequence_sha256": "candidate",
                    "composition_reference_sequence_sha256": "candidate",
                }
            ],
            [_result("candidate", 0.7, [0.8, 0.6])],
        )


def test_pair_robustness_rejects_scramble_chain_as_parent() -> None:
    with pytest.raises(ValueError, match="must be an unpaired parent"):
        summarize_composition_pair_panel_robustness(
            [
                {"sequence": "AACC", "sequence_sha256": "root"},
                {
                    "sequence": "CACA",
                    "sequence_sha256": "middle",
                    "composition_reference_sequence_sha256": "root",
                },
                {
                    "sequence": "ACAC",
                    "sequence_sha256": "leaf",
                    "composition_reference_sequence_sha256": "middle",
                },
            ],
            [
                _result("root", 0.7, [0.8, 0.6]),
                _result("middle", 0.4, [0.5, 0.3]),
                _result("leaf", 0.2, [0.3, 0.1]),
            ],
        )
