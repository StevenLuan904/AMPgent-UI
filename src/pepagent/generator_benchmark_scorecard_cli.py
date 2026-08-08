from __future__ import annotations

import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

PROFILE_METRICS = (
    "valid_unique_yield",
    "median_macrel_amp_probability",
    "median_macrel_hemolysis_probability",
    "median_toxinpred3_ml_score",
    "median_llamp_predicted_mic_um",
    "median_amp_read_predicted_mic_um",
    "median_net_charge_ph7_4",
    "median_hydrophobic_moment_eisenberg",
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def build_scorecard(rows: list[dict[str, str]], expected_per_seed: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["generator_id"]].append(row)

    scorecard: list[dict[str, Any]] = []
    for generator_id, generator_rows in grouped.items():
        selected_counts = [int(row["selected_count"]) for row in generator_rows]
        qualified = all(count == expected_per_seed for count in selected_counts)
        result: dict[str, Any] = {
            "generator_id": generator_id,
            "seed_count": len(generator_rows),
            "selected_total": sum(selected_counts),
            "qualification_status": (
                "qualified" if qualified else "disqualified_short_cohort"
            ),
            "decision_tier": (
                "pareto_front_selected_for_deep_iteration"
                if qualified
                else "not_rankable_on_profile_metrics"
            ),
            "forced_rank": "none",
        }
        for metric in PROFILE_METRICS:
            values = [float(row[metric]) for row in generator_rows if row.get(metric)]
            result[metric.removeprefix("median_")] = (
                statistics.median(values) if values else None
            )
        scorecard.append(result)
    return sorted(
        scorecard,
        key=lambda row: (
            row["qualification_status"] != "qualified",
            -float(row["valid_unique_yield"] or 0.0),
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-per-seed", type=int, required=True)
    args = parser.parse_args()
    rows = build_scorecard(_read_csv(args.seed_summary), args.expected_per_seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
