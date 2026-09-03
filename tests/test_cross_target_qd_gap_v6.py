def test_v6_operator_exists():
    from pathlib import Path

    assert (Path(__file__).parents[1] / "analysis" / "cross_target_qd_gap_v6.py").exists()
