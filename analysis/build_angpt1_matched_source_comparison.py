"""Compare the ANGPT1 PepFlow and PepGLAD matched-source arms.

All rates retain their stage denominator; this report deliberately contains no
weighted composite score and treats unavailable shadow runtimes as unevaluated.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

Z = 1.959963984540054


def wilson(successes: int, trials: int) -> tuple[float, float, float]:
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError("invalid binomial counts")
    if trials == 0:
        return 0.0, 0.0, 0.0
    p = successes / trials
    denominator = 1.0 + Z * Z / trials
    center = (p + Z * Z / (2.0 * trials)) / denominator
    half = Z * math.sqrt(p * (1.0 - p) / trials + Z * Z / (4.0 * trials * trials)) / denominator
    return p, max(0.0, center - half), min(1.0, center + half)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _score_dir(root: Path) -> Path:
    fixed = root / "score_all_fixed"
    return fixed if (fixed / "candidate_scores.csv").exists() else root / "score_all"


def _prepared_queue(root: Path) -> Path:
    nested = root / "coarse5_prepared" / "coarse5_prepared_queue.csv"
    return nested if nested.exists() else root / "coarse5_prepared_queue.csv"


def _qd_path(root: Path) -> Path:
    provisional = root / "provisional_qd.json"
    return provisional if provisional.exists() else root / "quality_diversity.json"


def _materialization_input(root: Path) -> Path:
    singular = root / "materialization_input" / "candidate_scores.csv"
    return (
        singular
        if singular.exists()
        else root / "materialization_inputs" / "candidate_scores.csv"
    )


def summarize_arm(name: str, root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    score_root = _score_dir(root)
    scores = _rows(score_root / "candidate_scores.csv")
    calibrated = _rows(root / "candidate_scores_calibrated.csv")
    challenger = _rows(root / "challenger" / "challenger_review.csv")
    qd = _json(_qd_path(root))
    material = _json(root / "materialization_receipt.json")
    qd_by_hash = {item["candidate_id"]: item for item in qd["contributions"]}
    selected_hashes = {row["sequence_sha256"] for row in _rows(_materialization_input(root))}
    missing_shadows = set(
        _json(root / "challenger" / "receipt.json").get("missing_verified_runtimes", [])
    )
    counts = {
        "proposal": len(scores),
        "materialized": int(material["materialized_or_reused_in_run_count"]),
        "formal12": sum(row.get("formal_12_complete", "").lower() == "true" for row in scores),
        "display": sum(row.get("display_eligible", "").lower() == "true" for row in scores),
        "support_ge_2": sum(
            int(row.get("activity_model_support_count_calibrated", 0)) >= 2 for row in calibrated
        ),
        "excellent": sum(
            row.get("excellent_sequence_stage_calibrated", "").lower() == "true"
            for row in calibrated
        ),
        "challenger_reviewed": len(challenger),
        "challenger_no_conflict": sum(
            row.get("challenger_conflict_status") == "no_conflict" for row in challenger
        ),
        "qd_eligible": int(
            qd.get("quality_eligible_candidate_count", qd.get("quality_eligible_count", 0))
        ),
        "qd_new_cell": sum(
            item.get("contribution") == "empty_cell" for item in qd_by_hash.values()
        ),
        "qd_replacement": sum(
            item.get("contribution") == "incumbent_replacement" for item in qd_by_hash.values()
        ),
        "coarse5_prepared": len(_rows(_prepared_queue(root))),
    }
    rows: list[dict[str, Any]] = []
    for metric, count in counts.items():
        denominator = counts["proposal"]
        if metric in {"qd_new_cell", "qd_replacement"}:
            denominator = counts["qd_eligible"]
            denominator_stage = "qd_eligible"
        else:
            denominator_stage = "proposal"
        rate, low, high = wilson(count, denominator)
        rows.append(
            {
                "arm": name,
                "metric": metric,
                "count": count,
                "denominator": denominator,
                "denominator_stage": denominator_stage,
                "rate": rate,
                "wilson_low": low,
                "wilson_high": high,
            }
        )
    material_rows = [row for row in scores if row["sequence_sha256"] in selected_hashes]
    material_calibrated = [row for row in calibrated if row["sequence_sha256"] in selected_hashes]
    material_challenger = [row for row in challenger if row["sequence_sha256"] in selected_hashes]
    cohort = {
        "materialized_formal12": sum(
            row.get("formal_12_complete", "").lower() == "true" for row in material_rows
        ),
        "materialized_display": sum(
            row.get("display_eligible", "").lower() == "true" for row in material_rows
        ),
        "materialized_support_ge_2": sum(
            int(row.get("activity_model_support_count_calibrated", 0)) >= 2
            for row in material_calibrated
        ),
        "materialized_challenger_reviewed": len(material_challenger),
        "materialized_challenger_no_conflict": sum(
            row.get("challenger_conflict_status") == "no_conflict" for row in material_challenger
        ),
    }
    for metric, count in cohort.items():
        rate, low, high = wilson(count, counts["materialized"])
        rows.append(
            {
                "arm": name,
                "metric": metric,
                "count": count,
                "denominator": counts["materialized"],
                "denominator_stage": "materialized",
                "rate": rate,
                "wilson_low": low,
                "wilson_high": high,
            }
        )
    summary = {
        "arm": name,
        "counts": counts,
        "materialized_cohort": cohort,
        "missing_shadow_runtimes": sorted(missing_shadows),
        "no_weighted_total": True,
    }
    return rows, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pepflow-dir", type=Path, required=True)
    parser.add_argument("--pepglad-dir", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    summaries = []
    for name, root in (("PepFlow", args.pepflow_dir), ("PepGLAD", args.pepglad_dir)):
        arm_rows, summary = summarize_arm(name, root)
        rows.extend(arm_rows)
        summaries.append(summary)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "schema_version": "ampgent.angpt1-matched-source-comparison.1",
        "arms": summaries,
        "comparison_csv_sha256": __import__("hashlib")
        .sha256(args.output_csv.read_bytes())
        .hexdigest(),
        "no_weighted_total": True,
    }
    args.output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
