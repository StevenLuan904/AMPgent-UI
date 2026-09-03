import json
from pathlib import Path


def test_v9_parent_mismatch_matrix_fails_closed():
    root = Path(__file__).parents[1]
    receipt = json.loads(
        (root / "reports/target_agnostic_v10_diagnostic_20260903/diagnostic_receipt.json").read_text()
    )
    assert receipt["v9_unique_parent_count"] == 16
    assert receipt["branch_mismatch_count"] == 16
    assert receipt["calibration_domain_mismatch_count"] == 16
    assert receipt["target_agnostic_support_ge_2"] == 0
