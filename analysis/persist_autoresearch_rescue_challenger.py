from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    persist_operational_call,
)
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json


def _calibrated_probability(raw_score: float, coefficient: float, intercept: float) -> float:
    clipped = min(max(raw_score, 1e-12), 1.0 - 1e-12)
    calibrated_logit = coefficient * math.log(clipped / (1.0 - clipped)) + intercept
    if calibrated_logit >= 0:
        return 1.0 / (1.0 + math.exp(-calibrated_logit))
    exp_value = math.exp(calibrated_logit)
    return exp_value / (1.0 + exp_value)


async def _run(args: argparse.Namespace) -> None:
    if len(args.source_commit) != 40 or set(args.source_commit) - set("0123456789abcdef"):
        raise ValueError("source commit must be a lowercase SHA-1")
    worker_input = json.loads(args.worker_input.read_text(encoding="utf-8"))
    worker_output = json.loads(args.worker_output.read_text(encoding="utf-8"))
    calibration = json.loads(args.calibration.read_text(encoding="utf-8-sig"))
    expected = worker_input["candidates"]
    records = worker_output["records"]
    if len(expected) != len(records):
        raise ValueError("challenger input/output coverage drifted")
    coefficient = float(calibration["calibrator"]["coefficient"])
    intercept = float(calibration["calibrator"]["intercept"])
    threshold = float(calibration["threshold_policy"]["calibrated_probability_threshold"])
    reviews = []
    for expected_row, record in zip(expected, records, strict=True):
        if any(
            record[key] != expected_row[key]
            for key in ("candidate_id", "sequence", "sequence_sha256", "target_key")
        ):
            raise ValueError("challenger input/output identity drifted")
        probability = _calibrated_probability(
            float(record["hemopi2_classification_score"]), coefficient, intercept
        )
        threshold_exceeded = probability >= threshold
        raw_risk = int(record["hemopi2_classification_label"]) == 1
        hc50_risk = float(record["hemopi2_hc50_um"]) < 100.0
        conflict = threshold_exceeded or raw_risk or hc50_risk
        reviews.append(
            {
                **record,
                "calibrated_hemolysis_probability": probability,
                "calibration_risk_threshold": threshold,
                "calibration_threshold_exceeded": threshold_exceeded,
                "reported_hc50_below_100_um": hc50_risk,
                "conflict_status": (
                    "cross_model_disagreement_retained" if conflict else "no_conflict"
                ),
                "unresolved_severe_conflict": False,
                "verified_challenger_models": "hemopi2_v27",
                "missing_verified_runtimes": "apex;peptiverse",
                "candidate_hard_gate_allowed": False,
            }
        )
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(reviews[0]))
        writer.writeheader()
        writer.writerows(reviews)

    input_sha256 = sha256_file(args.worker_input)
    output_sha256 = sha256_file(args.worker_output)
    review_csv_sha256 = sha256_file(args.output_csv)
    persisted = []
    for branch_key in sorted({row["target_key"] for row in reviews}):
        branch_rows = [row for row in reviews if row["target_key"] == branch_key]
        now = datetime.now(UTC)
        record = OperationalCallRecord(
            operation_key=(
                f"safety-rescue-hemopi2-challenger:{args.source_commit}:"
                f"{output_sha256}:{branch_key}"
            ),
            target_key=branch_key,  # type: ignore[arg-type]
            purpose="challenger",
            tool_name="autoresearch_hemopi2_v27_challenger",
            tool_version="v1",
            status="succeeded",
            input_payload={
                "worker_input_sha256": input_sha256,
                "candidate_count": len(branch_rows),
            },
            parameters={
                "calibration_sha256": sha256_file(args.calibration),
                "candidate_hard_gate_allowed": False,
            },
            execution_context={
                "source_commit": args.source_commit,
                "execution_mode": "isolated_cpu_network_disabled",
                "gpu_used": False,
                "temporal_used": False,
                "historical_run_modified": False,
            },
            output_payload={
                "worker_output_sha256": output_sha256,
                "review_csv_sha256": review_csv_sha256,
                "reviews": branch_rows,
                "missing_verified_runtimes": ["apex", "peptiverse"],
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
        "schema_version": "ampgent.autoresearch-rescue-challenger-receipt.1",
        "persisted_at_utc": datetime.now(UTC).isoformat(),
        "source_commit": args.source_commit,
        "worker_input_sha256": input_sha256,
        "worker_output_sha256": output_sha256,
        "calibration_sha256": sha256_file(args.calibration),
        "review_csv_sha256": review_csv_sha256,
        "candidate_count": len(reviews),
        "conflict_count": sum(
            row["conflict_status"] == "cross_model_disagreement_retained" for row in reviews
        ),
        "missing_verified_runtimes": ["apex", "peptiverse"],
        "persisted": persisted,
        "historical_run_modified": False,
        "workflow_submitted": False,
        "gpu_task_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    args.output_json.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-input", type=Path, required=True)
    parser.add_argument("--worker-output", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
