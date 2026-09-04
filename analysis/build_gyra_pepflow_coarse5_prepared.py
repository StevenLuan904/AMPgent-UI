"""Build a compact, non-dispatched targeted coarse5 inventory from PG IDs."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import uuid
from pathlib import Path

from sqlalchemy import select

from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json

HYBRID_SOURCE = "PepGLAD-ancestry_x_PepFlow-donor"
IMMEDIATE_PARENT_SOURCE = "generation4_hybrid_QD_elite"
ANCESTRAL_PARENT_SOURCE = "PepGLAD"
DONOR_SOURCE = "PepFlow"


async def _pg_candidates(run_id: str, hashes: list[str]) -> dict[str, Candidate]:
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
    if len(result) != len(hashes):
        raise ValueError("materialized PG candidate mapping is incomplete")
    return result


def build(
    score_csv: Path,
    qd_json: Path,
    materialization_json: Path,
    output_dir: Path,
    *,
    source: str = HYBRID_SOURCE,
    parent_source: str = IMMEDIATE_PARENT_SOURCE,
    ancestral_parent_source: str = ANCESTRAL_PARENT_SOURCE,
    donor_source: str = DONOR_SOURCE,
) -> dict:
    scores = []
    with score_csv.open(encoding="utf-8-sig", newline="") as handle:
        scores = list(csv.DictReader(handle))
    material = json.loads(materialization_json.read_text(encoding="utf-8"))
    qd = json.loads(qd_json.read_text(encoding="utf-8"))
    contributions = {
        item["candidate_id"]: item
        for item in qd.get("contributions", [])
        if item.get("contribution") in {"empty_cell", "incumbent_replacement"}
    }
    selected = [row for row in scores if row["sequence_sha256"] in contributions]
    run_id = str(material["operational_run_id"])
    found = asyncio.run(
        _pg_candidates(run_id, [row["sequence_sha256"] for row in selected])
    )
    rows = []
    for rank, row in enumerate(sorted(selected, key=lambda item: item["sequence"]), 1):
        candidate = found[row["sequence_sha256"]]
        qd_row = contributions[row["sequence_sha256"]]
        rows.append(
            {
                "target_key": "gyra",
                "run_id": run_id,
                "candidate_id": str(candidate.id),
                "sequence": candidate.sequence,
                "sequence_sha256": candidate.sequence_sha256,
                "source": source,
                "evidence_arm": source,
                "parent_source": parent_source,
                "immediate_parent_source": parent_source,
                "ancestral_parent_source": ancestral_parent_source,
                "donor_source": donor_source,
                "source_run_id": row.get("source_run_id", row.get("parent_run_id", "")),
                "parent_run_id": row.get("parent_run_id", ""),
                "parent_candidate_id": row.get("parent_candidate_id", ""),
                "parent_sequence_sha256": row.get("parent_sequence_sha256", ""),
                "edit_position_zero_based": row.get("edit_position_zero_based", ""),
                "from_residue": row.get("from_residue", ""),
                "to_residue": row.get("to_residue", ""),
                "donor_candidate_id": row.get("donor_candidate_id", ""),
                "donor_artifact_id": row.get("donor_artifact_id", ""),
                "operator_id": row.get("operator_id", ""),
                "qd_cell": qd_row["cell_id"],
                "qd_contribution": qd_row["contribution"],
                "priority_rank": rank,
                "task_key": f"rosetta-coarse5:gyra:{run_id}:{candidate.id}",
                "nstruct": 5,
                "existing_decoys": 0,
                "remaining_decoys": 5,
                "status": "prepared_not_dispatched",
                "dispatch_allowed": "false",
                "pool_a_admitted": "false",
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "coarse5_prepared_queue.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    receipt = {
        "schema_version": "ampgent.gyra-pepflow-coarse5-prepared.1",
        "target_key": "gyra",
        "run_id": run_id,
        "candidate_count": len(rows),
        "task_key_count": len({row["task_key"] for row in rows}),
        "nstruct": 5,
        "dispatch_allowed": False,
        "pool_a_admitted_count": 0,
        "median_dg_gate": -30,
        "median_dg_gate_semantics": "median InterfaceAnalyzer dG must be < gate before Pool A",
        "source": source,
        "evidence_arm": source,
        "parent_source": parent_source,
        "immediate_parent_source": parent_source,
        "ancestral_parent_source": ancestral_parent_source,
        "donor_source": donor_source,
        "operator_id": sorted({row.get("operator_id", "") for row in selected}),
        "source_run_ids": sorted(
            {row.get("source_run_id", row.get("parent_run_id", "")) for row in selected}
        ),
        "parent_candidate_ids": sorted(
            {row.get("parent_candidate_id", "") for row in selected}
        ),
        "donor_artifact_ids": sorted({row.get("donor_artifact_id", "") for row in selected}),
        "qd_contribution_counts": {
            "empty_cell": sum(row["qd_contribution"] == "empty_cell" for row in rows),
            "incumbent_replacement": sum(
                row["qd_contribution"] == "incumbent_replacement" for row in rows
            ),
        },
        "candidate_scores_sha256": sha256_file(score_csv),
        "qd_sha256": sha256_file(qd_json),
        "materialization_sha256": sha256_file(materialization_json),
        "remote_large_artifact_downloaded": False,
        "remote_file_deleted": False,
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
    parser.add_argument("--source", default=HYBRID_SOURCE)
    parser.add_argument("--parent-source", default=IMMEDIATE_PARENT_SOURCE)
    parser.add_argument("--ancestral-parent-source", default=ANCESTRAL_PARENT_SOURCE)
    parser.add_argument("--donor-source", default=DONOR_SOURCE)
    args = parser.parse_args()
    print(
        json.dumps(
            build(
                args.score_csv,
                args.qd_json,
                args.materialization_json,
                args.output_dir,
                source=args.source,
                parent_source=args.parent_source,
                ancestral_parent_source=args.ancestral_parent_source,
                donor_source=args.donor_source,
            )
        )
    )


if __name__ == "__main__":
    main()
