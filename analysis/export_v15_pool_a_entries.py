from __future__ import annotations

import csv
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
base = root / "reports/target_agnostic_source_graft_v15_20260903"
with (base / "candidate_scores_calibrated.csv").open(encoding="utf-8-sig", newline="") as stream:
    scores = list(csv.DictReader(stream))
qd = json.loads((base / "quality_diversity.json").read_text(encoding="utf-8"))
contributions = {row["candidate_id"]: row for row in qd["contributions"]}
rows = []
for row in scores:
    contribution = contributions[row["sequence_sha256"]]
    if contribution["contribution"] == "incumbent_replacement":
        rows.append(
            {
                "sequence": row["sequence"],
                "sequence_sha256": row["sequence_sha256"],
                "run_id": "7519f976-6b0d-5a4d-a665-e4bab9a21e15",
                "qd_contribution": contribution["contribution"],
                "qd_status": "eligible",
                "quality": contribution["quality"],
                "target_key": "target_agnostic",
            }
        )
fields = [
    "sequence",
    "sequence_sha256",
    "run_id",
    "qd_contribution",
    "qd_status",
    "quality",
    "target_key",
]
with (base / "pool_a_entries.csv").open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
(base / "pool_a_entries_receipt.json").write_text(
    json.dumps(
        {
            "count": len(rows),
            "contribution": "incumbent_replacement",
            "rosetta_required": False,
            "exemption_reason": "target_agnostic",
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
