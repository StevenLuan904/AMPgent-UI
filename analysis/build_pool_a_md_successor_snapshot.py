"""Build the exact Pool-A increment not present in an immutable MD snapshot."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def identity(row: dict) -> tuple[str, str, str, str]:
    return tuple(
        str(row[key]) for key in ("run_id", "candidate_id", "target_key", "sequence_sha256")
    )


def build(current: dict, scheduled: dict) -> dict:
    scheduled_ids = {identity(row) for row in scheduled["pool_a_all"]}
    rows = [row for row in current["pool_a_all"] if identity(row) not in scheduled_ids]
    if len({identity(row) for row in rows}) != len(rows):
        raise ValueError("duplicate Pool-A identity in successor snapshot")
    return {
        "schema_version": "ampgent.pool-a-md-successor-snapshot.1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source_observed_at": current.get("observed_at"),
        "prior_snapshot_candidate_count": len(scheduled_ids),
        "successor_candidate_count": len(rows),
        "pool_a_all": rows,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--current", type=Path, required=True)
    p.add_argument("--scheduled", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    payload = build(json.loads(a.current.read_text()), json.loads(a.scheduled.read_text()))
    a.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = a.output.with_name(f".{a.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(a.output)


if __name__ == "__main__":
    main()
