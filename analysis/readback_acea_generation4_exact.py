"""Read back the two AceA generation-4 identities by run and candidate UUID."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from pepagent.db.models import Candidate, Evaluation, ToolCall
from pepagent.db.session import SessionFactory

RUN_ID = uuid.UUID("c6718e9b-15be-5197-87b0-ffe9d78d2ed7")
CANDIDATE_IDS = tuple(
    uuid.UUID(value)
    for value in (
        "91309ee5-0f3c-4796-8367-851f24a68fb2",
        "a0bb708e-276a-4c9d-aa48-381a4532bb5c",
    )
)
TOOL_CALL_ID = uuid.UUID("f93c7663-5b34-4f3b-9bb0-8ad937a703ab")
EXPECTED_EVALUATIONS_PER_CANDIDATE = 17


async def exact_readback(
    run_id: uuid.UUID = RUN_ID,
    candidate_ids: tuple[uuid.UUID, ...] = CANDIDATE_IDS,
    tool_call_id: uuid.UUID = TOOL_CALL_ID,
) -> dict[str, Any]:
    """Use one session and only indexed identity predicates; never scan by sequence."""
    try:
        async with SessionFactory() as session:
            await session.execute(text("SET statement_timeout = '15000ms'"))
            candidate_rows = list(
                (
                    await session.execute(
                        select(Candidate.id, Candidate.run_id, Candidate.sequence_sha256).where(
                            Candidate.run_id == run_id,
                            Candidate.id.in_(candidate_ids),
                        )
                    )
                ).all()
            )
            evaluation_rows = list(
                (
                    await session.execute(
                        select(
                            Evaluation.candidate_id,
                            Evaluation.subject_run_id,
                            Evaluation.tool_call_id,
                            Evaluation.model_release_key,
                        ).where(
                            Evaluation.subject_run_id == run_id,
                            Evaluation.candidate_id.in_(candidate_ids),
                            Evaluation.tool_call_id == tool_call_id,
                        )
                    )
                ).all()
            )
            tool_call = await session.get(ToolCall, tool_call_id)
    except Exception as exc:  # fail closed; receipt contains no raw connection error
        return {
            "schema_version": "ampgent.acea-generation4-exact-readback.1",
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "status": "unavailable",
            "error_class": type(exc).__name__,
            "query_mode": "exact_run_id+candidate_id",
            "sequence_history_scan": False,
            "run_id": str(run_id),
            "candidate_ids": [str(value) for value in candidate_ids],
            "tool_call_id": str(tool_call_id),
        }

    candidate_by_id = {row.id: row for row in candidate_rows}
    evaluation_counts = {candidate_id: 0 for candidate_id in candidate_ids}
    drift = 0
    for row in evaluation_rows:
        if row.candidate_id not in evaluation_counts or row.subject_run_id != run_id:
            drift += 1
        else:
            evaluation_counts[row.candidate_id] += 1
    drift += len(set(candidate_ids) - set(candidate_by_id))
    drift += sum(row.run_id != run_id for row in candidate_rows)
    drift += int(tool_call is None or tool_call.run_id != run_id)
    drift += sum(
        count != EXPECTED_EVALUATIONS_PER_CANDIDATE for count in evaluation_counts.values()
    )
    verified = (
        set(candidate_by_id) == set(candidate_ids)
        and len(candidate_rows) == len(candidate_ids)
        and len(evaluation_rows) == len(candidate_ids) * EXPECTED_EVALUATIONS_PER_CANDIDATE
        and tool_call is not None
        and tool_call.run_id == run_id
        and drift == 0
    )
    return {
        "schema_version": "ampgent.acea-generation4-exact-readback.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "status": "readback_verified" if verified else "contract_failed",
        "query_mode": "exact_run_id+candidate_id",
        "sequence_history_scan": False,
        "run_id": str(run_id),
        "candidate_ids": [str(value) for value in candidate_ids],
        "candidate_count": len(candidate_rows),
        "evaluation_count": len(evaluation_rows),
        "evaluations_per_candidate": {
            str(candidate_id): evaluation_counts[candidate_id] for candidate_id in candidate_ids
        },
        "tool_call_id": str(tool_call_id),
        "tool_call_run_binding": bool(tool_call is not None and tool_call.run_id == run_id),
        "identity_drift": drift,
        "model_release_keys": sorted(
            {row.model_release_key for row in evaluation_rows if row.model_release_key}
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", type=uuid.UUID, default=RUN_ID)
    parser.add_argument("--candidate-ids", type=uuid.UUID, nargs="+", default=CANDIDATE_IDS)
    parser.add_argument("--tool-call-id", type=uuid.UUID, default=TOOL_CALL_ID)
    args = parser.parse_args()
    receipt = asyncio.run(exact_readback(args.run_id, tuple(args.candidate_ids), args.tool_call_id))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
