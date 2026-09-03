import bisect
import csv
import json
from pathlib import Path

base = Path("reports/target_agnostic_source_graft_v11_20260903")


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


batch = read(base / "score_all/candidate_scores.csv")
archive = read(Path("reports/target_agnostic_quality_combined_round10_20260826T2112.csv"))
metrics = ("amp_read_log10_mic_um", "llamp_log10_mic_um", "macrel_amp_probability")
ordered = {metric: sorted(float(row[metric]) for row in archive) for metric in metrics}
for row in batch:
    percentiles = {}
    for metric in metrics:
        value = float(row[metric])
        percentiles[metric] = (
            bisect.bisect_right(ordered[metric], value) / len(archive)
            if metric == "macrel_amp_probability"
            else (len(archive) - bisect.bisect_left(ordered[metric], value)) / len(archive)
        )
        row[f"{metric}__parent_benefit_percentile"] = f"{percentiles[metric]:.6f}"
    support = sum(value >= 0.75 for value in percentiles.values())
    row["activity_model_support_count_calibrated"] = str(support)
    row["excellent_sequence_stage_calibrated"] = str(
        row.get("display_eligible", "").lower() == "true" and support >= 2
    ).lower()
    row["activity_support_semantics"] = (
        "at_or_above_target_agnostic_archive_top_quartile_per_independent_model"
    )
    row["activity_support_percentile_semantics"] = "target_agnostic_archive_empirical_cdf"
with (base / "candidate_scores_calibrated.csv").open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(batch[0]))
    writer.writeheader()
    writer.writerows(batch)
(base / "calibration_receipt.json").write_text(
    json.dumps(
        {
            "candidate_count": len(batch),
            "display_count": sum(
                row.get("display_eligible", "").lower() == "true" for row in batch
            ),
            "support_ge_2_count": sum(
                int(row["activity_model_support_count_calibrated"]) >= 2 for row in batch
            ),
            "archive_count": len(archive),
            "directions": {
                "amp_read_log10_mic_um": "minimize",
                "llamp_log10_mic_um": "minimize",
                "macrel_amp_probability": "maximize",
            },
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
