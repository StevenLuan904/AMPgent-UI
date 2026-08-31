from __future__ import annotations

from pathlib import Path


def test_targeted_lineage_probe_accepts_selected_branch_only() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "analysis"
        / "autoresearch_lineage_probe.py"
    ).read_text(encoding="utf-8")

    assert "source_branches != set(active_branches)" in source
    assert "requires all six target branches" not in source
