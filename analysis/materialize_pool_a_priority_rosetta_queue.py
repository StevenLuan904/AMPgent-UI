from __future__ import annotations

import argparse
import asyncio
import csv
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import bindparam, text

from pepagent.db.session import SessionFactory

LOOKUP = text(
    """
    SELECT c.id AS candidate_id,c.run_id,c.sequence,c.sequence_sha256,c.generation,
           t.accession
    FROM candidates c
    JOIN experiment_runs r ON r.id=c.run_id
    JOIN targets t ON t.id=r.target_id
    WHERE c.run_id IN :run_ids AND c.sequence_sha256 IN :sequence_hashes
    """
).bindparams(bindparam("run_ids", expanding=True), bindparam("sequence_hashes", expanding=True))


async def run(args: argparse.Namespace) -> dict[str, object]:
    with args.source.open(encoding="utf-8-sig", newline="") as stream:
        source = list(csv.DictReader(stream))
    run_ids = sorted({uuid.UUID(row["subject_run_id"]) for row in source})
    hashes = sorted({row["sequence_sha256"] for row in source})
    async with SessionFactory() as session:
        found = (
            await session.execute(LOOKUP, {"run_ids": run_ids, "sequence_hashes": hashes})
        ).mappings().all()
    lookup = {(str(row["run_id"]), row["sequence_sha256"]): row for row in found}
    if len(lookup) != len(source):
        missing = [
            [row["subject_run_id"], row["sequence_sha256"]]
            for row in source
            if (row["subject_run_id"], row["sequence_sha256"]) not in lookup
        ]
        raise ValueError(f"exact PostgreSQL mappings missing: {missing[:5]!r} ({len(missing)})")
    by_target: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in source:
        by_target[row["target_key"]].append(row)
    for rows in by_target.values():
        rows.sort(key=lambda row: int(row["pool_a_pre_rosetta_rank"]))
    ordered: list[dict[str, str]] = []
    targets = ("acea", "gyra", "pbp2a", "fgf2", "angpt1", "vegfa")
    while any(by_target.values()):
        for target in targets:
            if by_target[target]:
                ordered.append(by_target[target].pop(0))
    fieldnames = (
        "branch_key", "generation", "proposal_rank", "sequence", "sequence_sha256",
        "guruprasad_instability_index", "candidate_id", "subject_run_id",
        "source_proposal_id", "family_key_80_80", "activity_model_support_count",
        "challenger_conflict_status", "formal_12_complete", "display_eligible",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for rank, source_row in enumerate(ordered, 1):
            db = lookup[(source_row["subject_run_id"], source_row["sequence_sha256"])]
            if db["sequence"] != source_row["sequence"]:
                raise ValueError("sequence identity differs after exact mapping")
            writer.writerow(
                {
                    "branch_key": source_row["target_key"],
                    "generation": db["generation"],
                    "proposal_rank": rank,
                    "sequence": db["sequence"],
                    "sequence_sha256": db["sequence_sha256"],
                    "guruprasad_instability_index": source_row["guruprasad_instability_index"],
                    "candidate_id": db["candidate_id"],
                    "subject_run_id": db["run_id"],
                    "source_proposal_id": source_row["candidate_id"],
                    "family_key_80_80": source_row["family_key_80_80"],
                    "activity_model_support_count": source_row["activity_model_support_count"],
                    "challenger_conflict_status": source_row["challenger_conflict_status"],
                    "formal_12_complete": "true",
                    "display_eligible": "true",
                }
            )
    manifest = {
        "schema_version": "ampgent.rosetta-pool-a-priority.1",
        "created_at": datetime.now(UTC).isoformat(),
        "source_queue": str(args.source),
        "active_candidate_ids": [
            str(lookup[(row["subject_run_id"], row["sequence_sha256"])]["candidate_id"])
            for row in ordered
        ],
        "paused_candidate_ids": [],
        "active_count": len(ordered),
        "paused_count": 0,
        "identity": "subject_run_id+sequence_sha256 -> PostgreSQL candidate UUID",
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {"candidate_count": len(ordered), "output": str(args.output), "manifest": str(args.manifest)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), sort_keys=True))


if __name__ == "__main__":
    main()
