from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoresearch_challenger_rescue_round3 import _run_hemopi2

from pepagent.provenance.hashing import sha256_file, sha256_json


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(args: argparse.Namespace) -> None:
    with args.input_csv.open(encoding="utf-8-sig", newline="") as stream:
        source_rows = list(csv.DictReader(stream))
    rows = [
        row
        for row in source_rows
        if row.get("excellent_sequence_stage_calibrated", "").lower() == "true"
    ]
    if not rows:
        raise ValueError("challenger review has no calibrated excellent candidates")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    review, challenger_hashes = _run_hemopi2(
        rows=rows,
        repo_root=args.repo_root.resolve(),
        output_dir=output_dir,
        runtime_python=args.hemopi2_runtime,
        worker=args.hemopi2_worker,
        model_root=args.hemopi2_model_root,
        calibration_path=args.hemopi2_calibration,
    )
    review.sort(
        key=lambda row: (
            row["branch_key"],
            row["challenger_conflict_status"] != "no_conflict",
            float(row["calibrated_hemolysis_probability"]),
            -int(row["activity_model_support_count_calibrated"]),
            row["sequence"],
        )
    )
    _write_csv(output_dir / "challenger_review.csv", review)
    no_conflict = [row for row in review if row["challenger_conflict_status"] == "no_conflict"]
    if no_conflict:
        _write_csv(output_dir / "challenger_no_conflict.csv", no_conflict)
    summary = []
    for branch_key in sorted({str(row["branch_key"]) for row in review}):
        branch_rows = [row for row in review if row["branch_key"] == branch_key]
        summary.append(
            {
                "branch_key": branch_key,
                "excellent_candidate_count": len(branch_rows),
                "challenger_no_conflict_count": sum(
                    row["challenger_conflict_status"] == "no_conflict"
                    for row in branch_rows
                ),
                "challenger_conflict_count": sum(
                    row["challenger_conflict_status"] != "no_conflict"
                    for row in branch_rows
                ),
            }
        )
    _write_csv(output_dir / "branch_summary.csv", summary)
    receipt = {
        "schema_version": "ampgent.autoresearch-challenger-review.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "source_csv_sha256": sha256_file(args.input_csv),
        "reviewed_excellent_candidate_count": len(review),
        "challenger_no_conflict_count": len(no_conflict),
        "challenger_conflict_count": len(review) - len(no_conflict),
        "branch_summary": summary,
        **challenger_hashes,
        "challenger_review_csv_sha256": sha256_file(
            output_dir / "challenger_review.csv"
        ),
        "challenger_is_not_a_primary_hard_gate": True,
        "missing_verified_runtimes": ["apex", "peptiverse"],
        "workflow_submitted": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--hemopi2-runtime", type=Path, required=True)
    parser.add_argument("--hemopi2-worker", type=Path, required=True)
    parser.add_argument("--hemopi2-model-root", type=Path, required=True)
    parser.add_argument("--hemopi2-calibration", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
