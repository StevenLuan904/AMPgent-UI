from __future__ import annotations

import json
from pathlib import Path


def test_vegfa_pepflow_close_separates_runtime_and_quality_gates() -> None:
    root = Path(__file__).resolve().parents[1]
    receipt = json.loads(
        (root / "reports/vegfa_pepflow_peripheral_rescue_20260904_v1/close_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["formal12"]["score_all_complete"] == 6
    assert receipt["calibration"]["support_ge_2_count"] == 0
    assert receipt["qd"]["new_cell_count"] == 0
    assert receipt["qd"]["valid_cell_coverage"] == 0.0
    assert receipt["qd"]["archive_qd_score"] is None
    assert receipt["qd"]["archive_baseline"]["not_batch_contribution"] is True
    assert receipt["materialization"]["executed"] is False
    for name in ("apex", "peptiverse"):
        assert receipt["challenger"][name]["applicability_status"] == "runtime_unavailable"
        assert receipt["challenger"][name]["conflict_status"] == "not_assessed"
