from __future__ import annotations

from pepagent.generator_challenger_scorecard_cli import (
    build_append_only_scorecard,
)


def _row(generator_id: str, seed: int, **overrides: float) -> dict[str, str]:
    values: dict[str, float] = {
        "valid_unique_yield": 0.8,
        "median_macrel_amp_probability": 0.6,
        "median_macrel_hemolysis_probability": 0.4,
        "median_toxinpred3_ml_score": 0.4,
        "median_llamp_predicted_mic_um": 20.0,
        "median_amp_read_predicted_mic_um": 20.0,
    }
    values.update(overrides)
    return {
        "generator_id": generator_id,
        "seed": str(seed),
        "selected_count": "100",
        **{key: str(value) for key, value in values.items()},
    }


def test_non_dominated_challenger_is_promoted_without_forced_rank() -> None:
    references = [
        _row("safe", seed, median_macrel_amp_probability=0.4)
        for seed in (1, 2, 3)
    ]
    challenger = [
        _row(
            "challenger",
            seed,
            median_macrel_amp_probability=0.8,
            median_macrel_hemolysis_probability=0.8,
        )
        for seed in (4, 5, 6)
    ]
    scorecard, decision = build_append_only_scorecard(
        references,
        challenger,
        expected_per_seed=100,
        challenger_id="challenger",
    )
    assert decision["promoted_to_followup_validation"] is True
    assert decision["forced_rank"] is False
    assert scorecard[-1]["decision_tier"] == "promoted_non_dominated_challenger"


def test_dominated_challenger_is_not_promoted() -> None:
    references = [_row("reference", seed) for seed in (1, 2, 3)]
    challenger = [
        _row(
            "challenger",
            seed,
            valid_unique_yield=0.7,
            median_macrel_amp_probability=0.5,
            median_macrel_hemolysis_probability=0.5,
            median_toxinpred3_ml_score=0.5,
            median_llamp_predicted_mic_um=30.0,
            median_amp_read_predicted_mic_um=30.0,
        )
        for seed in (4, 5, 6)
    ]
    _, decision = build_append_only_scorecard(
        references,
        challenger,
        expected_per_seed=100,
        challenger_id="challenger",
    )
    assert decision["promoted_to_followup_validation"] is False
    assert decision["dominated_by_qualified_references"] == ["reference"]
