from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ESSENTIAL_DIRECTIONS = {
    "valid_unique_yield": "maximize",
    "median_macrel_amp_probability": "maximize",
    "median_macrel_hemolysis_probability": "minimize",
    "median_toxinpred3_ml_score": "minimize",
    "median_llamp_predicted_mic_um": "minimize",
    "median_amp_read_predicted_mic_um": "minimize",
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _aggregate(rows: list[dict[str, str]], expected_per_seed: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["generator_id"]].append(row)
    aggregated: list[dict[str, Any]] = []
    for generator_id, generator_rows in grouped.items():
        selected_counts = [int(row["selected_count"]) for row in generator_rows]
        qualified = len(generator_rows) == 3 and all(
            count == expected_per_seed for count in selected_counts
        )
        result: dict[str, Any] = {
            "generator_id": generator_id,
            "seed_count": len(generator_rows),
            "selected_total": sum(selected_counts),
            "qualification_status": (
                "qualified" if qualified else "disqualified_short_or_incomplete_cohort"
            ),
        }
        for metric in ESSENTIAL_DIRECTIONS:
            values = [float(row[metric]) for row in generator_rows if row.get(metric)]
            result[metric.removeprefix("median_")] = (
                statistics.median(values) if len(values) == len(generator_rows) else None
            )
        aggregated.append(result)
    return aggregated


def _dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    no_worse = True
    strictly_better = False
    for metric, direction in ESSENTIAL_DIRECTIONS.items():
        key = metric.removeprefix("median_")
        left_value = left.get(key)
        right_value = right.get(key)
        if left_value is None or right_value is None:
            return False
        if direction == "maximize":
            no_worse &= left_value >= right_value
            strictly_better |= left_value > right_value
        else:
            no_worse &= left_value <= right_value
            strictly_better |= left_value < right_value
    return no_worse and strictly_better


def build_append_only_scorecard(
    reference_rows: list[dict[str, str]],
    challenger_rows: list[dict[str, str]],
    *,
    expected_per_seed: int,
    challenger_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    references = _aggregate(reference_rows, expected_per_seed)
    challengers = _aggregate(challenger_rows, expected_per_seed)
    if len(challengers) != 1 or challengers[0]["generator_id"] != challenger_id:
        raise ValueError("scorecard requires exactly the preregistered challenger")
    challenger = challengers[0]
    qualified_references = [
        row for row in references if row["qualification_status"] == "qualified"
    ]
    dominated_by = [
        row["generator_id"]
        for row in qualified_references
        if _dominates(row, challenger)
    ]
    challenger_qualified = challenger["qualification_status"] == "qualified"
    promoted = challenger_qualified and not dominated_by
    scorecard: list[dict[str, Any]] = []
    for row in references:
        scorecard.append(
            {
                **row,
                "track": "frozen_v23_reference",
                "decision_tier": (
                    "frozen_qualified_reference"
                    if row["qualification_status"] == "qualified"
                    else "not_rankable_on_profile_metrics"
                ),
                "dominated_by": "",
                "forced_rank": "none",
            }
        )
    scorecard.append(
        {
            **challenger,
            "track": "append_only_v25_challenger",
            "decision_tier": (
                "promoted_non_dominated_challenger"
                if promoted
                else "not_promoted_dominated_or_unqualified"
            ),
            "dominated_by": ";".join(dominated_by),
            "forced_rank": "none",
        }
    )
    decision = {
        "challenger_id": challenger_id,
        "challenger_qualified": challenger_qualified,
        "dominated_by_qualified_references": dominated_by,
        "promoted_to_followup_validation": promoted,
        "claim_scope": "soft_prediction_non_dominated_profile_only",
        "weighted_composite_used": False,
        "forced_rank": False,
        "reference_results_rewritten": False,
        "experimental_evidence": False,
        "acea_binding_evidence": False,
    }
    return scorecard, decision


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-seed-summary", type=Path, required=True)
    parser.add_argument("--challenger-seed-summary", type=Path, required=True)
    parser.add_argument("--challenger-id", required=True)
    parser.add_argument("--expected-per-seed", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decision-output", type=Path, required=True)
    args = parser.parse_args()
    scorecard, decision = build_append_only_scorecard(
        _read_csv(args.reference_seed_summary),
        _read_csv(args.challenger_seed_summary),
        expected_per_seed=args.expected_per_seed,
        challenger_id=args.challenger_id,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(scorecard[0]))
        writer.writeheader()
        writer.writerows(scorecard)
    args.decision_output.write_text(
        json.dumps(decision, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(decision, ensure_ascii=False))


if __name__ == "__main__":
    main()
