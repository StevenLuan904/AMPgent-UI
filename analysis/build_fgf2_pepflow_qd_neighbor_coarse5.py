"""Prepare a non-dispatched FGF2 coarse5 queue from authoritative PG IDs."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


async def _authoritative(run_id: str, hashes: list[str]) -> dict[str, Candidate]:
    async with SessionFactory() as session:
        rows = list(
            await session.scalars(
                select(Candidate).where(
                    Candidate.run_id == uuid.UUID(run_id),
                    Candidate.sequence_sha256.in_(hashes),
                )
            )
        )
    result = {row.sequence_sha256: row for row in rows}
    if set(result) != set(hashes) or len(result) != len(hashes):
        raise ValueError("authoritative FGF2 candidate mapping is incomplete")
    return result


def build(report_dir: Path, output_dir: Path) -> dict[str, Any]:
    material = _json(report_dir / "materialization_receipt.json")
    qd = _json(report_dir / "provisional_qd.json")
    selected = _rows(report_dir / "materialization_input" / "candidate_scores.csv")
    run_id = str(material["operational_run_id"])
    hashes = [row["sequence_sha256"].lower() for row in selected]
    contributions = {
        str(item["candidate_id"]).lower(): item
        for item in qd.get("contributions", [])
        if item.get("contribution")
        in {"empty_cell", "new_cell", "incumbent_replacement", "replacement"}
    }
    if set(hashes) - set(contributions):
        raise ValueError("coarse5 selection is not a QD contribution")
    authoritative = asyncio.run(_authoritative(run_id, hashes))
    rows: list[dict[str, Any]] = []
    for row in selected:
        digest = row["sequence_sha256"].lower()
        candidate = authoritative[digest]
        contribution = contributions[digest]
        candidate_id = str(candidate.id)
        rows.append(
            {
                "target_key": "fgf2",
                "run_id": run_id,
                "authoritative_candidate_id": candidate_id,
                "sequence_sha256": digest,
                "qd_cell": contribution["cell_id"],
                "qd_contribution": contribution["contribution"],
                "task_key": f"rosetta-coarse5:fgf2:{run_id}:{candidate_id}",
                "nstruct": 5,
                "existing_decoys": 0,
                "remaining_decoys": 5,
                "status": "prepared_not_dispatched",
                "dispatch_allowed": "false",
                "pool_a_admitted": "false",
                "remote_large_artifact_downloaded": "false",
            }
        )
    if len({row["task_key"] for row in rows}) != len(rows):
        raise ValueError("coarse5 task identity is not unique")
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "coarse5_prepared_queue.csv"
    with queue_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    receipt = {
        "schema_version": "ampgent.fgf2-pepflow-qd-neighbor-vnext-coarse5.1",
        "target_key": "fgf2",
        "run_id": run_id,
        "materialization_tool_call_id": material["tool_call_id"],
        "candidate_count": len(rows),
        "authoritative_candidate_ids": [row["authoritative_candidate_id"] for row in rows],
        "task_key_count": len({row["task_key"] for row in rows}),
        "nstruct": 5,
        "existing_decoys": 0,
        "remaining_decoys": 5,
        "status": "prepared_not_dispatched",
        "dispatch_allowed": False,
        "pool_a_admitted_count": 0,
        "qd_contribution_counts": {
            "new_cell": sum(row["qd_contribution"] in {"empty_cell", "new_cell"} for row in rows),
            "replacement": sum(
                row["qd_contribution"] in {"incumbent_replacement", "replacement"} for row in rows
            ),
        },
        "candidate_scores_sha256": sha256_file(
            report_dir / "materialization_input" / "candidate_scores.csv"
        ),
        "qd_sha256": sha256_file(report_dir / "provisional_qd.json"),
        "materialization_sha256": sha256_file(report_dir / "materialization_receipt.json"),
        "queue_csv_sha256": sha256_file(queue_path),
        "remote_large_artifact_downloaded": False,
        "remote_dispatch_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "coarse5_prepared_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.report_dir, args.output_dir), ensure_ascii=False, separators=(",", ":")
        )
    )


if __name__ == "__main__":
    main()
