def test_v7_operator_exists():
    from pathlib import Path

    assert (Path(__file__).parents[1] / "analysis" / "qd_new_family_block_graft_v7.py").exists()
