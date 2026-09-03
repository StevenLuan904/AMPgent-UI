"""Measure whether Rosetta coarse-screen ranks agree with completed MD endpoints."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean

METRICS = {
    "interface_rmsd_mean_nm": {"unit": "nm", "expected_rho_sign": 1},
    "native_contact_fraction_mean": {"unit": "fraction", "expected_rho_sign": -1},
    "mmgbsa_mean_kcal_mol": {"unit": "kcal/mol", "expected_rho_sign": 1},
}


def ranks(values: list[float]) -> list[float]:
    result = [0.0] * len(values)
    ordered = sorted(range(len(values)), key=values.__getitem__)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        average_rank = (start + 1 + end) / 2
        for index in ordered[start:end]:
            result[index] = average_rank
        start = end
    return result


def pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    left_mean, right_mean = fmean(left), fmean(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean)
        for a, b in zip(left, right, strict=True)
    )
    left_scale = sum((value - left_mean) ** 2 for value in left)
    right_scale = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_scale * right_scale)
    return numerator / denominator if denominator else None


def spearman(left: list[float], right: list[float]) -> float | None:
    return pearson(ranks(left), ranks(right))


def boolean(value: str) -> bool:
    if value == "True":
        return True
    if value == "False":
        return False
    raise ValueError(f"invalid boolean: {value!r}")


def metric_result(rows: list[dict], key: str, spec: dict) -> dict:
    pairs = [
        (float(row["rosetta_median_dg_reu"]), float(row[key]))
        for row in rows
        if row.get("rosetta_median_dg_reu") not in (None, "")
        and row.get(key) not in (None, "")
    ]
    rho = spearman([pair[0] for pair in pairs], [pair[1] for pair in pairs])
    alignment = rho * spec["expected_rho_sign"] if rho is not None else None
    if alignment is None:
        strength = "not_estimable"
    elif alignment >= 0.7:
        strength = "strong_expected_direction"
    elif alignment >= 0.3:
        strength = "moderate_expected_direction"
    else:
        strength = "weak_or_opposite_direction"
    return {
        "paired_count": len(pairs),
        "spearman_rho": rho,
        "expected_rho_sign": spec["expected_rho_sign"],
        "signed_alignment": alignment,
        "alignment_class": strength,
        "unit": spec["unit"],
    }


def cohort(rows: list[dict]) -> dict:
    return {
        "candidate_count": len(rows),
        "peptide_departed_count": sum(boolean(row["peptide_departed"]) for row in rows),
        "metrics": {
            key: metric_result(rows, key, spec) for key, spec in METRICS.items()
        },
    }


def analyze(rows: list[dict]) -> dict:
    complete = [
        row
        for row in rows
        if boolean(row["pool_s_evidence_complete"])
        and boolean(row["postgresql_evidence_complete"])
    ]
    seen = {(row["run_id"], row["candidate_id"]) for row in complete}
    if len(seen) != len(complete):
        raise ValueError("duplicate completed run/candidate identity")
    by_target: dict[str, list[dict]] = defaultdict(list)
    for row in complete:
        by_target[row["target_key"]].append(row)
    overall = cohort(complete)
    correlations = [
        value["signed_alignment"]
        for value in overall["metrics"].values()
        if value["signed_alignment"] is not None
    ]
    strong_surrogate_evidence = len(complete) >= 30 and correlations and min(correlations) >= 0.7
    return {
        "schema_version": "ampgent.rosetta-md-concordance.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "scope": "completed Pool-A candidates with PostgreSQL-closed MD evidence",
        "overall": overall,
        "targets": {key: cohort(value) for key, value in sorted(by_target.items())},
        "decision": (
            "rosetta_can_replace_md"
            if strong_surrogate_evidence
            else "retain_md_as_nonredundant_gate"
        ),
        "limitations": [
            "Correlations are descriptive model-to-model concordance, not binding validation.",
            "Fewer than 30 completed candidates is treated as preliminary evidence.",
            "Pooled correlations can mix target-specific scales; target cohorts remain separate.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with args.candidates.open(newline="", encoding="utf-8") as stream:
        result = analyze(list(csv.DictReader(stream)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)


if __name__ == "__main__":
    main()
