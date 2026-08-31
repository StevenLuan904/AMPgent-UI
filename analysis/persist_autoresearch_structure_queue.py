from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    persist_operational_call,
)
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json


async def _run(args: argparse.Namespace) -> None:
    if len(args.source_commit) != 40 or set(args.source_commit) - set("0123456789abcdef"):
        raise ValueError("source commit must be a lowercase SHA-1")
    with args.selected_csv.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 300:
        raise ValueError("structure queue freeze requires exactly 300 candidates")
    if len({row["sequence_sha256"] for row in rows}) != len(rows):
        raise ValueError("structure queue contains a duplicate sequence")
    if len({row["family_key_80_80"] for row in rows}) != len(rows):
        raise ValueError("structure queue contains a duplicate family")
    if any(
        row.get("structure_queue_selected") != "true"
        or row.get("rosetta_dg_receipt_status") != "missing"
        for row in rows
    ):
        raise ValueError("structure queue freeze status drifted")
    selected_sha256 = sha256_file(args.selected_csv)
    manifest_receipt_sha256 = sha256_file(args.manifest_receipt)
    persisted = []
    for branch_key in sorted({row["branch_key"] for row in rows}):
        branch_rows = [row for row in rows if row["branch_key"] == branch_key]
        if len(branch_rows) != 50:
            raise ValueError(f"{branch_key} structure queue must contain exactly 50 rows")
        now = datetime.now(UTC)
        record = OperationalCallRecord(
            operation_key=(
                f"structure-queue-freeze:{args.source_commit}:"
                f"{selected_sha256}:{branch_key}"
            ),
            target_key=branch_key,  # type: ignore[arg-type]
            purpose="structure",
            tool_name="autoresearch_structure_queue_freeze",
            tool_version="v1",
            status="succeeded",
            input_payload={
                "selected_csv_sha256": selected_sha256,
                "manifest_receipt_sha256": manifest_receipt_sha256,
                "candidate_count": len(branch_rows),
            },
            parameters={
                "minimum_rosetta_decoys_per_candidate": 200,
                "exact_once_submission_required": True,
            },
            execution_context={
                "source_commit": args.source_commit,
                "execution_mode": "queue_freeze_only",
                "gpu_used": False,
                "gpu_task_submitted": False,
                "workflow_submitted": False,
                "historical_run_modified": False,
            },
            output_payload={
                "queue_status": "frozen_not_submitted",
                "rosetta_dg_receipt_complete_count": 0,
                "rosetta_dg_receipt_missing_count": len(branch_rows),
                "candidates": [
                    {
                        "sequence": row["sequence"],
                        "sequence_sha256": row["sequence_sha256"],
                        "family_key_80_80": row["family_key_80_80"],
                        "activity_model_support_count": int(
                            row["activity_model_support_count_calibrated"]
                        ),
                        "structure_status": "not_started",
                    }
                    for row in branch_rows
                ],
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
        "schema_version": "ampgent.autoresearch-structure-queue-persistence.1",
        "persisted_at_utc": datetime.now(UTC).isoformat(),
        "source_commit": args.source_commit,
        "selected_csv_sha256": selected_sha256,
        "manifest_receipt_sha256": manifest_receipt_sha256,
        "candidate_count": len(rows),
        "per_branch_count": 50,
        "queue_status": "frozen_not_submitted",
        "rosetta_dg_receipt_complete_count": 0,
        "persisted": persisted,
        "historical_run_modified": False,
        "workflow_submitted": False,
        "gpu_task_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    args.output.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selected-csv", type=Path, required=True)
    parser.add_argument("--manifest-receipt", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
