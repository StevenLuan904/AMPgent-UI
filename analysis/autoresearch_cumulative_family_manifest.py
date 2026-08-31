from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_file, sha256_json

BRANCHES = ("acea", "angpt1", "fgf2", "gyra", "pbp2a", "vegfa")
PERCENTILE_FIELDS = (
    "amp_read_log10_mic_um__parent_benefit_percentile",
    "llamp_log10_mic_um__parent_benefit_percentile",
    "macrel_amp_probability__parent_benefit_percentile",
)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _priority(row: dict[str, str]) -> tuple[float | int | str, ...]:
    return (
        -int(row["activity_model_support_count_calibrated"]),
        float(row["calibrated_hemolysis_probability"]),
        *(-float(row[field]) for field in PERCENTILE_FIELDS),
        float(row["guruprasad_instability_index"]),
        row["sequence"],
    )


def _diversity_qualified(row: dict[str, str]) -> bool:
    explicit = row.get("diversity_qualified", "").strip()
    if explicit:
        return explicit.lower() == "true"
    relative_to_all = row.get("new_family_relative_to_all_references", "").strip()
    if relative_to_all:
        return relative_to_all.lower() == "true"
    return (
        row.get("new_family_relative_to_postgresql_history", "").strip().lower()
        == "true"
    )


def run(args: argparse.Namespace) -> None:
    rows: list[dict[str, str]] = []
    source_hashes: list[str] = []
    for path in args.input_csv:
        source_hashes.append(sha256_file(path))
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows.extend(csv.DictReader(stream))
    if not rows:
        raise ValueError("cumulative family manifest input is empty")
    required_truths = (
        "formal_12_complete",
        "display_eligible",
        "excellent_sequence_stage_calibrated",
    )
    for row in rows:
        if any(row.get(field, "").lower() != "true" for field in required_truths):
            raise ValueError("cumulative candidate violates a required sequence-stage gate")
        if not _diversity_qualified(row):
            raise ValueError("cumulative candidate violates the diversity gate")
        if row.get("challenger_conflict_status") != "no_conflict":
            raise ValueError("cumulative candidate has an unresolved challenger conflict")
        if not row.get("family_key_80_80"):
            raise ValueError("cumulative candidate is missing an 80/80 family key")
    sequences = [row["sequence"] for row in rows]
    family_keys = [row["family_key_80_80"] for row in rows]
    if len(set(sequences)) != len(sequences):
        raise ValueError("cumulative family manifest contains a duplicate sequence")
    if len(set(family_keys)) != len(family_keys):
        raise ValueError("cumulative family manifest contains a duplicate 80/80 family")

    annotated: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    for branch_key in BRANCHES:
        branch_rows = sorted(
            (row for row in rows if row["branch_key"] == branch_key), key=_priority
        )
        if len(branch_rows) < args.minimum_per_branch:
            raise ValueError(
                f"{branch_key} has {len(branch_rows)} qualified families; "
                f"requires {args.minimum_per_branch}"
            )
        selected_sequences = {
            row["sequence"] for row in branch_rows[: args.minimum_per_branch]
        }
        for row in branch_rows:
            output_row = {
                **row,
                "structure_queue_selected": str(
                    row["sequence"] in selected_sequences
                ).lower(),
                "structure_status": "not_started",
                "rosetta_dg_receipt_status": "missing",
                "md_status": "not_started",
                "selection_method": (
                    "lexicographic_support_challenger_percentiles_stability_no_weighted_total"
                ),
            }
            annotated.append(output_row)
            if row["sequence"] in selected_sequences:
                selected.append(output_row)
        summary.append(
            {
                "branch_key": branch_key,
                "qualified_distinct_family_count": len(branch_rows),
                "structure_queue_selected_count": args.minimum_per_branch,
                "rosetta_dg_receipt_complete_count": 0,
                "rosetta_dg_receipt_missing_count": args.minimum_per_branch,
                "sequence_stage_family_quota_met": "true",
                "final_wetlab_goal_complete": "false",
            }
        )
    annotated.sort(key=lambda row: (str(row["branch_key"]), _priority(row)))
    selected.sort(key=lambda row: (str(row["branch_key"]), _priority(row)))
    _write_csv(args.output_csv, annotated)
    _write_csv(args.selected_csv, selected)
    _write_csv(args.summary_csv, summary)
    receipt = {
        "schema_version": "ampgent.autoresearch-cumulative-family-manifest.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "source_csv_sha256s": source_hashes,
        "qualified_candidate_count": len(rows),
        "globally_distinct_sequence_count": len(set(sequences)),
        "globally_distinct_family_count": len(set(family_keys)),
        "minimum_per_branch": args.minimum_per_branch,
        "structure_queue_candidate_count": len(selected),
        "branch_summary": summary,
        "all_sequence_stage_family_quotas_met": True,
        "all_rosetta_dg_receipts_complete": False,
        "final_wetlab_goal_complete": False,
        "no_weighted_total_score": True,
        "output_csv_sha256": sha256_file(args.output_csv),
        "selected_csv_sha256": sha256_file(args.selected_csv),
        "summary_csv_sha256": sha256_file(args.summary_csv),
        "workflow_submitted": False,
        "gpu_task_submitted": False,
        "historical_run_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    args.output_json.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", type=Path, action="append", required=True)
    parser.add_argument("--minimum-per-branch", type=int, default=50)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--selected-csv", type=Path, required=True)
    parser.add_argument("--summary-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
