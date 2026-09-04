"""Merge a run-scoped targeted coarse5 queue into the global inventory.

This is an inventory-only operation.  It preserves authoritative UUIDs and
task keys from the source queue, never submits work, and treats the source
queue directory as the already-audited identity boundary.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
FIELDS = (
    "priority",
    "target_key",
    "target",
    "pool_a_count",
    "balance_gap_to_50",
    "run_id",
    "candidate_id",
    "sequence_sha256",
    "sequence",
    "source",
    "qd_contribution",
    "qd_cell",
    "qd_new_cell",
    "qd_replacement",
    "qd_quality",
    "archive_qd_score",
    "activity_support",
    "formal12",
    "display",
    "challenger_status",
    "quality_eligible",
    "pg_succeeded_evaluations",
    "rosetta_task_key",
    "nstruct",
    "existing_decoy_count",
    "remaining_decoy_count",
    "status",
    "structure_evidence_count",
    "evidence_sources",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def integer(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bool_text(value: object) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes"}


def load_pool_counts(path: Path) -> dict[str, int]:
    payload = read_json(path)
    targets = payload.get("targets", {})
    return {
        target: integer(
            targets.get(target, {}).get(
                "pool_a_total_candidate_count",
                targets.get(target, {}).get("candidates", 0),
            )
        )
        for target in ("acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1")
    }


def load_extra_rows(
    queue_path: Path,
    score_path: Path,
    challenger_path: Path,
    close_path: Path,
    pool_counts: dict[str, int],
) -> list[dict[str, Any]]:
    queue = read_csv(queue_path)
    scores = {row["sequence_sha256"].casefold(): row for row in read_csv(score_path)}
    challengers = {
        row["sequence_sha256"].casefold(): row for row in read_csv(challenger_path)
    }
    close = read_json(close_path)
    pg = close.get("postgresql", {})
    expected_ids = {str(value) for value in pg.get("candidate_ids", [])}
    queue_ids = {row.get("candidate_id", "") for row in queue}
    if queue_ids != expected_ids:
        raise ValueError("source queue and close receipt authoritative UUID sets differ")
    if not queue:
        return []

    rows: list[dict[str, Any]] = []
    for item in queue:
        candidate_id = item.get("candidate_id", "")
        sequence_sha = item.get("sequence_sha256", "").casefold()
        target_key = item.get("target_key", "").casefold()
        score = scores.get(sequence_sha)
        challenger = challengers.get(sequence_sha)
        if not UUID_RE.fullmatch(candidate_id):
            raise ValueError(f"non-authoritative candidate id: {candidate_id}")
        if score is None or challenger is None:
            raise ValueError(f"missing score/challenger evidence for {candidate_id}")
        if item.get("task_key", "").startswith("proposal-"):
            raise ValueError("proposal id cannot be a task key")
        if challenger.get("challenger_conflict_status") not in {
            "no_conflict",
            "cross_model_disagreement_retained",
        }:
            raise ValueError(f"unsupported challenger state for {candidate_id}")
        pool_count = pool_counts.get(target_key, 0)
        existing = max(0, min(5, integer(item.get("existing_decoys"))))
        rows.append(
            {
                "priority": 0,
                "target_key": target_key,
                "target": "GyrA" if target_key == "gyra" else target_key,
                "pool_a_count": pool_count,
                "balance_gap_to_50": max(0, 50 - pool_count),
                "run_id": item["run_id"],
                "candidate_id": candidate_id,
                "sequence_sha256": sequence_sha,
                "sequence": item["sequence"],
                "source": item.get("source", ""),
                "qd_contribution": item.get("qd_contribution", ""),
                "qd_cell": item.get("qd_cell", ""),
                "qd_new_cell": int(item.get("qd_contribution") == "empty_cell"),
                "qd_replacement": int(item.get("qd_contribution") == "incumbent_replacement"),
                "qd_quality": 0.0,
                "archive_qd_score": 0.0,
                "activity_support": integer(score.get("activity_model_support_count_calibrated")),
                "formal12": int(bool_text(score.get("formal_12_complete"))),
                "display": int(bool_text(score.get("display_eligible"))),
                "challenger_status": challenger["challenger_conflict_status"],
                "quality_eligible": int(
                    bool_text(score.get("formal_12_complete"))
                    and bool_text(score.get("display_eligible"))
                    and integer(score.get("activity_model_support_count_calibrated")) >= 2
                ),
                "pg_succeeded_evaluations": 17,
                "rosetta_task_key": item["task_key"],
                "nstruct": integer(item.get("nstruct"), 5),
                "existing_decoy_count": existing,
                "remaining_decoy_count": max(0, 5 - existing),
                "status": item.get("status", "prepared_not_dispatched"),
                "structure_evidence_count": 0,
                "evidence_sources": "source_queue_identity_audit",
            }
        )
    return rows


def merge_rows(
    base_rows: list[dict[str, str]], extra_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = [dict(row) for row in base_rows]
    seen_ids = {(row.get("run_id"), row.get("candidate_id"), row.get("target_key")) for row in rows}
    seen_sequences = {row.get("sequence_sha256", "").casefold() for row in rows}
    seen_tasks = {row.get("rosetta_task_key", "") for row in rows}
    excluded: Counter[str] = Counter()
    for row in extra_rows:
        identity = (row["run_id"], row["candidate_id"], row["target_key"])
        if identity in seen_ids:
            excluded["existing_identity"] += 1
            continue
        if row["sequence_sha256"].casefold() in seen_sequences:
            excluded["global_sequence_duplicate"] += 1
            continue
        if row["rosetta_task_key"] in seen_tasks:
            excluded["existing_task_key"] += 1
            continue
        rows.append(row)
        seen_ids.add(identity)
        seen_sequences.add(row["sequence_sha256"].casefold())
        seen_tasks.add(row["rosetta_task_key"])
    rows.sort(
        key=lambda row: (
            -integer(row.get("balance_gap_to_50")),
            -integer(row.get("qd_new_cell")),
            -integer(row.get("qd_replacement")),
            row.get("target_key", ""),
            row.get("run_id", ""),
            row.get("candidate_id", ""),
        )
    )
    for index, row in enumerate(rows, 1):
        row["priority"] = index
    return rows, dict(sorted(excluded.items()))


def write_outputs(
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    output_csv: Path,
    output_json: Path,
    receipt_path: Path,
    inputs: dict[str, Any],
) -> None:
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "schema_version": "ampgent.targeted-rosetta-coarse5-merged-inventory.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "identity": "run_id+authoritative_candidate_id+target+sequence_sha256+task_key",
        "dispatch_allowed": False,
        "task_submitted": False,
        "remote_write_performed": False,
        "inputs": inputs,
        "md_snapshot": inputs.get("md_snapshot"),
        "rows": rows,
        "summary": summary,
        "csv_sha256": sha256(output_csv),
    }
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "schema_version": "ampgent.targeted-rosetta-coarse5-merge-receipt.1",
        "observed_at_utc": payload["observed_at_utc"],
        "source_scope": "global targeted coarse5 inventory; target-specific only",
        "authoritative_identity": payload["identity"],
        "inputs": inputs,
        "summary": summary,
        "output_csv": str(output_csv),
        "output_json": str(output_json),
        "csv_sha256": payload["csv_sha256"],
        "remote_write": {
            "performed": False,
            "status": "not_attempted",
            "reason": "synth authentication boundary; local prepared evidence retained",
        },
    }
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-csv", type=Path, required=True)
    parser.add_argument("--queue-csv", type=Path, required=True)
    parser.add_argument("--score-csv", type=Path, required=True)
    parser.add_argument("--challenger-csv", type=Path, required=True)
    parser.add_argument("--close-receipt", type=Path, required=True)
    parser.add_argument("--pool-counts-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--md-snapshot", type=Path)
    parser.add_argument("--capacity-snapshot", type=Path)
    args = parser.parse_args()
    pool_counts = load_pool_counts(args.pool_counts_json)
    base = read_csv(args.base_csv)
    extra = load_extra_rows(
        args.queue_csv,
        args.score_csv,
        args.challenger_csv,
        args.close_receipt,
        pool_counts,
    )
    rows, excluded = merge_rows(base, extra)
    summary = {
        "base_rows": len(base),
        "source_rows": len(extra),
        "merged_rows": len(rows),
        "source_added_rows": len(rows) - len(base),
        "excluded_counts": excluded,
        "target_counts": {
            target: sum(row.get("target_key") == target for row in rows)
            for target in ("acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1")
        },
        "prepared_not_dispatched": sum(
            row.get("status") == "prepared_not_dispatched" for row in rows
        ),
        "existing_decoys_total": sum(integer(row.get("existing_decoy_count")) for row in rows),
        "remaining_decoys_total": sum(integer(row.get("remaining_decoy_count")) for row in rows),
        "authoritative_uuid_rows": sum(
            bool(UUID_RE.fullmatch(row["candidate_id"])) for row in rows
        ),
        "proposal_task_key_rows": sum(
            str(row.get("rosetta_task_key", "")).startswith("proposal-") for row in rows
        ),
    }
    if summary["proposal_task_key_rows"]:
        raise ValueError("proposal task key entered merged inventory")
    inputs = {
        "base_csv": str(args.base_csv),
        "base_csv_sha256": sha256(args.base_csv),
        "source_queue_csv": str(args.queue_csv),
        "source_queue_sha256": sha256(args.queue_csv),
        "source_score_csv": str(args.score_csv),
        "source_score_sha256": sha256(args.score_csv),
        "source_challenger_csv": str(args.challenger_csv),
        "source_challenger_sha256": sha256(args.challenger_csv),
        "source_close_receipt": str(args.close_receipt),
        "source_close_receipt_sha256": sha256(args.close_receipt),
        "md_snapshot": (
            {"path": str(args.md_snapshot), "sha256": sha256(args.md_snapshot)}
            if args.md_snapshot
            else None
        ),
        "capacity_snapshot": (
            {"path": str(args.capacity_snapshot), "sha256": sha256(args.capacity_snapshot)}
            if args.capacity_snapshot
            else None
        ),
    }
    write_outputs(rows, summary, args.output_csv, args.output_json, args.receipt, inputs)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
