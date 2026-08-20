from __future__ import annotations

import pytest

from pepagent.toxicity_endpoint_contract import qualify_toxicity_endpoint


def test_hemolysis_is_kept_in_its_own_evidence_domain() -> None:
    result = qualify_toxicity_endpoint(
        endpoint_kind="hemolysis",
        experimentally_measured=True,
    )
    assert result.evidence_domain == "hemolysis"
    assert result.formal_safety_gate_candidate is True
    assert "cytotoxicity" not in result.permitted_usage


def test_generic_toxin_label_cannot_be_a_formal_safety_gate() -> None:
    result = qualify_toxicity_endpoint(
        endpoint_kind="database_toxin_keyword",
        experimentally_measured=False,
    )
    assert result.formal_safety_gate_candidate is False
    assert result.permitted_usage == "shadow_diagnostic_only"
    assert result.blockers == ("endpoint_not_assay_specific",)


def test_complete_mammalian_cell_assay_is_a_validation_candidate() -> None:
    result = qualify_toxicity_endpoint(
        endpoint_kind="mammalian_cell_cytotoxicity",
        experimentally_measured=True,
        cell_line_present=True,
        concentration_present=True,
        exposure_duration_present=True,
        assay_endpoint_present=True,
    )
    assert result.evidence_domain == "mammalian_cytotoxicity"
    assert result.formal_safety_gate_candidate is True
    assert result.blockers == ()


def test_mammalian_cell_assay_missing_dose_and_duration_fails_closed() -> None:
    result = qualify_toxicity_endpoint(
        endpoint_kind="mammalian_cell_cytotoxicity",
        experimentally_measured=True,
        cell_line_present=True,
        assay_endpoint_present=True,
    )
    assert result.formal_safety_gate_candidate is False
    assert result.blockers == ("concentration_missing", "exposure_duration_missing")


def test_empty_endpoint_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="endpoint_kind must be non-empty"):
        qualify_toxicity_endpoint(endpoint_kind=" ", experimentally_measured=False)
