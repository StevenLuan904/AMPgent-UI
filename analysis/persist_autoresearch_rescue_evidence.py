from __future__ import annotations

import argparse
import asyncio
import csv
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    persist_operational_call,
)
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json


def _candidate_payload(
    row: dict[str, str],
    *,
    excellent_challenger_status: str,
) -> dict[str, object]:
    excellent = row["excellent_sequence_stage_calibrated"].lower() == "true"
    return {
        "sequence": row["sequence"],
        "sequence_sha256": row["sequence_sha256"],
        "formal_12_complete": row["formal_12_complete"],
        "display_eligible": row["display_eligible"],
        "activity_model_support_count": int(
            row["activity_model_support_count_calibrated"]
        ),
        "family_key_80_80": row.get("family_key_80_80"),
        "diversity_qualified": row.get(
            "diversity_qualified",
            row.get("new_family_relative_to_postgresql_history", "false"),
        ),
        "excellent_sequence_stage": row["excellent_sequence_stage_calibrated"],
        "structure_status": "not_started",
        "challenger_status": (
            excellent_challenger_status
            if excellent
            else "not_reviewed_not_excellent_sequence_stage"
        ),
        "md_status": "not_started",
    }


async def _run(args: argparse.Namespace) -> None:
    if len(args.source_commit) != 40 or set(args.source_commit) - set("0123456789abcdef"):
        raise ValueError("source commit must be a lowercase SHA-1")
    superseded_run_id = (
        str(uuid.UUID(args.supersedes_operational_run_id))
        if args.supersedes_operational_run_id
        else None
    )
    with args.candidate_scores.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("operational score-all persistence requires scored rows")
    if any(row["formal_12_complete"].lower() != "true" for row in rows):
        raise ValueError("operational score-all persistence requires complete formal-12 rows")
    score_sha256 = sha256_file(args.candidate_scores)
    source_receipt_sha256 = sha256_file(args.source_receipt)
    persisted: list[dict[str, str]] = []
    for branch_key in sorted({row["branch_key"] for row in rows}):
        branch_rows = [row for row in rows if row["branch_key"] == branch_key]
        excellent_count = sum(
            row["excellent_sequence_stage_calibrated"].lower() == "true"
            for row in branch_rows
        )
        now = datetime.now(UTC)
        record = OperationalCallRecord(
            operation_key=(
                f"safety-rescue-fullscore:{args.source_commit}:{score_sha256}:{branch_key}"
            ),
            target_key=branch_key,  # type: ignore[arg-type]
            purpose="score_all",
            tool_name="autoresearch_safety_rescue_fullscore",
            tool_version="v2",
            status="succeeded",
            input_payload={
                "candidate_scores_sha256": score_sha256,
                "source_receipt_sha256": source_receipt_sha256,
                "candidate_count": len(branch_rows),
                "excellent_candidate_count": excellent_count,
                "supersedes_operational_run_id": superseded_run_id,
                "challenger_receipt_sha256": args.challenger_receipt_sha256,
            },
            parameters={
                "formal_metric_count": 12,
                "activity_support_threshold": "parent_run_top_quartile",
                "minimum_independent_activity_support": 2,
            },
            execution_context={
                "source_commit": args.source_commit,
                "execution_mode": "local_cpu_direct_frozen_metric_runtimes",
                "temporal_used": False,
                "gpu_used": False,
                "historical_run_modified": False,
                "supersedes_operational_run_id": superseded_run_id,
            },
            output_payload={
                "candidate_count": len(branch_rows),
                "excellent_candidate_count": excellent_count,
                "candidates": [
                    _candidate_payload(
                        row,
                        excellent_challenger_status=args.challenger_status,
                    )
                    for row in branch_rows
                ],
                "candidate_scores_sha256": score_sha256,
                "source_receipt_sha256": source_receipt_sha256,
                "challenger_receipt_sha256": args.challenger_receipt_sha256,
            },
            queued_at=now,
            started_at=now,
            finished_at=now,
        )
        async with SessionFactory() as session:
            run, call = await persist_operational_call(session, record)
            await session.commit()
        persisted.append(
            {
                "branch_key": branch_key,
                "operational_run_id": str(run.id),
                "tool_call_id": str(call.id),
            }
        )
    receipt = {
        "schema_version": "ampgent.autoresearch-safety-rescue-persistence.1",
        "persisted_at_utc": datetime.now(UTC).isoformat(),
        "source_commit": args.source_commit,
        "candidate_scores_sha256": score_sha256,
        "source_receipt_sha256": source_receipt_sha256,
        "candidate_count": len(rows),
        "excellent_candidate_count": sum(
            row["excellent_sequence_stage_calibrated"].lower() == "true" for row in rows
        ),
        "persistence_scope": "all_formal_12_scored_candidates",
        "supersedes_operational_run_id": superseded_run_id,
        "persisted": persisted,
        "historical_run_modified": False,
        "workflow_submitted": False,
        "gpu_task_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-scores", type=Path, required=True)
    parser.add_argument("--source-receipt", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--supersedes-operational-run-id")
    parser.add_argument(
        "--challenger-status",
        choices=("not_started", "reviewed_no_conflict", "reviewed_conflict"),
        default="not_started",
    )
    parser.add_argument("--challenger-receipt-sha256")
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
