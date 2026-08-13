from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
AGENT_RULES = ROOT / "AGENTS.md"


def test_agent_style_distinguishes_numeric_tolerance_from_integrity() -> None:
    rules = AGENT_RULES.read_text(encoding="utf-8")
    assert "absolute_difference <= 1e-8" in rules
    assert "relative_difference <= 1e-6" in rules
    assert "A difference around `1e-15`" in rules
    assert "Require exact equality for identities and integrity" in rules
    assert "Do not use fail-closed as a reflex" in rules


def test_retired_metric_and_prohibited_host_are_explicit() -> None:
    rules = AGENT_RULES.read_text(encoding="utf-8")
    normalized_rules = " ".join(rules.split())
    assert "AMPlify is retired from this project by user decision" in rules
    assert "Host `192.168.99.32` is temporarily prohibited by user decision" in rules
    assert "This whole-host prohibition explicitly includes GPU3 and GPU4" in rules
    assert (
        "it is not permission to contact the host or use a different GPU there"
        in normalized_rules
    )
