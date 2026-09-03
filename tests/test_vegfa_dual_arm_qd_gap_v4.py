from pathlib import Path


def test_v4_operator_and_test_exist():
    root = Path(__file__).parents[1]
    assert (root / "analysis" / "vegfa_dual_arm_qd_gap_v4.py").exists()
