from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "analysis" / "autoresearch_lineage_probe.py"
    spec = importlib.util.spec_from_file_location("_lineage_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_targeted_lineage_probe_accepts_selected_branch_only() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "analysis" / "autoresearch_lineage_probe.py"
    ).read_text(encoding="utf-8")

    assert "source_branches != set(active_branches)" in source
    assert "requires all six target branches" not in source
    assert "args.minimum_calibrated_support" in source
    assert 'args.output_dir / "source_cohort.csv"' in source
    assert "historical_sequences = set(input_sequences)" in source
    assert "args.include_postgresql_history" in source
    assert "sequence_hashes.update(postgresql_hashes)" in source


def test_prefer_full_support_removes_weaker_rows_only_from_rescued_families() -> None:
    module = _load_module()
    rows = [
        {
            "family_key_80_80": "rescued",
            "activity_model_support_count_calibrated": "3",
            "sequence": "AAA",
        },
        {
            "family_key_80_80": "rescued",
            "activity_model_support_count_calibrated": "2",
            "sequence": "BBB",
        },
        {
            "family_key_80_80": "remaining",
            "activity_model_support_count_calibrated": "2",
            "sequence": "CCC",
        },
    ]

    selected = module._prefer_full_support_within_families(rows)

    assert [row["sequence"] for row in selected] == ["AAA", "CCC"]


def test_unresolved_family_parent_ids_are_complete_and_deterministic() -> None:
    module = _load_module()
    rows = [
        {
            "family_key_80_80": "gap-b",
            "activity_model_support_count_calibrated": "2",
            "sequence_sha256": "b" * 64,
        },
        {
            "family_key_80_80": "resolved",
            "activity_model_support_count_calibrated": "3",
            "sequence_sha256": "c" * 64,
        },
        {
            "family_key_80_80": "gap-a",
            "activity_model_support_count_calibrated": "2",
            "sequence_sha256": "a" * 64,
        },
        {
            "family_key_80_80": "resolved",
            "activity_model_support_count_calibrated": "2",
            "sequence_sha256": "d" * 64,
        },
    ]

    assert module._unresolved_family_parent_ids(rows) == {
        "gap-a": ("a" * 64,),
        "gap-b": ("b" * 64,),
    }
