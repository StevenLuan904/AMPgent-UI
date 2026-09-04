"""Build a local prepared-only coarse5 queue from PG readback identities."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_file, sha256_json


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def build(
    score_csv: Path,
    qd_csv: Path,
    readback_json: Path,
    output_dir: Path,
) -> dict[str, Any]:
    scores = {row["sequence_sha256"]: row for row in _csv(score_csv)}
    qd_rows = {
        row["sequence_sha256"]: row
        for row in _csv(qd_csv)
        if row.get("contribution") in {"empty_cell", "incumbent_replacement"}
    }
    readback = json.loads(readback_json.read_text(encoding="utf-8"))
    if not readback.get("complete") or readback.get("drift") != 0:
        raise ValueError("PG readback is not complete and drift-free")
    candidate_rows = readback.get("authoritative_candidates", [])
    if len(candidate_rows) != 4:
        raise ValueError("expected four authoritative candidates")
    by_hash = {row["sequence_sha256"]: row for row in candidate_rows}
    if set(by_hash) != set(qd_rows) or not set(by_hash) <= set(scores):
        raise ValueError("PG readback, QD, and score identities do not match")
    run_id = str(readback["run_id"])
    rows: list[dict[str, Any]] = []
    for rank, digest in enumerate(sorted(by_hash), 1):
        qd = qd_rows[digest]
        score = scores[digest]
        candidate_id = by_hash[digest]["candidate_id"]
        rows.append(
            {
                "priority_rank": rank,
                "target_key": "pbp2a",
                "run_id": run_id,
                "candidate_id": candidate_id,
                "sequence": score["sequence"],
                "sequence_sha256": digest,
                "qd_cell": qd["actual_cell_id"],
                "qd_contribution": qd["contribution"],
                "task_key": f"rosetta-coarse5:pbp2a:{run_id}:{candidate_id}",
                "nstruct": 5,
                "existing_decoys": 0,
                "remaining_decoys": 5,
                "status": "prepared_not_dispatched",
                "dispatch_allowed": "false",
                "pool_a_admitted": "false",
                "structure_status": "not_created",
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    queue_path = output_dir / "coarse5_prepared_queue.csv"
    with queue_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    receipt = {
        "schema_version": "ampgent.pbp2a-pepmlm-pepflow-coarse5-prepared.1",
        "target_key": "pbp2a",
        "run_id": run_id,
        "candidate_count": len(rows),
        "authoritative_candidate_ids": [row["candidate_id"] for row in rows],
        "task_key_count": len({row["task_key"] for row in rows}),
        "task_key_identity": "target_key+run_id+authoritative_candidate_id",
        "nstruct": 5,
        "dispatch_allowed": False,
        "status": "prepared_not_dispatched",
        "structure_status": "not_created",
        "existing_decoys_total": 0,
        "remaining_decoys_total": len(rows) * 5,
        "pool_a_admitted_count": 0,
        "qd_contribution_counts": {
            "empty_cell": sum(row["qd_contribution"] == "empty_cell" for row in rows),
            "incumbent_replacement": sum(
                row["qd_contribution"] == "incumbent_replacement" for row in rows
            ),
        },
        "pg_readback": {
            "candidate_count": readback["candidate_count"],
            "evaluation_count": readback["evaluation_count"],
            "evaluation_count_per_candidate": readback[
                "evaluation_count_per_candidate"
            ],
            "tool_call_id": readback["tool_call_id"],
            "identity_drift_count": readback["identity_drift_count"],
            "drift": readback["drift"],
        },
        "candidate_scores_sha256": sha256_file(score_csv),
        "qd_sha256": sha256_file(qd_csv),
        "readback_sha256": sha256_file(readback_json),
        "historical_runs_modified": False,
        "remote_large_artifact_downloaded": False,
        "remote_dispatch": False,
        "rosetta_required": True,
    }
    receipt["queue_csv_sha256"] = sha256_file(queue_path)
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "coarse5_prepared_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--score-csv", type=Path, required=True)
    parser.add_argument("--qd-csv", type=Path, required=True)
    parser.add_argument("--readback-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.score_csv, args.qd_csv, args.readback_json, args.output_dir)))


if __name__ == "__main__":
    main()
