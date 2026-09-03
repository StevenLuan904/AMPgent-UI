from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path

import asyncpg

from pepagent.provenance.hashing import sha256_file, sha256_json


async def _authoritative_ids(
    database_url: str, run_id: str, hashes: list[str]
) -> dict[str, str]:
    url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(url, timeout=5, command_timeout=10)
    try:
        rows = await conn.fetch(
            """
            select sequence_sha256, id
            from candidates
            where run_id = $1 and sequence_sha256 = any($2::text[])
            order by sequence_sha256, id
            """,
            run_id,
            hashes,
        )
    finally:
        await conn.close()
    matches: dict[str, list[str]] = {}
    for row in rows:
        matches.setdefault(str(row["sequence_sha256"]), []).append(str(row["id"]))
    unresolved = sorted(
        sequence_hash
        for sequence_hash in hashes
        if len(matches.get(sequence_hash, [])) != 1
    )
    if unresolved:
        raise ValueError(
            "authoritative candidate identity unresolved for run "
            f"{run_id}: {','.join(unresolved)}"
        )
    return {sequence_hash: values[0] for sequence_hash, values in matches.items()}


def export(args: argparse.Namespace) -> None:
    scores = list(csv.DictReader(args.scores.open(encoding="utf-8-sig", newline="")))
    qd = json.loads(args.qd.read_text(encoding="utf-8"))
    challenger = {
        row["sequence_sha256"]: row
        for row in csv.DictReader(args.challenger.open(encoding="utf-8-sig", newline=""))
    }
    contributions = {
        f"proposal-{item['candidate_id'][:20]}": item
        for item in qd.get("contributions", [])
        if item.get("contribution") in {
            "empty_cell",
            "incumbent_replacement",
            "same_cell_non_elite",
        }
    }
    selected = []
    for row in scores:
        if row.get("excellent_sequence_stage_calibrated") != "true":
            continue
        contribution = contributions.get(row["candidate_id"])
        if contribution is None:
            continue
        review = challenger.get(row["sequence_sha256"])
        if review is None:
            raise ValueError(f"missing challenger row for {row['sequence_sha256']}")
        selected.append({
            "run_id": args.run_id,
            "candidate_id": row["candidate_id"],
            "source_proposal_id": row.get("candidate_id", ""),
            "sequence": row["sequence"],
            "sequence_sha256": row["sequence_sha256"],
            "target_key": row["branch_key"],
            "target_cell": row.get("target_cell", ""),
            "actual_cell": contribution.get("cell_id", ""),
            "qd_contribution": contribution["contribution"],
            "quality": row.get("quality", ""),
            "activity_support_calibrated": row.get("activity_model_support_count_calibrated", ""),
            "challenger_conflict_status": review.get("challenger_conflict_status", ""),
            "apex_status": "runtime_unavailable",
            "peptiverse_status": "runtime_unavailable",
            "structure_status": "not_started",
        })
    if args.database_url:
        ids = asyncio.run(
            _authoritative_ids(
                args.database_url,
                args.run_id,
                sorted({row["sequence_sha256"] for row in selected}),
            )
        )
        for row in selected:
            row["source_proposal_id"] = row["candidate_id"]
            row["candidate_id"] = ids[row["sequence_sha256"]]
    selected.sort(key=lambda row: (row["target_key"], row["sequence_sha256"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)
    receipt = {
        "schema_version": "ampgent.qd-gap-structure-queue.1",
        "run_id": args.run_id,
        "candidate_count": len(selected),
        "qd_new_cell_count": sum(row["qd_contribution"] == "empty_cell" for row in selected),
        "qd_replacement_count": sum(
            row["qd_contribution"] == "incumbent_replacement" for row in selected
        ),
        "queue_csv_sha256": sha256_file(args.output),
        "source_scores_sha256": sha256_file(args.scores),
        "source_qd_sha256": sha256_file(args.qd),
        "source_challenger_sha256": sha256_file(args.challenger),
        "gpu_rosetta_md_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    args.output.with_name("structure_queue_receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--qd", type=Path, required=True)
    parser.add_argument("--challenger", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--output", type=Path, required=True)
    export(parser.parse_args())


if __name__ == "__main__":
    main()
