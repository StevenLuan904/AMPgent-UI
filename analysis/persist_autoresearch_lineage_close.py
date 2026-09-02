from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    persist_operational_call,
)
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json


def _validated_payloads(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    archives = json.loads(args.archive_updates.read_text(encoding="utf-8"))
    replay = json.loads(args.replay_bundle.read_text(encoding="utf-8"))
    if receipt.get("schema_version") != "ampgent.autoresearch-lineage-close.1":
        raise ValueError("lineage close receipt schema differs")
    if archives.get("schema_version") != "ampgent.autoresearch-multibranch-archive-update.1":
        raise ValueError("lineage archive update schema differs")
    if replay.get("schema_version") != "ampgent.autoresearch-multibranch-replay.1":
        raise ValueError("lineage replay schema differs")
    if set(receipt["branches"]) != set(archives["branches"]):
        raise ValueError("lineage close receipt/archive branches differ")
    expected_hashes = {
        "archive_updates_sha256": sha256_file(args.archive_updates),
        "replay_bundle_sha256": sha256_file(args.replay_bundle),
        "formal_12_parent_child_deltas_sha256": sha256_file(args.metric_deltas),
    }
    for key, value in expected_hashes.items():
        if receipt.get(key) != value:
            raise ValueError(f"lineage close file identity differs: {key}")
    if set(replay.get("branches", {})) != set(receipt["branches"]):
        raise ValueError("lineage replay branches differ")
    return receipt, replay


async def persist(args: argparse.Namespace) -> dict[str, Any]:
    receipt, replay = _validated_payloads(args)
    receipt_sha256 = sha256_file(args.receipt)
    persisted: list[dict[str, Any]] = []
    async with SessionFactory() as session:
        for branch_key in sorted(receipt["branches"]):
            branch = replay["branches"][branch_key]
            generation = int(branch["plan"]["generation"])
            record = OperationalCallRecord(
                operation_key=(
                    f"autoresearch-lineage-close:{receipt_sha256}:{branch_key}:"
                    f"generation-{generation}"
                ),
                target_key=branch_key,
                purpose="audit_reconciliation",
                tool_name="autoresearch_lineage_close",
                tool_version="2026.09.01-v1",
                status="succeeded",
                input_payload={
                    "receipt_sha256": receipt_sha256,
                    "archive_before_sha256": branch["archive_before_sha256"],
                    "plan_sha256": sha256_json(branch["plan"]),
                    "upstream_operational_run_ids": args.upstream_operational_run_id,
                },
                parameters={
                    "generation": generation,
                    "formal_metric_count": int(receipt["formal_metric_count"]),
                    "instability_hard_gate": "finite <=50",
                    "challenger_is_shadow_not_primary_gate": True,
                },
                execution_context={
                    "source_commit": args.source_commit,
                    "storage_uri": args.storage_uri,
                    "historical_run_modified": False,
                    "gpu_task_submitted": False,
                },
                output_payload={
                    "schema_version": "ampgent.autoresearch-lineage-close-branch.1",
                    "branch_key": branch_key,
                    "generation": generation,
                    "archive_after_sha256": branch["archive_after_sha256"],
                    "parent_child_delta_receipt_count": len(
                        branch["parent_child_deltas"]
                    ),
                    "archive_update_sha256": branch["archive_update"]["update_sha256"],
                    "lineage_close_receipt_sha256": receipt_sha256,
                },
                queued_at=datetime.now(UTC),
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
            run, call = await persist_operational_call(session, record)
            await session.commit()
            persisted.append(
                {
                    "branch_key": branch_key,
                    "generation": generation,
                    "operational_run_id": str(run.id),
                    "tool_call_id": str(call.id),
                }
            )
    return {
        "schema_version": "ampgent.autoresearch-lineage-close-persistence.1",
        "persisted_at_utc": datetime.now(UTC).isoformat(),
        "lineage_close_receipt_sha256": receipt_sha256,
        "storage_uri": args.storage_uri,
        "persisted": persisted,
        "historical_run_modified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--archive-updates", type=Path, required=True)
    parser.add_argument("--replay-bundle", type=Path, required=True)
    parser.add_argument("--metric-deltas", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--storage-uri", required=True)
    parser.add_argument("--upstream-operational-run-id", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = asyncio.run(persist(args))
    payload["receipt_payload_sha256"] = sha256_json(payload)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
