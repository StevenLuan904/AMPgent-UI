from __future__ import annotations

import csv
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
base = root / "reports/target_agnostic_source_graft_v13_20260903"
with (base / "candidate_scores_calibrated.csv").open(
    encoding="utf-8-sig", newline=""
) as stream:
    scores = list(csv.DictReader(stream))
qd = json.loads((base / "quality_diversity.json").read_text(encoding="utf-8"))
by_hash = {row["candidate_id"]: row for row in qd["contributions"]}
fields = [
    "sequence", "sequence_sha256", "run_id", "qd_contribution", "qd_status",
    "quality", "target_key",
]
rows = []
for row in scores:
    contribution = by_hash[row["sequence_sha256"]]
    if contribution["contribution"] != "incumbent_replacement":
        continue
    rows.append(
        {
            "sequence": row["sequence"],
            "sequence_sha256": row["sequence_sha256"],
            "run_id": "8606dd93-5364-5ca2-ab95-b5de2f73bda9",
            "qd_contribution": contribution["contribution"],
            "qd_status": "eligible",
            "quality": contribution["quality"],
            "target_key": "target_agnostic",
        }
    )
with (base / "structure_queue.csv").open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
(base / "structure_queue_receipt.json").write_text(
    json.dumps(
        {
            "count": len(rows),
            "qd_contribution": "incumbent_replacement",
            "non_contribution_excluded": True,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
