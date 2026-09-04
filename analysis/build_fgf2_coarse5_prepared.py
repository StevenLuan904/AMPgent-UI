"""Build a PG-backed, prepared-only coarse-5 queue for FGF2 candidates."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import uuid
from pathlib import Path

from sqlalchemy import func, select

from pepagent.db.models import Candidate, Evaluation
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


async def build(scores_path: Path, run_id: uuid.UUID, output_dir: Path) -> dict:
    score_rows = _rows(scores_path)
    score_by_sha = {row["sequence_sha256"]: row for row in score_rows}
    async with SessionFactory() as session:
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == run_id)
                .order_by(Candidate.proposal_rank, Candidate.id)
            )
        )
        rows = []
        for candidate in candidates:
            score = score_by_sha.get(candidate.sequence_sha256)
            if score is None or score.get("target_key") != "fgf2":
                raise ValueError("materialized candidate is not bound to FGF2 score input")
            evaluation_count = await session.scalar(
                select(func.count(Evaluation.id)).where(
                    Evaluation.candidate_id == candidate.id,
                    Evaluation.subject_run_id == run_id,
                )
            )
            if evaluation_count != 17:
                raise ValueError("coarse5 queue requires 17 PG evaluations per candidate")
            candidate_id = str(candidate.id)
            rows.append(
                {
                    "target_key": "fgf2",
                    "run_id": str(run_id),
                    "authoritative_candidate_id": candidate_id,
                    "sequence_sha256": candidate.sequence_sha256,
                    "task_key": f"rosetta-coarse5:fgf2:{run_id}:{candidate_id}",
                    "nstruct": "5",
                    "existing_decoys": "0",
                    "remaining_decoys": "5",
                    "status": "prepared_not_dispatched",
                    "dispatch_allowed": "false",
                    "median_dg_gate": "-30",
                    "exemption_reason": "targeted_requires_five_decoy_median",
                }
            )
    if not rows:
        raise ValueError("no materialized FGF2 candidates found")
    if len({row["task_key"] for row in rows}) != len(rows):
        raise ValueError("coarse5 task keys are not unique")
    queue_path = output_dir / "coarse5_prepared_queue.csv"
    fields = list(rows[0])
    with queue_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    receipt = {
        "schema_version": "ampgent.targeted-coarse5-prepared.1",
        "target_key": "fgf2",
        "source": "PepFlow",
        "operator_id": "fgf2-pepflow-source-expansion-1aa-v1",
        "run_id": str(run_id),
        "authoritative_candidate_ids": [row["authoritative_candidate_id"] for row in rows],
        "candidate_count": len(rows),
        "pg_readback": {
            "candidate_count": len(rows),
            "evaluation_count": len(rows) * 17,
            "evaluations_per_candidate": 17,
        },
        "task_key_identity": "target_key+run_id+authoritative_candidate_id",
        "nstruct": 5,
        "existing_decoys": 0,
        "remaining_decoys_total": len(rows) * 5,
        "status": "prepared_not_dispatched",
        "dispatch_allowed": False,
        "median_dg_gate": -30,
        "queue_sha256": sha256_file(queue_path),
        "historical_run_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "coarse5_prepared_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--run-id", type=uuid.UUID, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    asyncio.run(build(args.scores, args.run_id, args.output_dir))


if __name__ == "__main__":
    main()
