"""Write a fail-closed challenger artifact when a local model runtime is absent."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from pepagent.provenance.hashing import sha256_file


def write(input_csv: Path, output_dir: Path, *, runtime: str = "hemopi2") -> dict:
    with input_csv.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("challenger input is empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    review = [
        {
            "candidate_id": row["candidate_id"],
            "sequence": row["sequence"],
            "sequence_sha256": row["sequence_sha256"],
            "branch_key": row["branch_key"],
            "target_key": row["target_key"],
            "challenger_runtime_status": "runtime_unavailable",
            "challenger_conflict_status": "not_assessed",
            "candidate_hard_gate_allowed": "false",
            "missing_verified_runtimes": runtime,
        }
        for row in rows
    ]
    review_path = output_dir / "challenger_review.csv"
    with review_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(review[0]))
        writer.writeheader()
        writer.writerows(review)
    receipt = {
        "schema_version": "ampgent.autoresearch-challenger-review.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "source_csv_sha256": sha256_file(input_csv),
        "review_scope": "formal12",
        "reviewed_candidate_count": 0,
        "candidate_input_count": len(rows),
        "challenger_no_conflict_count": 0,
        "challenger_conflict_count": 0,
        "challenger_status": "runtime_unavailable",
        "missing_verified_runtimes": [runtime],
        "challenger_is_not_a_primary_hard_gate": True,
        "review_csv_sha256": sha256_file(review_path),
        "workflow_submitted": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
    }
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runtime", default="hemopi2")
    args = parser.parse_args()
    print(json.dumps(write(args.input_csv, args.output_dir, runtime=args.runtime), sort_keys=True))


if __name__ == "__main__":
    main()
