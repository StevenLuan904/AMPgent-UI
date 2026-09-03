from __future__ import annotations

import bisect
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "reports/target_agnostic_source_graft_v14_20260903"
ARCHIVE = ROOT / "reports/target_agnostic_quality_combined_round10_20260826T2112.csv"


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


archive = read(ARCHIVE)
batch = read(BASE / "score_all/candidate_scores.csv")
keys = ("amp_read_log10_mic_um", "llamp_log10_mic_um", "macrel_amp_probability")
ordered = {key: sorted(float(row[key]) for row in archive) for key in keys}
for row in batch:
    ranks = {}
    for key in keys:
        value = float(row[key])
        rank = (
            bisect.bisect_right(ordered[key], value) / len(archive)
            if key == keys[2]
            else (len(archive) - bisect.bisect_left(ordered[key], value)) / len(archive)
        )
        ranks[key] = rank
        row[f"{key}__parent_benefit_percentile"] = f"{rank:.6f}"
    count = sum(rank >= 0.75 for rank in ranks.values())
    row["activity_model_support_count_calibrated"] = str(count)
    row["excellent_sequence_stage_calibrated"] = str(
        row.get("display_eligible", "").lower() == "true" and count >= 2
    ).lower()
    row["activity_support_semantics"] = "target_agnostic_archive_top_quartile_per_model"
    row["activity_support_percentile_semantics"] = "target_agnostic_archive_empirical_cdf"
with (BASE / "candidate_scores_calibrated.csv").open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(batch[0]))
    writer.writeheader()
    writer.writerows(batch)
(BASE / "calibration_receipt.json").write_text(
    json.dumps(
        {
            "schema_version": "ampgent.target-agnostic-v14-calibration.1",
            "candidate_count": len(batch),
            "display_count": sum(
                row.get("display_eligible", "").lower() == "true" for row in batch
            ),
            "support_ge_2_count": sum(
                int(row["activity_model_support_count_calibrated"]) >= 2 for row in batch
            ),
            "archive_count": len(archive),
            "model_release_key": "ampgent_formal12_frozen",
            "branch_key": "target_agnostic_amp",
            "directions": {keys[0]: "minimize", keys[1]: "minimize", keys[2]: "maximize"},
            "threshold": 0.75,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
