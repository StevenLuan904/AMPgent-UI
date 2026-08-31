from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    analysis_dir = Path(__file__).resolve().parents[1] / "analysis"
    sys.path.insert(0, str(analysis_dir))
    spec = importlib.util.spec_from_file_location(
        "_autoresearch_safety_rescue_variants",
        analysis_dir / "autoresearch_safety_rescue_variants.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unsafe_parent_selection_can_target_source_family() -> None:
    module = _load_module()
    rows = [
        {
            "source_family_key_80_80": "target-gap",
            "activity_model_support_count_calibrated": "2",
            "display_eligible": "false",
            "sequence": "KKKK",
        },
        {
            "source_family_key_80_80": "other-gap",
            "activity_model_support_count_calibrated": "3",
            "display_eligible": "false",
            "sequence": "RRRR",
        },
        {
            "source_family_key_80_80": "target-gap",
            "activity_model_support_count_calibrated": "2",
            "display_eligible": "true",
            "sequence": "KRKR",
        },
    ]

    selected = module._select_unsafe_parents(rows, family_keys=("target-gap",))

    assert [row["sequence"] for row in selected] == ["KKKK"]
