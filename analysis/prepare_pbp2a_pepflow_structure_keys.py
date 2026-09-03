"""Create prepared, non-dispatched five-decoy keys for QD contributors."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qd-csv", type=Path, required=True)
    parser.add_argument("--pg-verification", type=Path, required=True)
    parser.add_argument("--score-csv", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()
    qd = {
        row["sequence_sha256"]: row
        for row in csv.DictReader(args.qd_csv.open(encoding="utf-8-sig", newline=""))
        if row["contribution"] in {"empty_cell", "incumbent_replacement"}
    }
    verification = json.loads(args.pg_verification.read_text(encoding="utf-8"))
    identities = {
        row["sequence_sha256"]: row["candidate_id"]
        for row in verification["candidate_evidence"]
    }
    scores = {
        row["sequence_sha256"]: row
        for row in csv.DictReader(args.score_csv.open(encoding="utf-8-sig", newline=""))
    }
    rows = []
    for digest in sorted(qd):
        if digest not in identities or digest not in scores:
            raise ValueError(f"missing authoritative identity or score: {digest}")
        candidate_id = identities[digest]
        row = scores[digest]
        rows.append(
            {
                "task_key": f"rosetta5:pbp2a:{verification['run_id']}:{candidate_id}",
                "run_id": verification["run_id"],
                "candidate_id": candidate_id,
                "target_key": "pbp2a",
                "sequence": row["sequence"],
                "sequence_sha256": digest,
                "qd_contribution": qd[digest]["contribution"],
                "decoy_count": 5,
                "rosetta_required": True,
                "submitted": False,
                "status": "prepared_not_dispatched",
                "median_dg_threshold": -30.0,
            }
        )
    if not rows:
        raise ValueError("no QD contributors to prepare")
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "schema_version": "ampgent.pbp2a-pepflow-same-domain-prepared-keys.1",
        "run_id": verification["run_id"],
        "candidate_count": len(rows),
        "decoy_count_per_candidate": 5,
        "submitted_count": 0,
        "status": "prepared_not_dispatched",
        "rosetta_median_dg_threshold": -30.0,
        "candidate_ids": [row["candidate_id"] for row in rows],
        "pg_verification_sha256": hashlib.sha256(
            args.pg_verification.read_bytes()
        ).hexdigest(),
        "historical_run_modified": False,
    }
    args.output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
