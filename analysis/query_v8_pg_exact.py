from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path

import asyncpg


async def query(args: argparse.Namespace) -> None:
    with args.input.open(encoding="utf-8-sig", newline="") as stream:
        hashes = [row["sequence_sha256"] for row in csv.DictReader(stream)]
    batches = [hashes[index : index + 4] for index in range(0, len(hashes), 4)]
    matches: list[dict[str, str]] = []
    attempts: list[dict[str, object]] = []
    for index, batch in enumerate(batches):
        for retry in range(1, 4):
            try:
                conn = await asyncio.wait_for(
                    asyncpg.connect(args.dsn, command_timeout=30), timeout=30
                )
                try:
                    rows = await conn.fetch(
                        "SELECT id::text AS candidate_id, run_id::text AS run_id, "
                        "sequence_sha256::text AS sequence_sha256 "
                        "FROM candidates WHERE sequence_sha256 = ANY($1::text[])",
                        batch,
                    )
                finally:
                    await conn.close()
                found = [dict(row) for row in rows]
                matches.extend(found)
                attempts.append(
                    {
                        "batch": index,
                        "retry": retry,
                        "status": "succeeded",
                        "count": len(found),
                    }
                )
                break
            except Exception as exc:  # bounded operational evidence
                attempts.append(
                    {
                        "batch": index,
                        "retry": retry,
                        "status": "failed",
                        "error": repr(exc),
                    }
                )
                if retry == 3:
                    break
    matched_hashes = {row["sequence_sha256"] for row in matches}
    payload = {
        "schema_version": "ampgent.v8.pg-exact.1",
        "input_sha256": __import__("hashlib").sha256(args.input.read_bytes()).hexdigest(),
        "input_count": len(hashes),
        "batch_size": 4,
        "timeout_seconds": 30,
        "max_retries": 3,
        "matches": sorted(matches, key=lambda row: (row["sequence_sha256"], row["candidate_id"])),
        "historical_exact_match_count": len(matched_hashes),
        "pg_new_count": len(hashes) - len(matched_hashes),
        "pending_batch_count": sum(
            1
            for batch in range(len(batches))
            if not any(
                item["batch"] == batch and item["status"] == "succeeded"
                for item in attempts
            )
        ),
        "attempts": attempts,
        "materialization_allowed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dsn", default="postgresql://pepagent:change-me@localhost:55432/pepagent")
    asyncio.run(query(parser.parse_args()))


if __name__ == "__main__":
    main()
