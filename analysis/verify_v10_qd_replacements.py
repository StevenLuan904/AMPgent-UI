import bisect
import csv
import json
from pathlib import Path

base = Path("reports/target_agnostic_source_graft_v10_20260903")


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


archive = read(Path("reports/target_agnostic_quality_combined_round10_20260826T2112.csv"))
batch = {row["sequence_sha256"]: row for row in read(base / "candidate_scores_calibrated.csv")}
metrics = ("amp_read_log10_mic_um", "llamp_log10_mic_um", "macrel_amp_probability")
ordered = {metric: sorted(float(row[metric]) for row in archive) for metric in metrics}


def quality(row):
    scores = []
    for metric in metrics:
        value = float(row[metric])
        scores.append(
            bisect.bisect_right(ordered[metric], value) / len(archive)
            if metric == "macrel_amp_probability"
            else (len(archive) - bisect.bisect_left(ordered[metric], value)) / len(archive)
        )
    return sum(scores) / len(scores)


qd = json.loads((base / "quality_diversity.json").read_text())
replacement = [
    item for item in qd["contributions"] if item["contribution"] == "incumbent_replacement"
]
verified = []
for item in replacement:
    incumbent = next(
        row for row in archive if row["sequence_sha256"] == item["incumbent_candidate_id"]
    )
    child_row = batch[item["candidate_id"]]
    verified.append(
        {
            "candidate_id": item["candidate_id"],
            "incumbent_candidate_id": item["incumbent_candidate_id"],
            "child_quality": quality(child_row),
            "incumbent_quality": quality(incumbent),
        }
    )
payload = {
    "replacement_count": len(verified),
    "all_display_support_ge_2": all(
        batch[row["candidate_id"]]["display_eligible"].lower() == "true"
        and int(batch[row["candidate_id"]]["activity_model_support_count_calibrated"]) >= 2
        for row in verified
    ),
    "all_child_quality_gt_incumbent": all(
        row["child_quality"] > row["incumbent_quality"] for row in verified
    ),
    "rows": verified,
}
(base / "replacement_verification.json").write_text(
    json.dumps(payload, indent=2) + "\n", encoding="utf-8"
)
