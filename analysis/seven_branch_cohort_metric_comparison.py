from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

METRICS = (
    ("amp_read_log10_mic_um", "numeric", "min"),
    ("llamp_log10_mic_um", "numeric", "min"),
    ("macrel_amp_probability", "numeric", "max"),
    ("toxinpred3_label", "label", "Non-Toxin"),
    ("toxinpred3_hybrid_score", "numeric", "min"),
    ("macrel_hemolysis_label", "label", "low"),
    ("macrel_hemolysis_probability", "numeric", "min"),
    ("net_charge_ph7_4", "numeric", "descriptive"),
    ("hydrophobic_ratio_modlamp", "numeric", "descriptive"),
    ("hydrophobic_moment_eisenberg", "numeric", "max"),
    ("maximum_hydrophobic_run", "numeric", "min"),
    ("guruprasad_instability_index", "numeric", "min_non_gating"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _numeric_summary(
    cohort: str,
    rows: list[dict[str, str]],
    metric: str,
    direction: str,
) -> dict[str, object]:
    valid = [row for row in rows if row.get(metric, "") != ""]
    values = [float(row[metric]) for row in valid]
    minimum = min(valid, key=lambda row: float(row[metric]))
    maximum = max(valid, key=lambda row: float(row[metric]))
    if direction in {"min", "min_non_gating"}:
        best, worst = minimum, maximum
    elif direction == "max":
        best, worst = maximum, minimum
    else:
        best = worst = None
    unit = next((row.get(f"{metric}__unit", "") for row in valid if row.get(f"{metric}__unit")), "")
    return {
        "cohort": cohort,
        "cohort_n": len(rows),
        "metric": metric,
        "kind": "numeric",
        "direction": direction,
        "unit": unit,
        "valid_n": len(valid),
        "missing_n": len(rows) - len(valid),
        "ood_n": sum(row.get(f"{metric}__ood", "").lower() == "true" for row in valid),
        "min": float(minimum[metric]),
        "min_candidate_id": minimum["candidate_id"],
        "min_sequence": minimum["sequence"],
        "max": float(maximum[metric]),
        "max_candidate_id": maximum["candidate_id"],
        "max_sequence": maximum["sequence"],
        "best_candidate_id": best["candidate_id"] if best else "",
        "best_sequence": best["sequence"] if best else "",
        "worst_candidate_id": worst["candidate_id"] if worst else "",
        "worst_sequence": worst["sequence"] if worst else "",
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "standard_deviation": statistics.pstdev(values),
        "p10": _quantile(values, 0.10),
        "p25": _quantile(values, 0.25),
        "p75": _quantile(values, 0.75),
        "p90": _quantile(values, 0.90),
        "categories_json": "",
    }


def _label_summary(
    cohort: str,
    rows: list[dict[str, str]],
    metric: str,
    favorable: str,
) -> dict[str, object]:
    valid = [row for row in rows if row.get(metric, "") != ""]
    categories = Counter(row[metric] for row in valid)
    return {
        "cohort": cohort,
        "cohort_n": len(rows),
        "metric": metric,
        "kind": "label",
        "direction": favorable,
        "unit": "",
        "valid_n": len(valid),
        "missing_n": len(rows) - len(valid),
        "ood_n": sum(row.get(f"{metric}__ood", "").lower() == "true" for row in valid),
        "min": "",
        "min_candidate_id": "",
        "min_sequence": "",
        "max": "",
        "max_candidate_id": "",
        "max_sequence": "",
        "best_candidate_id": "",
        "best_sequence": "",
        "worst_candidate_id": "",
        "worst_sequence": "",
        "mean": "",
        "median": "",
        "standard_deviation": "",
        "p10": "",
        "p25": "",
        "p75": "",
        "p90": "",
        "categories_json": json.dumps(
            {
                label: {
                    "count": count,
                    "percentage": count / len(rows) * 100 if rows else 0,
                }
                for label, count in sorted(categories.items())
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()

    with args.input_csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1900:
        raise ValueError("expected the frozen 1900-candidate delivery")

    cohorts: dict[str, list[dict[str, str]]] = {
        "all_1900": rows,
        "priority_a": [row for row in rows if row["priority_tier"] == "A_pareto_no_audit_flag"],
        "three_model_consensus_a": [
            row
            for row in rows
            if row["priority_tier"] == "A_pareto_no_audit_flag"
            and row["activity_model_concordance"] == "three_model_consensus_top_quartile"
        ],
        "robust_core": [row for row in rows if row["robust_priority_core"] == "True"],
        "balanced_activity_safety": [
            row
            for row in rows
            if row["priority_tier"] == "A_pareto_no_audit_flag"
            and int(row["activity_models_top_quartile_count"]) >= 2
            and float(row["macrel_hemolysis_probability"]) <= 0.20
            and float(row["toxinpred3_hybrid_score"]) <= 0.10
            and float(row["maximum_hydrophobic_run"]) <= 6
        ],
    }
    branches = sorted({row["branch_key"] for row in rows})
    for branch in branches:
        branch_rows = [row for row in rows if row["branch_key"] == branch]
        cohorts[f"branch:{branch}:all"] = branch_rows
        cohorts[f"branch:{branch}:robust_core"] = [
            row for row in branch_rows if row["robust_priority_core"] == "True"
        ]

    summaries: list[dict[str, object]] = []
    for cohort, cohort_rows in cohorts.items():
        if not cohort_rows:
            continue
        for metric, kind, direction in METRICS:
            if kind == "numeric":
                summaries.append(_numeric_summary(cohort, cohort_rows, metric, direction))
            else:
                summaries.append(_label_summary(cohort, cohort_rows, metric, direction))

    output_csv = args.output_prefix.with_suffix(".csv")
    output_json = args.output_prefix.with_suffix(".json")
    balanced_csv = args.output_prefix.with_name(
        args.output_prefix.name + "_balanced_activity_safety"
    ).with_suffix(".csv")
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    balanced = cohorts["balanced_activity_safety"]
    with balanced_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(balanced)
    payload = {
        "schema_version": "ampgent.seven-branch-cohort-metric-comparison.1",
        "created_at": datetime.now(UTC).isoformat(),
        "source_csv": str(args.input_csv),
        "source_sha256": _sha256(args.input_csv),
        "cohort_counts": {name: len(items) for name, items in cohorts.items()},
        "metric_count": len(METRICS),
        "metric_summary_rows": len(summaries),
        "semantics": "model_predictions_and_sequence_descriptors_not_wet_lab_measurements",
        "balanced_activity_safety_definition": {
            "priority_tier": "A_pareto_no_audit_flag",
            "activity_models_top_quartile_count": ">=2",
            "macrel_hemolysis_probability": "<=0.20",
            "toxinpred3_hybrid_score": "<=0.10",
            "maximum_hydrophobic_run": "<=6",
            "semantics": "optional_balanced_challenger_cohort_not_new_admission_gate",
        },
        "balanced_csv": str(balanced_csv),
        "balanced_csv_sha256": _sha256(balanced_csv),
        "summaries": summaries,
        "output_csv_sha256": _sha256(output_csv),
    }
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in payload.items() if key != "summaries"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
