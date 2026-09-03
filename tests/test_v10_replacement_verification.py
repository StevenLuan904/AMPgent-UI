import json
from pathlib import Path


def test_v10_replacements_are_quality_gated_and_better_than_incumbents():
    root = Path(__file__).parents[1]
    data = json.loads(
        (
            root / "reports/target_agnostic_source_graft_v10_20260903/replacement_verification.json"
        ).read_text()
    )
    assert data["replacement_count"] == 8
    assert data["all_display_support_ge_2"] is True
    assert data["all_child_quality_gt_incumbent"] is True
