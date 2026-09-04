"""Read back one exact-once PBP2a hybrid materialization from PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from pepagent.db.models import Candidate, Evaluation, LifecycleEvent, ToolCall
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_text


def _hashes(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    return {str(row["sequence_sha256"]): str(row["sequence"]) for row in rows}


async def verify(materialization_receipt: Path, candidate_input: Path) -> dict[str, Any]:
    receipt = json.loads(
        await asyncio.to_thread(materialization_receipt.read_text, encoding="utf-8")
    )
    run_id = UUID(receipt["operational_run_id"])
    expected_tool_call_id = UUID(receipt["tool_call_id"])
    expected_sequences = _hashes(candidate_input)
    async with SessionFactory() as session:
        await session.execute(text("SET statement_timeout = '60000ms'"))
        await session.execute(
            text("SET application_name = 'ampgent-pbp2a-run2-materialization-readback'")
        )
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(
                    Candidate.run_id == run_id,
                    Candidate.sequence_sha256.in_(list(expected_sequences)),
                )
                .order_by(Candidate.sequence_sha256, Candidate.id)
            )
        )
        candidate_ids = [candidate.id for candidate in candidates]
        evaluations = list(
            await session.scalars(
                select(Evaluation)
                .where(
                    Evaluation.subject_run_id == run_id,
                    Evaluation.candidate_id.in_(candidate_ids),
                )
                .order_by(Evaluation.candidate_id, Evaluation.id)
            )
        )
        tool_calls = list(
            await session.scalars(
                select(ToolCall).where(
                    ToolCall.id == expected_tool_call_id, ToolCall.run_id == run_id
                )
            )
        )
        events = list(
            await session.scalars(
                select(LifecycleEvent).where(
                    LifecycleEvent.aggregate_type == "run",
                    LifecycleEvent.aggregate_id == run_id,
                    LifecycleEvent.event_type
                    == "autoresearch.scored_lineage.materialized",
                )
            )
        )

    candidate_hashes = {candidate.sequence_sha256 for candidate in candidates}
    identity_drift = [
        candidate.sequence_sha256
        for candidate in candidates
        if expected_sequences.get(candidate.sequence_sha256) != candidate.sequence
        or sha256_text(candidate.sequence) != candidate.sequence_sha256
    ]
    groups: dict[str, dict[str, Any]] = {}
    per_candidate: dict[str, int] = {}
    for evaluation in evaluations:
        per_candidate[str(evaluation.candidate_id)] = (
            per_candidate.get(str(evaluation.candidate_id), 0) + 1
        )
        key = f"{evaluation.evidence_role}:{evaluation.model_release_key}"
        item = groups.setdefault(
            key,
            {
                "evidence_role": evaluation.evidence_role,
                "model_release_key": evaluation.model_release_key,
                "count": 0,
                "metric_names": [],
            },
        )
        item["count"] += 1
        item["metric_names"].append(evaluation.metric_name)
    exact_binding_errors = [
        str(evaluation.id)
        for evaluation in evaluations
        if evaluation.subject_run_id != run_id
        or evaluation.candidate_id not in candidate_ids
        or evaluation.tool_call_id != expected_tool_call_id
        or not evaluation.model_release_key
    ]
    release_counts = sorted(groups.values(), key=lambda item: item["evidence_role"] or "")
    return {
        "schema_version": "ampgent.pbp2a-pepflow-hybrid.materialization-readback.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "target_key": "PBP2a",
        "run_id": str(run_id),
        "tool_call_id": str(expected_tool_call_id),
        "candidate_count": len(candidates),
        "authoritative_candidates": [
            {
                "candidate_id": str(candidate.id),
                "sequence_sha256": candidate.sequence_sha256,
                "generation": candidate.generation,
            }
            for candidate in candidates
        ],
        "evaluation_count": len(evaluations),
        "evaluation_count_per_candidate": sorted(per_candidate.values()),
        "release_counts": release_counts,
        "tool_call_count": len(tool_calls),
        "tool_call_statuses": sorted({call.status for call in tool_calls}),
        "materialization_event_count": len(events),
        "exact_subject_run_binding": not exact_binding_errors,
        "exact_tool_call_binding": not exact_binding_errors,
        "identity_drift_count": len(identity_drift),
        "identity_drift_hashes": identity_drift,
        "candidate_hash_coverage": len(candidate_hashes),
        "expected_hash_count": len(expected_sequences),
        "replay_check": {
            "status": "verified_by_deterministic_receipt",
            "global_exact_replay_skip_count": receipt.get("global_exact_replay_skip_count", 0),
            "historical_runs_modified": receipt.get("historical_runs_modified", True),
        },
        "drift": 0 if not identity_drift and not exact_binding_errors else 1,
        "complete": (
            len(candidates) == 4
            and len(evaluations) == 68
            and sorted(per_candidate.values()) == [17, 17, 17, 17]
            and len(tool_calls) == 1
            and tool_calls[0].status == "succeeded"
            and len(events) == 1
            and not identity_drift
            and not exact_binding_errors
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--materialization-receipt", type=Path, required=True)
    parser.add_argument("--candidate-input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = asyncio.run(verify(args.materialization_receipt, args.candidate_input))
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    if not payload["complete"]:
        raise SystemExit("materialization readback contract failed")


if __name__ == "__main__":
    main()
