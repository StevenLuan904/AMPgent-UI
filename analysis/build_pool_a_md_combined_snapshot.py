"""Combine exact, identity-disjoint Pool-A MD cohorts."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def identity(row: dict) -> tuple[str, str, str, str]:
    return tuple(
        str(row[key])
        for key in ("run_id", "candidate_id", "target_key", "sequence_sha256")
    )


def combine(cohorts: list[dict]) -> dict:
    rows = [row for cohort in cohorts for row in cohort["pool_a_all"]]
    identities = [identity(row) for row in rows]
    candidate_ids = [str(row["candidate_id"]) for row in rows]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate exact Pool-A identity across MD cohorts")
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("candidate_id reused across MD cohorts")
    return {
        "schema_version": "ampgent.pool-a-md-combined-snapshot.1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "cohort_candidate_counts": [len(cohort["pool_a_all"]) for cohort in cohorts],
        "combined_candidate_count": len(rows),
        "pool_a_all": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.cohort) < 2:
        raise ValueError("at least two cohorts are required")
    payload = combine(
        [json.loads(path.read_text(encoding="utf-8")) for path in args.cohort]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)


if __name__ == "__main__":
    main()
