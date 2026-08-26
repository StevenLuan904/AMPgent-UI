from __future__ import annotations

import importlib.util
import random
from pathlib import Path
from types import ModuleType


def _load_review_module() -> ModuleType:
    path = Path(__file__).parents[1] / "analysis" / "quality_continuation_review.py"
    spec = importlib.util.spec_from_file_location("_quality_continuation_review", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REVIEW = _load_review_module()
PARETO_OBJECTIVES = REVIEW.PARETO_OBJECTIVES
_dominates = REVIEW._dominates
_pareto_front = REVIEW._pareto_front


def _naive_front(rows: list[dict[str, str]]) -> set[str]:
    return {
        candidate["candidate_id"]
        for candidate in rows
        if not any(
            other["candidate_id"] != candidate["candidate_id"]
            and _dominates(other, candidate)
            for other in rows
        )
    }


def test_vectorized_pareto_matches_naive_reference() -> None:
    rng = random.Random(20260826)
    rows = [
        {
            "candidate_id": f"candidate-{ordinal}",
            **{
                metric: str(rng.randint(0, 12) / 4)
                for metric, _direction in PARETO_OBJECTIVES
            },
        }
        for ordinal in range(250)
    ]

    assert _pareto_front(rows) == _naive_front(rows)


def test_vectorized_pareto_keeps_equal_points_and_handles_empty_input() -> None:
    equal_metrics = {metric: "1.0" for metric, _direction in PARETO_OBJECTIVES}
    rows = [
        {"candidate_id": "equal-a", **equal_metrics},
        {"candidate_id": "equal-b", **equal_metrics},
    ]

    assert _pareto_front([]) == set()
    assert _pareto_front(rows) == {"equal-a", "equal-b"}
