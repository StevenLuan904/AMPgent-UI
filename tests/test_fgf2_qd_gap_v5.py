import importlib.util
import sys
from pathlib import Path


def _module():
    root = Path(__file__).parents[1]
    sys.path.insert(0, str(root / "analysis"))
    spec = importlib.util.spec_from_file_location(
        "fgf2_v5", root / "analysis" / "fgf2_qd_gap_v5.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v5_requires_archive_elite_and_support():
    module = _module()
    archive = {
        "elites": [{"candidate_id": "keep", "sequence": "ACDEFGHIKL"}],
        "empty_cell_ids": [],
        "policy": {},
    }
    rows = [
        {
            "candidate_id": "keep", "sequence": "ACDEFGHIKL", "display_eligible": "true",
            "formal_12_complete": "true", "activity_model_support_count_calibrated": "2",
        },
        {
            "candidate_id": "drop", "sequence": "ACDEFGHIKL", "display_eligible": "true",
            "formal_12_complete": "true", "activity_model_support_count_calibrated": "1",
        },
    ]
    assert [row["candidate_id"] for row in module.eligible_parents(rows, archive)] == ["keep"]


def test_v5_arm_limits_and_distinct_modes():
    module = _module()
    archive = {"empty_cell_ids": [], "policy": {}}
    assert module.generate_arm([], set(), archive, "A_one_aa", 16) == []
    assert module.generate_arm([], set(), archive, "B_two_aa", 16) == []
