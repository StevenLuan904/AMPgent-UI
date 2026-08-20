from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pepagent.synthesis_feasibility import assess_synthesis_feasibility


@pytest.fixture
def ruleset() -> dict:
    path = (
        Path(__file__).parents[1]
        / "config/enterprise/synthesis_feasibility_ruleset_v39.yaml"
    )
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_routine_sequence_has_no_review_flags(ruleset: dict) -> None:
    result = assess_synthesis_feasibility("GIKAEGKKLSEKRLQKKA", ruleset=ruleset)
    assert result["in_applicability_domain"] is True
    assert result["status"] == "routine_sequence_only_spps"
    assert result["review_flags"] == []


def test_difficult_sequence_flags_are_transparent_and_nonrejecting(ruleset: dict) -> None:
    result = assess_synthesis_feasibility("QCCDGMMMMMWW", ruleset=ruleset)
    assert result["status"] == "manual_review_required"
    assert set(result["review_flags"]) == {
        "long_identical_residue_run",
        "long_hydrophobic_run",
        "high_hydrophobic_fraction",
        "multiple_cysteines_require_disulfide_strategy",
        "multiple_oxidation_sensitive_residues",
        "aspartimide_susceptible_motif",
        "n_terminal_glutamine_pyroglutamate_review",
    }


def test_out_of_domain_sequence_is_not_silently_scored(ruleset: dict) -> None:
    result = assess_synthesis_feasibility("ACDEFG", ruleset=ruleset)
    assert result["in_applicability_domain"] is False
    assert result["status"] == "out_of_domain_manual_review_required"


def test_noncanonical_sequence_fails_closed(ruleset: dict) -> None:
    with pytest.raises(ValueError, match="non-canonical"):
        assess_synthesis_feasibility("ACDEFGHX", ruleset=ruleset)


def test_ruleset_identity_is_bound_to_every_assessment(ruleset: dict) -> None:
    first = assess_synthesis_feasibility("GIKAEGKKLSEKRLQKKA", ruleset=ruleset)
    changed = dict(ruleset)
    changed["review_thresholds"] = dict(ruleset["review_thresholds"])
    changed["review_thresholds"]["hydrophobic_run_review_at"] = 4
    second = assess_synthesis_feasibility("GIKAEGKKLSEKRLQKKA", ruleset=changed)
    assert first["ruleset_sha256"] != second["ruleset_sha256"]
