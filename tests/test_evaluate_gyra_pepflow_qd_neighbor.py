def test_qd_neighbor_module_exposes_evaluator():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parents[1] / "analysis"))
    from evaluate_gyra_pepflow_qd_neighbor import evaluate

    assert callable(evaluate)
