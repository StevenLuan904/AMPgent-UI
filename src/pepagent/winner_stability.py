from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

import numpy as np
import pandas as pd

Direction = Literal["min", "max"]


def compute_weight_perturbation_stability(
    frame: pd.DataFrame,
    *,
    metric_directions: Mapping[str, Direction],
    top_k: int,
    trials: int = 2_000,
    random_seed: int = 20260824,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Measure ranking stability under many non-negative objective weightings.

    Metrics are converted to within-cohort percentile utilities so incompatible native
    units are never mixed. The result is diagnostic: it does not create an admission
    threshold or replace the underlying metric evidence.
    """

    if frame.empty:
        raise ValueError("winner stability requires a non-empty cohort")
    if not metric_directions:
        raise ValueError("winner stability requires at least one directional metric")
    if top_k <= 0 or trials <= 0:
        raise ValueError("top_k and trials must be positive")
    required = {"candidate_id", "sequence", *metric_directions}
    missing_columns = required - set(frame.columns)
    if missing_columns:
        raise ValueError(f"winner stability missing columns: {sorted(missing_columns)}")
    if frame["candidate_id"].duplicated().any():
        raise ValueError("winner stability candidate identities must be unique")

    numeric = frame[list(metric_directions)].apply(pd.to_numeric, errors="coerce")
    complete = numeric.notna().all(axis=1)
    cohort = frame.loc[complete].reset_index(drop=True).copy()
    numeric = numeric.loc[complete].reset_index(drop=True)
    if cohort.empty:
        raise ValueError("winner stability has no complete directional-score rows")

    utilities: list[np.ndarray] = []
    for metric, direction in metric_directions.items():
        values = numeric[metric]
        ascending = direction == "max"
        percentile = values.rank(method="average", pct=True, ascending=ascending)
        utilities.append(percentile.to_numpy(dtype=float))
    utility_matrix = np.column_stack(utilities)
    candidate_count, metric_count = utility_matrix.shape
    effective_top_k = min(top_k, candidate_count)

    rng = np.random.default_rng(random_seed)
    weights = rng.dirichlet(np.ones(metric_count), size=trials)
    weighted_scores = utility_matrix @ weights.T
    top_indices = np.argpartition(
        weighted_scores, candidate_count - effective_top_k, axis=0
    )[candidate_count - effective_top_k :]
    top_counts = np.bincount(top_indices.ravel(), minlength=candidate_count)
    winner_counts = np.bincount(
        np.argmax(weighted_scores, axis=0), minlength=candidate_count
    )

    leave_one_out_counts = np.zeros(candidate_count, dtype=int)
    if metric_count == 1:
        leave_one_out_counts[:] = 1
        leave_one_out_denominator = 1
    else:
        leave_one_out_denominator = metric_count
        for omitted in range(metric_count):
            keep = [index for index in range(metric_count) if index != omitted]
            score = utility_matrix[:, keep].mean(axis=1)
            selected = np.argpartition(score, candidate_count - effective_top_k)[
                candidate_count - effective_top_k :
            ]
            leave_one_out_counts[selected] += 1

    equal_weight_score = utility_matrix.mean(axis=1)
    equal_weight_rank = pd.Series(equal_weight_score).rank(
        method="min", ascending=False
    )
    result = cohort[["candidate_id", "sequence"]].copy()
    for optional in ("status", "round", "generator_id", "run_id"):
        if optional in cohort.columns:
            result[optional] = cohort[optional]
    result["metric_count"] = metric_count
    result["equal_weight_score"] = equal_weight_score
    result["equal_weight_rank"] = equal_weight_rank.astype(int)
    result["random_weight_top_k_probability"] = top_counts / trials
    result["random_weight_winner_probability"] = winner_counts / trials
    result["leave_one_metric_out_top_k_fraction"] = (
        leave_one_out_counts / leave_one_out_denominator
    )
    result["minimum_metric_percentile"] = utility_matrix.min(axis=1)
    result["maximum_metric_percentile"] = utility_matrix.max(axis=1)
    result["metric_percentile_stddev"] = utility_matrix.std(axis=1, ddof=0)
    result = result.sort_values(
        [
            "random_weight_top_k_probability",
            "leave_one_metric_out_top_k_fraction",
            "equal_weight_score",
            "candidate_id",
        ],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)

    historical_core = (
        result["status"].eq("mature_core")
        if "status" in result.columns
        else pd.Series(False, index=result.index)
    )
    summary = {
        "schema_version": "ampgent.winner-stability.1",
        "candidate_count": candidate_count,
        "excluded_incomplete_count": int((~complete).sum()),
        "metric_directions": dict(metric_directions),
        "top_k": effective_top_k,
        "random_weight_trials": trials,
        "random_seed": random_seed,
        "historical_mature_core_count": int(historical_core.sum()),
        "historical_core_top_k_probability_ge_0_8": int(
            (historical_core & result["random_weight_top_k_probability"].ge(0.8)).sum()
        ),
        "historical_core_top_k_probability_lt_0_2": int(
            (historical_core & result["random_weight_top_k_probability"].lt(0.2)).sum()
        ),
        "historical_core_leave_one_out_fraction_1": int(
            (historical_core & result["leave_one_metric_out_top_k_fraction"].eq(1.0)).sum()
        ),
        "diagnostic_only": True,
    }
    return result, summary
