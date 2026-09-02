from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from sqlalchemy import text

from pepagent.db.session import SessionFactory


async def verify(run_id: uuid.UUID) -> dict[str, object]:
    async with SessionFactory() as session:
        identity = (
            await session.execute(
                text(
                    "select r.status, count(distinct c.id), count(e.id), "
                    "count(e.id) filter (where e.subject_run_id <> c.run_id) "
                    "from experiment_runs r "
                    "left join candidates c on c.run_id=r.id "
                    "left join evaluations e on e.candidate_id=c.id "
                    "where r.id=:run_id group by r.status"
                ),
                {"run_id": run_id},
            )
        ).one()
        groups = (
            await session.execute(
                text(
                    "select evidence_role, model_release_key, applicability_status, "
                    "conflict_status, count(*) from evaluations "
                    "where subject_run_id=:run_id "
                    "group by evidence_role, model_release_key, applicability_status, "
                    "conflict_status order by evidence_role, model_release_key, conflict_status"
                ),
                {"run_id": run_id},
            )
        ).all()
    return {
        "run_id": str(run_id),
        "status": identity[0],
        "candidate_count": identity[1],
        "evaluation_count": identity[2],
        "subject_run_drift_count": identity[3],
        "evidence_groups": [list(row) for row in groups],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=uuid.UUID, required=True)
    parser.add_argument("--expected-candidates", type=int)
    parser.add_argument("--expected-evaluations", type=int)
    args = parser.parse_args()
    payload = asyncio.run(verify(args.run_id))
    if (
        args.expected_candidates is not None
        and payload["candidate_count"] != args.expected_candidates
    ):
        raise ValueError("materialized candidate count differs")
    if (
        args.expected_evaluations is not None
        and payload["evaluation_count"] != args.expected_evaluations
    ):
        raise ValueError("materialized evaluation count differs")
    if payload["status"] != "succeeded" or payload["subject_run_drift_count"] != 0:
        raise ValueError("materialized run identity/status verification failed")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
