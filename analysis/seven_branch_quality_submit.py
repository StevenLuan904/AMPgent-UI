from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.seven_branch_design import SevenBranchTopUpSchedule
from pepagent.seven_branch_reservation_cli import (
    reserve_seven_branch_top_up_schedule,
    submit_reserved_seven_branch_top_up_schedule,
)
from pepagent.seven_branch_schedule import (
    build_top_up_seven_branch_schedule,
    derive_top_up_seven_branch_run_ids,
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def _source_run_ids(delivery_csv: Path, branch_key: str) -> list[str]:
    with delivery_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["branch_key"] == branch_key]
    values = [row.get("source_run_id") or row.get("run_id") for row in rows]
    return list(dict.fromkeys(value for value in values if value))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze, reserve, and exact-once submit a quality-successor round."
    )
    parser.add_argument("--prior-schedule", type=Path, required=True)
    parser.add_argument("--delivery-csv", type=Path, required=True)
    parser.add_argument("--quality-json", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--request-template", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--schedule-smoke", type=Path, required=True)
    parser.add_argument("--branch-key", required=True)
    parser.add_argument("--parent-controller-run-id", type=UUID, required=True)
    parser.add_argument("--epoch-ordinal", type=int, required=True)
    parser.add_argument("--next-round-ordinal", type=int, required=True)
    parser.add_argument("--raw-count", type=int, required=True)
    parser.add_argument("--valid-unique-count", type=int, required=True)
    parser.add_argument("--fully-scored-count", type=int, required=True)
    parser.add_argument("--qualified-count", type=int, required=True)
    parser.add_argument("--delivered-count", type=int, required=True)
    parser.add_argument("--family-count", type=int, required=True)
    parser.add_argument(
        "--generator-allocation-policy",
        choices=("balanced_then_yield_v1", "safety_biased_hydramp_v1"),
        default="balanced_then_yield_v1",
    )
    parser.add_argument("--excluded-attempt-controller-run-id", type=UUID)
    parser.add_argument(
        "--excluded-attempt-run-id", action="append", type=UUID, default=[]
    )
    parser.add_argument("--schedule-output", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("submission is inert without --execute")

    prior_schedule = SevenBranchTopUpSchedule.model_validate(_load(args.prior_schedule))
    request_template = _load(args.request_template)
    preflight = _load(args.preflight)
    if preflight.get("status") != "ready_to_submit_unique_run":
        parser.error("formal preflight is not ready")
    quality_snapshot = _load(args.quality_json)
    quality_progress = quality_snapshot["branches"][args.branch_key]
    quality_progress = {
        key: value
        for key, value in quality_progress.items()
        if key
        in {
            "schema_version",
            "branch_key",
            "quality_quota",
            "quality_qualified_count",
            "archive_counts",
            "underfilled_archives",
        }
    }
    source_run_ids = _source_run_ids(args.delivery_csv, args.branch_key)
    progress = {
        "branch_key": args.branch_key,
        "raw_count": args.raw_count,
        "valid_unique_count": args.valid_unique_count,
        "fully_scored_count": args.fully_scored_count,
        "target_sequence_scored_count": 0,
        "qualified_count": args.qualified_count,
        "delivered_count": args.delivered_count,
        "family_count": args.family_count,
    }
    evidence_core: dict[str, Any] = {
        "source_run_ids": source_run_ids,
        "progress": progress,
        "quality_progress": quality_progress,
        "next_round_ordinal": args.next_round_ordinal,
        "generator_allocation_policy": args.generator_allocation_policy,
    }
    if args.excluded_attempt_controller_run_id is not None:
        if not args.excluded_attempt_run_id:
            parser.error("excluded controller requires an excluded child run")
        evidence_core.update(
            {
                "excluded_attempt_controller_run_id": str(
                    args.excluded_attempt_controller_run_id
                ),
                "excluded_attempt_run_ids": [
                    str(item) for item in args.excluded_attempt_run_id
                ],
                "excluded_attempt_outputs_reused": False,
            }
        )
    evidence = {
        args.branch_key: {
            **evidence_core,
            "snapshot_sha256": sha256_json(evidence_core),
        }
    }
    controller_run_id, child_ids = derive_top_up_seven_branch_run_ids(
        parent_controller_run_id=args.parent_controller_run_id,
        epoch_ordinal=args.epoch_ordinal,
        branch_evidence_sha256_by_key={
            args.branch_key: evidence[args.branch_key]["snapshot_sha256"]
        },
    )
    smoke = _load(args.schedule_smoke)
    if str(controller_run_id) != smoke["controller_run_id"]:
        raise ValueError("formal controller identity drifted from schedule smoke")
    if str(child_ids[args.branch_key]) != smoke["child_run_id"]:
        raise ValueError("formal child identity drifted from schedule smoke")
    manifest = _load(args.target_manifest)
    schedule = build_top_up_seven_branch_schedule(
        request_template=request_template,
        submission_preflight=preflight,
        design_contract=prior_schedule.design_contract,
        target_manifest=manifest,
        target_manifest_sha256=sha256_file(args.target_manifest),
        parent_controller_run_id=args.parent_controller_run_id,
        controller_run_id=controller_run_id,
        epoch_ordinal=args.epoch_ordinal,
        branch_evidence=evidence,
        child_run_ids_by_key=child_ids,
    )
    args.schedule_output.parent.mkdir(parents=True, exist_ok=True)
    args.schedule_output.write_text(
        schedule.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )

    async def _run() -> dict[str, Any]:
        reservation = await reserve_seven_branch_top_up_schedule(
            schedule=schedule, target_manifest=manifest
        )
        result: dict[str, Any] = {
            "schedule_sha256": schedule.sha256(),
            "reservation": reservation,
        }
        if args.submit:
            result["submission"] = await submit_reserved_seven_branch_top_up_schedule(
                schedule=schedule
            )
        return result

    receipt = asyncio.run(_run())
    args.receipt_output.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_output.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
