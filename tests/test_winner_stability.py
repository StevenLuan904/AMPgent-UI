from __future__ import annotations

import pandas as pd

from pepagent.winner_stability import compute_weight_perturbation_stability


def test_dominating_candidate_is_stable_winner() -> None:
    frame = pd.DataFrame(
        [
            {"candidate_id": "a", "sequence": "AAAA", "low": 0.0, "high": 1.0},
            {"candidate_id": "b", "sequence": "BBBB", "low": 1.0, "high": 0.0},
            {"candidate_id": "c", "sequence": "CCCC", "low": 2.0, "high": -1.0},
        ]
    )
    result, summary = compute_weight_perturbation_stability(
        frame,
        metric_directions={"low": "min", "high": "max"},
        top_k=1,
        trials=200,
        random_seed=7,
    )
    winner = result.set_index("candidate_id").loc["a"]
    assert winner["random_weight_top_k_probability"] == 1.0
    assert winner["random_weight_winner_probability"] == 1.0
    assert winner["leave_one_metric_out_top_k_fraction"] == 1.0
    assert summary["candidate_count"] == 3


def test_stability_is_deterministic_and_excludes_incomplete_rows() -> None:
    frame = pd.DataFrame(
        [
            {"candidate_id": "a", "sequence": "AAAA", "low": 0.0, "high": 0.2},
            {"candidate_id": "b", "sequence": "BBBB", "low": 0.5, "high": 0.9},
            {"candidate_id": "c", "sequence": "CCCC", "low": None, "high": 1.0},
        ]
    )
    first, first_summary = compute_weight_perturbation_stability(
        frame,
        metric_directions={"low": "min", "high": "max"},
        top_k=1,
        trials=500,
        random_seed=17,
    )
    second, second_summary = compute_weight_perturbation_stability(
        frame,
        metric_directions={"low": "min", "high": "max"},
        top_k=1,
        trials=500,
        random_seed=17,
    )
    pd.testing.assert_frame_equal(first, second)
    assert first_summary == second_summary
    assert first_summary["excluded_incomplete_count"] == 1
    assert set(first["candidate_id"]) == {"a", "b"}
