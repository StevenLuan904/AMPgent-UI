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

import numpy as np

NUMERIC_METRICS = {
    "amp_read_log10_mic_um": ("min", "model_prediction", "log10(uM)"),
    "llamp_log10_mic_um": ("min", "model_prediction", "log10(uM)"),
    "macrel_amp_probability": ("max", "model_prediction", "probability"),
    "toxinpred3_hybrid_score": ("min", "model_prediction", "unitless score"),
    "macrel_hemolysis_probability": ("min", "model_prediction", "probability"),
    "hydrophobic_moment_eisenberg": ("max", "sequence_descriptor", "unitless"),
    "hydrophobic_ratio_modlamp": ("descriptive", "sequence_descriptor", "fraction"),
    "maximum_hydrophobic_run": ("min", "sequence_descriptor", "residues"),
    "net_charge_ph7_4": ("descriptive", "sequence_descriptor", "elementary charge"),
    "guruprasad_instability_index": ("audit_min", "sequence_descriptor", "index"),
}
LABEL_METRICS = {
    "toxinpred3_label": ("Non-Toxin", "model_prediction"),
    "macrel_hemolysis_label": ("low", "model_prediction"),
}
PARETO_OBJECTIVES = (
    ("amp_read_log10_mic_um", "min"),
    ("llamp_log10_mic_um", "min"),
    ("macrel_amp_probability", "max"),
    ("toxinpred3_hybrid_score", "min"),
    ("macrel_hemolysis_probability", "min"),
    ("hydrophobic_moment_eisenberg", "max"),
    ("maximum_hydrophobic_run", "min"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _numeric_summary(rows: list[dict[str, str]], metric: str) -> dict[str, object]:
    valid = [row for row in rows if row.get(metric, "") not in {"", None}]
    values = [float(row[metric]) for row in valid]
    low = min(valid, key=lambda row: float(row[metric]))
    high = max(valid, key=lambda row: float(row[metric]))
    direction, evidence_kind, unit = NUMERIC_METRICS[metric]
    ood_count = sum(row.get(f"{metric}__ood", "").lower() == "true" for row in valid)
    failed_count = sum(
        row.get(f"{metric}__status", "") not in {"succeeded", ""} for row in rows
    )
    preferred = low if direction in {"min", "audit_min"} else high
    disfavored = high if direction in {"min", "audit_min"} else low
    if direction == "descriptive":
        preferred = disfavored = None
    skew = (
        "right-skewed"
        if statistics.fmean(values) > statistics.median(values)
        else "left-skewed_or_symmetric"
    )
    return {
        "metric": metric,
        "evidence_kind": evidence_kind,
        "direction": direction,
        "unit": unit,
        "cohort_n": len(rows),
        "valid_n": len(valid),
        "missing_n": len(rows) - len(valid),
        "failed_n": failed_count,
        "ood_n": ood_count,
        "min": min(values),
        "max": max(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "sd": statistics.pstdev(values),
        "p10": _quantile(values, 0.10),
        "p25": _quantile(values, 0.25),
        "p75": _quantile(values, 0.75),
        "p90": _quantile(values, 0.90),
        "min_candidate": {
            "candidate_id": low["candidate_id"],
            "sequence": low["sequence"],
        },
        "max_candidate": {
            "candidate_id": high["candidate_id"],
            "sequence": high["sequence"],
        },
        "best_candidate": (
            {"candidate_id": preferred["candidate_id"], "sequence": preferred["sequence"]}
            if preferred is not None
            else None
        ),
        "worst_candidate": (
            {"candidate_id": disfavored["candidate_id"], "sequence": disfavored["sequence"]}
            if disfavored is not None
            else None
        ),
        "distribution_note": (
            f"{skew}; central 50% spans {_quantile(values, 0.25):.6g} to "
            f"{_quantile(values, 0.75):.6g}."
        ),
    }


def _label_summary(rows: list[dict[str, str]], metric: str) -> dict[str, object]:
    valid = [row for row in rows if row.get(metric, "") not in {"", None}]
    counts = Counter(row[metric] for row in valid)
    favorable, evidence_kind = LABEL_METRICS[metric]
    return {
        "metric": metric,
        "evidence_kind": evidence_kind,
        "favorable_category": favorable,
        "cohort_n": len(rows),
        "valid_n": len(valid),
        "missing_n": len(rows) - len(valid),
        "failed_n": sum(
            row.get(f"{metric}__status", "") not in {"succeeded", ""}
            for row in rows
        ),
        "ood_n": sum(
            row.get(f"{metric}__ood", "").lower() == "true" for row in valid
        ),
        "categories": {
            label: {"count": count, "percentage": count / len(valid) * 100}
            for label, count in sorted(counts.items())
        },
    }


def _dominates(left: dict[str, str], right: dict[str, str]) -> bool:
    strictly_better = False
    for metric, direction in PARETO_OBJECTIVES:
        a = float(left[metric])
        b = float(right[metric])
        if direction == "min":
            if a > b:
                return False
            strictly_better |= a < b
        else:
            if a < b:
                return False
            strictly_better |= a > b
    return strictly_better


def _pareto_front(rows: list[dict[str, str]]) -> set[str]:
    """Return the exact non-dominated set using bounded vectorized blocks.

    The previous nested Python loop became the dominant cost once the quality
    archive reached thousands of candidates.  This keeps the same strict
    dominance semantics while moving comparisons into NumPy and bounding the
    temporary comparison arrays to roughly 64 MiB.
    """

    if not rows:
        return set()
    values = np.asarray(
        [
            [
                float(row[metric]) if direction == "min" else -float(row[metric])
                for metric, direction in PARETO_OBJECTIVES
            ]
            for row in rows
        ],
        dtype=np.float64,
    )
    row_count, objective_count = values.shape
    target_boolean_bytes = 64 * 1024 * 1024
    block_size = max(
        1,
        min(
            512,
            target_boolean_bytes // max(1, row_count * objective_count * 2),
        ),
    )
    dominated = np.zeros(row_count, dtype=np.bool_)
    for start in range(0, row_count, block_size):
        stop = min(start + block_size, row_count)
        candidates = values[start:stop]
        no_worse = np.all(values[:, None, :] <= candidates[None, :, :], axis=2)
        strictly_better = np.any(values[:, None, :] < candidates[None, :, :], axis=2)
        dominated[start:stop] = np.any(no_worse & strictly_better, axis=0)
    return {
        row["candidate_id"]
        for row, is_dominated in zip(rows, dominated, strict=True)
        if not bool(is_dominated)
    }


def _audit_flags(row: dict[str, str]) -> list[str]:
    flags: list[str] = []
    if float(row["maximum_hydrophobic_run"]) > 7:
        flags.append("long_hydrophobic_run_gt7")
    if abs(float(row["net_charge_ph7_4"])) > 8:
        flags.append("extreme_abs_charge_gt8")
    if row["guruprasad_instability_index__ood"].lower() == "true":
        flags.append("instability_short_peptide_ood")
    if float(row["guruprasad_instability_index"]) > 40:
        flags.append("instability_index_gt40_non_gating")
    return flags


def _cohort_metric_summary(rows: list[dict[str, str]]) -> dict[str, object]:
    return {
        "cohort_n": len(rows),
        "numeric_metrics": [
            _numeric_summary(rows, metric) for metric in NUMERIC_METRICS
        ],
        "label_metrics": [
            _label_summary(rows, metric) for metric in LABEL_METRICS
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()

    with args.input_csv.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or len({row["candidate_id"] for row in rows}) != len(rows):
        raise ValueError("input must contain a non-empty unique candidate cohort")

    safety_eligible = [
        row
        for row in rows
        if row["toxinpred3_label"] == "Non-Toxin"
        and row["macrel_hemolysis_label"] == "low"
    ]
    front = _pareto_front(safety_eligible)
    tier_counts: Counter[str] = Counter()
    flag_counts: Counter[str] = Counter()
    for row in rows:
        flags = _audit_flags(row)
        tier_flags = [
            flag
            for flag in flags
            if flag != "instability_short_peptide_ood"
        ]
        on_front = row["candidate_id"] in front
        if on_front and not tier_flags:
            tier = "A_pareto_no_audit_flag"
        elif on_front or not tier_flags:
            tier = "B_pareto_or_no_audit_flag"
        else:
            tier = "C_review_flags"
        row["quality_tier"] = tier
        row["pareto_front_within_safety_eligible"] = str(on_front)
        row["audit_flags_non_gating"] = ";".join(flags)
        tier_counts[tier] += 1
        flag_counts.update(flags)

    output_csv = args.output_prefix.with_suffix(".csv")
    output_json = args.output_prefix.with_suffix(".json")
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    payload = {
        "schema_version": "ampgent.quality-continuation-review.1",
        "created_at": datetime.now(UTC).isoformat(),
        "source_csv": str(args.input_csv),
        "source_sha256": _sha256(args.input_csv),
        "candidate_count": len(rows),
        "safety_eligible_count": len(safety_eligible),
        "pareto_front_count": len(front),
        "quality_tiers": dict(sorted(tier_counts.items())),
        "audit_flags": dict(sorted(flag_counts.items())),
        "novelty": {
            "historical_exact_duplicate_count": sum(
                row.get("historical_exact_duplicate", "false").lower() == "true"
                for row in rows
            ),
            "historical_family_overlap_count": sum(
                row.get("historical_family_overlap_80_80", "false").lower() == "true"
                for row in rows
            ),
            "within_cohort_family_count": len(
                {
                    row["sequence_family_key"]
                    for row in rows
                    if row.get("sequence_family_key")
                }
            ),
        },
        "numeric_metrics": [
            _numeric_summary(rows, metric) for metric in NUMERIC_METRICS
        ],
        "label_metrics": [_label_summary(rows, metric) for metric in LABEL_METRICS],
        "cohort_metric_summaries": {
            "all": _cohort_metric_summary(rows),
            "safety_eligible": _cohort_metric_summary(safety_eligible),
            **{
                tier: _cohort_metric_summary(
                    [row for row in rows if row["quality_tier"] == tier]
                )
                for tier in sorted(tier_counts)
            },
        },
        "evidence_scope": "computational predictions and sequence descriptors; no wet-lab data",
        "csv": str(output_csv),
        "csv_sha256": _sha256(output_csv),
    }
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
