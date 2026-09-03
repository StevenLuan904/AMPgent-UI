from __future__ import annotations

import bisect
import csv
import json
from pathlib import Path

base = Path("reports/target_agnostic_source_graft_v9_20260903")
score = base / "score_all/receipt.json"
input_csv = base / "score_all/candidate_scores.csv"
archive_csv = Path("reports/target_agnostic_quality_combined_round10_20260826T2112.csv")
output_csv = base / "candidate_scores_calibrated.csv"
output_json = base / "calibration_receipt.json"


def rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


batch = rows(input_csv)
archive = rows(archive_csv)
metrics = ("amp_read_log10_mic_um", "llamp_log10_mic_um", "macrel_amp_probability")
ordered = {metric: sorted(float(row[metric]) for row in archive) for metric in metrics}
for row in batch:
    values = {}
    for metric in metrics:
        value = float(row[metric])
        if metric == "macrel_amp_probability":
            percentile = bisect.bisect_right(ordered[metric], value) / len(archive)
        else:
            percentile = (len(archive) - bisect.bisect_left(ordered[metric], value)) / len(archive)
        values[metric] = percentile
        row[f"{metric}__parent_benefit_percentile"] = f"{percentile:.6f}"
    support = sum(value >= 0.75 for value in values.values())
    row["activity_model_support_count_calibrated"] = str(support)
    row["excellent_sequence_stage_calibrated"] = str(
        row.get("display_eligible", "").lower() == "true" and support >= 2
    ).lower()
    row["activity_support_semantics"] = (
        "at_or_above_target_agnostic_archive_top_quartile_per_independent_model"
    )
    row["activity_support_percentile_semantics"] = "target_agnostic_archive_empirical_cdf"

fields = list(batch[0])
with output_csv.open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(batch)
payload = {
    "schema_version": "ampgent.target-agnostic-v9-calibration.1",
    "candidate_count": len(batch),
    "formal12_count": sum(row.get("formal_12_complete", "").lower() == "true" for row in batch),
    "display_count": sum(row.get("display_eligible", "").lower() == "true" for row in batch),
    "support_ge_2_count": sum(
        int(row["activity_model_support_count_calibrated"]) >= 2 for row in batch
    ),
    "excellent_count": sum(row["excellent_sequence_stage_calibrated"] == "true" for row in batch),
    "parent_archive_count": len(archive),
    "score_receipt": str(score),
    "semantics": "target-agnostic archive empirical CDF; no target-specific parent substituted",
}
output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
