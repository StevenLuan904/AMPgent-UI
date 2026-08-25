from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import boto3
from botocore.config import Config

from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.seven_branch_design import SevenBranchTopUpSchedule
from pepagent.seven_branch_schedule import (
    build_top_up_seven_branch_schedule,
    derive_top_up_seven_branch_run_ids,
)

_RUNTIME_FIELDS = {
    "run_id",
    "controller_run_id",
    "execution_contract",
    "exploration_round",
    "seven_branch_round",
    "submission_preflight",
    "multitarget_plan_template",
    "structure_runtime_by_target_key",
    "boltz_seeds",
    "quality_continuation",
}


def _load_s3_json(*, bucket: str, key: str) -> dict[str, Any]:
    endpoint = os.environ["PEPAGENT_S3_ENDPOINT"]
    access_key = os.environ["PEPAGENT_S3_ACCESS_KEY"]
    secret_key = os.environ["PEPAGENT_S3_SECRET_KEY"]
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(signature_version="s3v4"),
    )
    body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body.decode("utf-8"))


def _source_run_ids(delivery_csv: Path, branch_key: str) -> list[str]:
    with delivery_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["branch_key"] == branch_key]
    return list(dict.fromkeys(row["source_run_id"] for row in rows))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prior-schedule-bucket", required=True)
    parser.add_argument("--prior-schedule-key", required=True)
    parser.add_argument("--delivery-csv", type=Path, required=True)
    parser.add_argument("--quality-json", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--request-template", type=Path, required=True)
    parser.add_argument("--branch-key", default="target_agnostic_amp")
    parser.add_argument("--parent-controller-run-id", type=UUID, required=True)
    parser.add_argument("--epoch-ordinal", type=int, required=True)
    parser.add_argument("--next-round-ordinal", type=int, required=True)
    parser.add_argument("--raw-count", type=int, required=True)
    parser.add_argument("--valid-unique-count", type=int, required=True)
    parser.add_argument("--fully-scored-count", type=int, required=True)
    parser.add_argument("--qualified-count", type=int, required=True)
    parser.add_argument("--delivered-count", type=int, required=True)
    parser.add_argument("--family-count", type=int, required=True)
    parser.add_argument("--excluded-attempt-controller-run-id", type=UUID)
    parser.add_argument(
        "--excluded-attempt-run-id",
        dest="excluded_attempt_run_ids",
        action="append",
        type=UUID,
        default=[],
    )
    parser.add_argument("--current-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    prior_payload = _load_s3_json(
        bucket=args.prior_schedule_bucket,
        key=args.prior_schedule_key,
    )
    prior_schedule = SevenBranchTopUpSchedule.model_validate(prior_payload)
    prior_epoch_branch = next(
        item for item in prior_schedule.branches if item.branch_key == args.branch_key
    )
    prior_request = prior_epoch_branch.frozen_round.request
    request_template = json.loads(args.request_template.read_text(encoding="utf-8"))
    leaked_runtime_fields = sorted(_RUNTIME_FIELDS.intersection(request_template))
    if leaked_runtime_fields:
        parser.error(
            "--request-template contains runtime fields: "
            + ", ".join(leaked_runtime_fields)
        )
    submission_preflight = prior_request["submission_preflight"]
    quality_snapshot = json.loads(args.quality_json.read_text(encoding="utf-8"))
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
    evidence_core = {
        "source_run_ids": source_run_ids,
        "progress": progress,
        "quality_progress": quality_progress,
        "next_round_ordinal": args.next_round_ordinal,
    }
    if args.excluded_attempt_controller_run_id is not None:
        if not args.excluded_attempt_run_ids:
            parser.error(
                "--excluded-attempt-controller-run-id requires at least one "
                "--excluded-attempt-run-id"
            )
        evidence_core.update(
            {
                "excluded_attempt_controller_run_id": str(
                    args.excluded_attempt_controller_run_id
                ),
                "excluded_attempt_run_ids": [
                    str(item) for item in args.excluded_attempt_run_ids
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
    target_manifest = json.loads(args.target_manifest.read_text(encoding="utf-8"))
    schedule = build_top_up_seven_branch_schedule(
        request_template=request_template,
        submission_preflight=submission_preflight,
        design_contract=prior_schedule.design_contract,
        target_manifest=target_manifest,
        target_manifest_sha256=sha256_file(args.target_manifest),
        parent_controller_run_id=args.parent_controller_run_id,
        controller_run_id=controller_run_id,
        epoch_ordinal=args.epoch_ordinal,
        branch_evidence=evidence,
        child_run_ids_by_key=child_ids,
    )
    epoch_branch = schedule.branches[0]
    frozen_request = epoch_branch.frozen_round.request
    worker_source_revision = str(frozen_request.get("worker_source_revision", ""))
    result = {
        "schema_version": "ampgent.seven-branch-quality-schedule-smoke.1",
        "status": "schedule_frozen_not_submitted",
        "branch_key": args.branch_key,
        "source_run_ids": source_run_ids,
        "evidence_snapshot_sha256": evidence[args.branch_key]["snapshot_sha256"],
        "quality_progress_sha256": frozen_request["quality_continuation"][
            "quality_progress_sha256"
        ],
        "schedule_schema_version": schedule.schema_version,
        "schedule_sha256": schedule.sha256(),
        "controller_run_id": str(schedule.controller_run_id),
        "child_run_id": str(epoch_branch.frozen_round.run_id),
        "workflow_id": epoch_branch.frozen_round.workflow_id,
        "quality_top_up_plan": epoch_branch.top_up_plan.model_dump(mode="json"),
        "excluded_attempt_controller_run_id": frozen_request[
            "quality_continuation"
        ].get("excluded_attempt_controller_run_id"),
        "excluded_attempt_run_ids": frozen_request["quality_continuation"].get(
            "excluded_attempt_run_ids", []
        ),
        "excluded_attempt_outputs_reused": frozen_request[
            "quality_continuation"
        ].get("excluded_attempt_outputs_reused"),
        "expected_raw_occurrences": frozen_request["execution_contract"][
            "expected_raw_occurrences"
        ],
        "worker_source_revision": worker_source_revision,
        "current_head": args.current_head,
        "worker_source_matches_current_head": worker_source_revision == args.current_head,
        "preflight_reused_for_submission": False,
        "temporal_submitted": False,
        "formal_runs_reserved": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
