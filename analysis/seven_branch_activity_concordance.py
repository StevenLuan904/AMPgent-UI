from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ACTIVITY_METRICS = (
    ("amp_read_log10_mic_um", "min"),
    ("llamp_log10_mic_um", "min"),
    ("macrel_amp_probability", "max"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _average_ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        average = (start + 1 + end) / 2
        for position in range(start, end):
            ranks[indexed[position][0]] = average
        start = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True))
    left_ss = sum((a - left_mean) ** 2 for a in left)
    right_ss = sum((b - right_mean) ** 2 for b in right)
    denominator = math.sqrt(left_ss * right_ss)
    return numerator / denominator if denominator else None


def _spearman(left: list[float], right: list[float]) -> float | None:
    return _pearson(_average_ranks(left), _average_ranks(right))


def _benefit_percentiles(
    rows: list[dict[str, str]], metric: str, direction: str
) -> dict[str, float]:
    values = [float(row[metric]) for row in rows]
    ranks = _average_ranks(values)
    denominator = max(len(rows) - 1, 1)
    result: dict[str, float] = {}
    for row, rank in zip(rows, ranks, strict=True):
        percentile = (rank - 1) / denominator
        if direction == "min":
            percentile = 1 - percentile
        result[row["candidate_id"]] = percentile
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()

    with args.input_csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1900:
        raise ValueError("expected the frozen 1900-candidate delivery")

    by_branch: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_branch[row["branch_key"]].append(row)

    enriched: list[dict[str, str]] = []
    summaries: dict[str, object] = {}
    for branch, branch_rows in sorted(by_branch.items()):
        percentiles = {
            metric: _benefit_percentiles(branch_rows, metric, direction)
            for metric, direction in ACTIVITY_METRICS
        }
        classifications: Counter[str] = Counter()
        for row in branch_rows:
            scores = [percentiles[metric][row["candidate_id"]] for metric, _ in ACTIVITY_METRICS]
            top_quartile_count = sum(value >= 0.75 for value in scores)
            optimistic_count = sum(value >= 0.9 for value in scores)
            weak_count = sum(value < 0.5 for value in scores)
            if top_quartile_count == 3:
                classification = "three_model_consensus_top_quartile"
            elif optimistic_count == 1 and weak_count >= 2:
                classification = "single_model_optimistic_conflict"
            elif top_quartile_count >= 2:
                classification = "two_model_consensus_top_quartile"
            else:
                classification = "mixed_or_midrange"
            item = dict(row)
            for metric, _ in ACTIVITY_METRICS:
                item[f"{metric}__within_branch_benefit_percentile"] = (
                    f"{percentiles[metric][row['candidate_id']]:.6f}"
                )
            item["activity_model_concordance"] = classification
            item["activity_models_top_quartile_count"] = str(top_quartile_count)
            enriched.append(item)
            classifications[classification] += 1

        metric_values = {
            metric: [float(row[metric]) for row in branch_rows] for metric, _ in ACTIVITY_METRICS
        }
        summaries[branch] = {
            "n": len(branch_rows),
            "classification_counts": dict(sorted(classifications.items())),
            "tier_by_classification": dict(
                sorted(
                    Counter(
                        f"{row['priority_tier']}|{row['activity_model_concordance']}"
                        for row in enriched
                        if row["branch_key"] == branch
                    ).items()
                )
            ),
            "spearman": {
                "amp_read_vs_llamp": _spearman(
                    metric_values["amp_read_log10_mic_um"],
                    metric_values["llamp_log10_mic_um"],
                ),
                "amp_read_vs_macrel_probability": _spearman(
                    [-value for value in metric_values["amp_read_log10_mic_um"]],
                    metric_values["macrel_amp_probability"],
                ),
                "llamp_vs_macrel_probability": _spearman(
                    [-value for value in metric_values["llamp_log10_mic_um"]],
                    metric_values["macrel_amp_probability"],
                ),
            },
        }

    classification_order = {
        "three_model_consensus_top_quartile": 0,
        "two_model_consensus_top_quartile": 1,
        "mixed_or_midrange": 2,
        "single_model_optimistic_conflict": 3,
    }
    enriched.sort(
        key=lambda row: (
            row["branch_key"],
            classification_order[row["activity_model_concordance"]],
            int(row["delivery_rank"]),
        )
    )
    output_csv = args.output_prefix.with_suffix(".csv")
    conflict_csv = args.output_prefix.with_name(args.output_prefix.name + "_conflicts").with_suffix(
        ".csv"
    )
    consensus_a_csv = args.output_prefix.with_name(
        args.output_prefix.name + "_consensus_a"
    ).with_suffix(".csv")
    output_json = args.output_prefix.with_suffix(".json")
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(enriched[0])
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(enriched)
    conflicts = [
        row
        for row in enriched
        if row["activity_model_concordance"] == "single_model_optimistic_conflict"
    ]
    with conflict_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(conflicts)
    consensus_a = [
        row
        for row in enriched
        if row["priority_tier"] == "A_pareto_no_audit_flag"
        and row["activity_model_concordance"] == "three_model_consensus_top_quartile"
    ]
    with consensus_a_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(consensus_a)
    payload = {
        "schema_version": "ampgent.seven-branch-activity-concordance.1",
        "created_at": datetime.now(UTC).isoformat(),
        "source_csv": str(args.input_csv),
        "source_sha256": _sha256(args.input_csv),
        "candidate_count": len(rows),
        "method": {
            "cohort": "within_branch_frozen_delivery",
            "benefit_percentile": "higher_is_better",
            "consensus_top_quartile": "all_three_models_at_or_above_branch_p75",
            "single_model_conflict": "exactly_one_model_at_or_above_p90_and_two_below_median",
            "semantics": "model_concordance_audit_not_experimental_activity",
        },
        "branch_summary": summaries,
        "conflict_count": len(conflicts),
        "output_csv_sha256": _sha256(output_csv),
        "conflict_csv": str(conflict_csv),
        "conflict_csv_sha256": _sha256(conflict_csv),
        "consensus_a_count": len(consensus_a),
        "consensus_a_csv": str(consensus_a_csv),
        "consensus_a_csv_sha256": _sha256(consensus_a_csv),
    }
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
