from __future__ import annotations

import bisect
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V9 = ROOT / "reports/target_agnostic_source_graft_v9_20260903/proposals.csv"
SOURCE = ROOT / "reports/pepflow_acea_reciprocal_micrograft_scoreall_20260903/candidate_scores_calibrated.csv"
ARCHIVE = ROOT / "reports/target_agnostic_quality_combined_round10_20260826T2112.csv"
OUT = ROOT / "reports/target_agnostic_v10_diagnostic_20260903"
METRICS = ("amp_read_log10_mic_um", "llamp_log10_mic_um", "macrel_amp_probability")


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


v9 = read(V9)
source = {row.get("candidate_id", ""): row for row in read(SOURCE)}
archive = read(ARCHIVE)
ordered = {metric: sorted(float(row[metric]) for row in archive) for metric in METRICS}
rows = []
for parent_id in sorted({row["parent_candidate_id"] for row in v9}):
    parent = source.get(parent_id)
    if parent is None:
        continue
    percentiles = {}
    for metric in METRICS:
        value = float(parent[metric])
        if metric == "macrel_amp_probability":
            percentile = bisect.bisect_right(ordered[metric], value) / len(archive)
        else:
            percentile = (len(archive) - bisect.bisect_left(ordered[metric], value)) / len(archive)
        percentiles[metric] = round(percentile, 6)
    target_support = sum(value >= 0.75 for value in percentiles.values())
    rows.append(
        {
            "parent_candidate_id": parent_id,
            "original_branch": parent.get("branch_key", ""),
            "original_calibration_semantics": parent.get("activity_support_percentile_semantics", ""),
            "original_model_release_keys": {metric: parent.get(f"{metric}__tool_version", "") for metric in METRICS},
            "original_percentiles": {metric: parent.get(f"{metric}__parent_benefit_percentile", "") for metric in METRICS},
            "original_support": int(float(parent.get("activity_model_support_count_calibrated", 0))),
            "target_agnostic_percentiles": percentiles,
            "target_agnostic_support": target_support,
            "display": parent.get("display_eligible", "").lower() == "true",
            "mismatch": {
                "branch": parent.get("branch_key") != "target_agnostic_amp",
                "calibration_domain": parent.get("activity_support_percentile_semantics") != "target_agnostic_archive_empirical_cdf",
                "support": int(float(parent.get("activity_model_support_count_calibrated", 0))) != target_support,
            },
        }
    )
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "mismatch_matrix.json").write_text(json.dumps({"parent_count": len(rows), "target_agnostic_archive_count": len(archive), "direction": {"amp_read_log10_mic_um": "higher_better", "llamp_log10_mic_um": "higher_better", "macrel_amp_probability": "higher_better"}, "rows": rows}, indent=2) + "\n", encoding="utf-8")
(OUT / "diagnostic_receipt.json").write_text(json.dumps({"v9_unique_parent_count": len(rows), "original_support_ge_2": sum(row["original_support"] >= 2 for row in rows), "target_agnostic_support_ge_2": sum(row["target_agnostic_support"] >= 2 and row["display"] for row in rows), "branch_mismatch_count": sum(row["mismatch"]["branch"] for row in rows), "calibration_domain_mismatch_count": sum(row["mismatch"]["calibration_domain"] for row in rows), "support_mismatch_count": sum(row["mismatch"]["support"] for row in rows)}, indent=2) + "\n", encoding="utf-8")
