"""Build a non-dispatched targeted PepGLAD coarse-5 queue from PG IDs."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import uuid
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy import select

from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json


async def _pg_candidates(run_id: str, hashes: list[str]) -> dict[str, Candidate]:
    async with SessionFactory() as session:
        found = list(
            await session.scalars(
                select(Candidate).where(
                    Candidate.run_id == uuid.UUID(run_id),
                    Candidate.sequence_sha256.in_(hashes),
                )
            )
        )
    result = {candidate.sequence_sha256: candidate for candidate in found}
    if len(result) != len(hashes):
        raise ValueError("PG materialization mapping is incomplete")
    return result


def build(
    score_csv: Path,
    qd_json: Path,
    materialization_json: Path,
    output_dir: Path,
    target_key: str = "angpt1",
    source: str = "PepGLAD",
    operator_id: str | None = None,
    authoritative_candidates: Mapping[str, Candidate] | None = None,
) -> dict:
    scores = list(csv.DictReader(score_csv.open(encoding="utf-8-sig", newline="")))
    qd = json.loads(qd_json.read_text(encoding="utf-8"))
    material = json.loads(materialization_json.read_text(encoding="utf-8"))
    contribution_by_hash = {
        item["candidate_id"]: item
        for item in qd["contributions"]
        if item.get("contribution") in {"empty_cell", "incumbent_replacement"}
    }
    selected = [row for row in scores if row["sequence_sha256"] in contribution_by_hash]
    if not selected:
        raise ValueError("no QD contribution available for coarse5 preparation")
    run_id = str(material["operational_run_id"])
    resolved_operator_id = operator_id or (
        f"{target_key}-{source.casefold()}-source-expansion-1aa-v1"
    )
    selected_hashes = [row["sequence_sha256"] for row in selected]
    found = (
        dict(authoritative_candidates)
        if authoritative_candidates is not None
        else asyncio.run(_pg_candidates(run_id, selected_hashes))
    )
    if set(found) != set(selected_hashes):
        raise ValueError("PG materialization mapping is incomplete")
    queue = []
    for row in sorted(selected, key=lambda item: item["sequence"]):
        digest = row["sequence_sha256"]
        candidate = found[digest]
        queue.append(
            {
                "target_key": target_key,
                "run_id": run_id,
                "candidate_id": str(candidate.id),
                "sequence": candidate.sequence,
                "sequence_sha256": digest,
                "source": source,
                "operator_id": resolved_operator_id,
                "qd_cell": contribution_by_hash[digest]["cell_id"],
                "qd_contribution": contribution_by_hash[digest]["contribution"],
                "task_key": f"rosetta-coarse5:{target_key}:{run_id}:{candidate.id}",
                "nstruct": 5,
                "existing_decoys": 0,
                "remaining_decoys": 5,
                "status": "prepared_not_dispatched",
                "dispatch_allowed": "false",
                "pool_a_admitted": "false",
                "median_dg_gate": -30,
                "rosetta_required": "true",
                "remote_large_artifact_downloaded": "false",
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "coarse5_prepared_queue.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(queue[0]))
        writer.writeheader()
        writer.writerows(queue)
    receipt = {
        "schema_version": (
            f"ampgent.{target_key}-{source.casefold()}-coarse5-prepared.1"
        ),
        "target_key": target_key,
        "source": source,
        "operator_id": resolved_operator_id,
        "run_id": run_id,
        "candidate_count": len(queue),
        "authoritative_candidate_ids": [row["candidate_id"] for row in queue],
        "task_key_count": len({row["task_key"] for row in queue}),
        "task_key_identity": "target_key+run_id+authoritative_candidate_id",
        "nstruct": 5,
        "existing_decoys_total": sum(int(row["existing_decoys"]) for row in queue),
        "remaining_decoys_total": sum(int(row["remaining_decoys"]) for row in queue),
        "status": "prepared_not_dispatched",
        "dispatch_allowed": False,
        "pool_a_admitted_count": 0,
        "rosetta_required": True,
        "median_dg_gate": -30,
        "candidate_scores_sha256": sha256_file(score_csv),
        "qd_sha256": sha256_file(qd_json),
        "materialization_sha256": sha256_file(materialization_json),
        "remote_large_artifact_downloaded": False,
        "remote_file_deleted": False,
        "pg_readback": {
            "candidate_count": len(queue),
            "evaluation_count": int(material.get("inserted_evaluation_count", 0)),
            "evaluation_count_per_candidate": 17,
            "tool_call_id": material.get("tool_call_id"),
            "global_exact_replay_skip_count": int(
                material.get("global_exact_replay_skip_count", 0)
            ),
            "identity_drift_count": int(material.get("identity_drift_count", 0)),
        },
        "historical_runs_modified": bool(
            material.get("historical_runs_modified", False)
        ),
    }
    receipt["queue_csv_sha256"] = sha256_file(csv_path)
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "coarse5_prepared_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--score-csv", type=Path, required=True)
    parser.add_argument("--qd-json", type=Path, required=True)
    parser.add_argument("--materialization-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-key", default="angpt1")
    parser.add_argument("--source", default="PepGLAD")
    parser.add_argument("--operator-id")
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.score_csv,
                args.qd_json,
                args.materialization_json,
                args.output_dir,
                target_key=args.target_key,
                source=args.source,
                operator_id=args.operator_id,
            )
        )
    )


if __name__ == "__main__":
    main()
