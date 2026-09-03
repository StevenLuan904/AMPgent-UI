"""Verify one scored-lineage materialization without changing PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import uuid
from pathlib import Path

import asyncpg


async def verify(dsn: str, run_id: str, input_csv: Path) -> dict[str, object]:
    expected = {
        row["sequence_sha256"]: row for row in csv.DictReader(
            input_csv.open(encoding="utf-8-sig", newline="")
        )
    }
    connection = await asyncpg.connect(dsn, timeout=5, command_timeout=15)
    try:
        run = await connection.fetchrow(
            """
            select r.id::text as run_id, r.status, t.name as target_name
            from experiment_runs r join targets t on t.id = r.target_id
            where r.id = $1::uuid
            """,
            run_id,
        )
        candidates = await connection.fetch(
            """
            select id::text as candidate_id, sequence_sha256, generation, run_id::text
            from candidates where run_id = $1::uuid order by proposal_rank, id
            """,
            run_id,
        )
        evaluations = await connection.fetch(
            """
            select e.candidate_id::text as candidate_id, e.metric_name,
                   e.evidence_role, e.evidence_family, e.status,
                   e.subject_run_id::text as subject_run_id,
                   e.tool_call_id::text as tool_call_id
            from evaluations e where e.subject_run_id = $1::uuid
            order by e.candidate_id, e.evidence_role, e.metric_name
            """,
            run_id,
        )
        calls = await connection.fetch(
            """
            select distinct tc.id::text as tool_call_id, tc.status, tc.tool_name
            from tool_calls tc join evaluations e on e.tool_call_id = tc.id
            where e.subject_run_id = $1::uuid
            """,
            run_id,
        )
    finally:
        await connection.close()
    eval_by_candidate: dict[str, list[dict[str, object]]] = {}
    for row in evaluations:
        eval_by_candidate.setdefault(str(row["candidate_id"]), []).append(dict(row))
    candidate_payload = []
    identity_drift = []
    for candidate in candidates:
        digest = str(candidate["sequence_sha256"])
        if digest not in expected:
            identity_drift.append(digest)
        candidate_payload.append(
            {
                "candidate_id": str(candidate["candidate_id"]),
                "sequence_sha256": digest,
                "generation": int(candidate["generation"]),
                "evaluation_count": len(eval_by_candidate.get(str(candidate["candidate_id"]), [])),
                "evaluation_metric_names": sorted(
                    str(item["metric_name"])
                    for item in eval_by_candidate.get(str(candidate["candidate_id"]), [])
                ),
            }
        )
    payload: dict[str, object] = {
        "schema_version": "ampgent.pbp2a-pepflow-same-domain-pg-verification.1",
        "run_id": run_id,
        "run": None if run is None else dict(run),
        "candidate_count": len(candidates),
        "evaluation_count": len(evaluations),
        "tool_call_count": len(calls),
        "tool_calls": [dict(item) for item in calls],
        "candidate_evidence": candidate_payload,
        "expected_input_count": len(expected),
        "identity_drift_count": len(identity_drift),
        "identity_drift_hashes": identity_drift,
        "per_candidate_evidence_count_17": all(
            item["evaluation_count"] == 17 for item in candidate_payload
        ),
        "historical_run_modified": False,
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    uuid.UUID(args.run_id)
    payload = asyncio.run(verify(args.dsn, args.run_id, args.input_csv))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
