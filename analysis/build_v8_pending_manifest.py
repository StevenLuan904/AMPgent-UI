import csv
import json
from pathlib import Path

base = Path("reports/target_agnostic_source_graft_v8_20260903")
source = base / "candidate_scores_calibrated.csv"
output = base / "pending_materialization_manifest.csv"
with source.open(encoding="utf-8-sig", newline="") as stream:
    rows = list(csv.DictReader(stream))
fields = [
    "sequence_sha256",
    "sequence",
    "donor_source",
    "display_eligible",
    "activity_model_support_count_calibrated",
    "excellent_sequence_stage_calibrated",
    "pg_status",
    "admission_status",
]
with output.open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                **{field: row.get(field, "") for field in fields},
                "pg_status": "pg_new",
                "admission_status": "raw_evidence_materialized_not_pool_a",
            }
        )
print(json.dumps({"count": len(rows), "output": str(output)}, ensure_ascii=False))
