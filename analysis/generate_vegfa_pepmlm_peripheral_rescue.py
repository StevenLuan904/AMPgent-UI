"""Generate a bounded VEGFA PepMLM peripheral rescue batch."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
from uuid import UUID

from generate_angpt1_pepglad_source_expansion import phi, sha256_text
from sqlalchemy import select

from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory

SEED = 20260904
TARGET_KEY = "vegfa"
SOURCE = "PepMLM"
OPERATOR_ID = "vegfa-pepmlm-peripheral-rescue-1aa-v1"
GENERATION = 5
EDIT_OPTIONS = ((3, "S"), (3, "T"), (3, "N"), (7, "S"), (7, "T"), (8, "S"), (8, "T"), (9, "T"))


async def _collect_pg_state(
    parent_run_id: str,
    parent_candidate_id: str,
    parent_sequence_sha256: str,
    potential_hashes: list[str],
) -> tuple[Candidate, set[str], set[tuple[int, str]]]:
    run_uuid = UUID(parent_run_id)
    candidate_uuid = UUID(parent_candidate_id)
    async with SessionFactory() as session:
        parent = await session.scalar(
            select(Candidate).where(
                Candidate.run_id == run_uuid,
                Candidate.id == candidate_uuid,
                Candidate.sequence_sha256 == parent_sequence_sha256,
            )
        )
        if parent is None:
            raise ValueError("authoritative parent run/candidate/sequence identity not found")
        existing = set(
            await session.scalars(
                select(Candidate.sequence_sha256).where(
                    Candidate.sequence_sha256.in_(potential_hashes)
                )
            )
        )
        edit_rows = list(
            (
                await session.execute(
                    select(Candidate.metadata_json).where(
                        Candidate.metadata_json["parent_sequence_sha256"].as_string()
                        == parent_sequence_sha256
                    )
                )
            ).scalars()
        )
    edits: set[tuple[int, str]] = set()
    for metadata in edit_rows:
        position = metadata.get("acceptor_start_zero_based")
        to_residue = metadata.get("to_residue")
        if isinstance(position, int) and to_residue:
            edits.add((position, str(to_residue)))
    return parent, {str(item) for item in existing}, edits


def build_proposals(
    parent: Candidate,
    source_receipt_sha256: str,
    historical_hashes: set[str],
    historical_edits: set[tuple[int, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen = set(historical_hashes)
    parent_sequence = parent.sequence
    for position, to_residue in EDIT_OPTIONS:
        from_residue = parent_sequence[position]
        if from_residue == to_residue or (position, to_residue) in historical_edits:
            continue
        child = parent_sequence[:position] + to_residue + parent_sequence[position + 1 :]
        child_sha = sha256_text(child)
        if child_sha in seen:
            continue
        before, after = phi(parent_sequence), phi(child)
        rows.append(
            {
                "sequence": child,
                "sequence_sha256": child_sha,
                "branch_key": TARGET_KEY,
                "target_key": TARGET_KEY,
                "source": SOURCE,
                "generation": str(GENERATION),
                "seed": str(SEED),
                "operator_id": OPERATOR_ID,
                "proposal_mode": "pepmlm_peripheral_macrel_support_rescue_1aa",
                "parent_run_id": str(parent.run_id),
                "parent_candidate_id": str(parent.id),
                "parent_identity_kind": "authoritative_candidate",
                "source_proposal_id": "",
                "source_parent_run_id": str(parent.run_id),
                "source_parent_candidate_id": str(parent.id),
                "source_parent_receipt_sha256": source_receipt_sha256,
                "parent_sequence": parent_sequence,
                "parent_sequence_sha256": parent.sequence_sha256,
                "parent_qd_cell": "q4-h2-m3-l2",
                "acceptor_start_zero_based": str(position),
                "from_residue": from_residue,
                "to_residue": to_residue,
                "actual_cell_preflight": "pending_formal12",
                "target_cell_hit_preflight": "pending_formal12",
                "delta_phi_skill": json.dumps(
                    {
                        "axes": [
                            "charge_density",
                            "hydrophobicity",
                            "hydrophobic_moment",
                            "length",
                        ],
                        "acceptor_to_child": [
                            left - right for left, right in zip(after, before, strict=True)
                        ],
                    },
                    sort_keys=True,
                ),
                "rescue_reason": (
                    "preserve_WW_and_activity_parent_while_testing_peripheral_polar_edit"
                ),
                "history_gate": "postgresql_exact_sequence_and_parent_edit_passed",
            }
        )
        seen.add(child_sha)
    return rows


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-receipt", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    parent_receipt = json.loads(args.parent_receipt.read_text(encoding="utf-8"))
    postgres = parent_receipt["postgresql"]
    parent_sequence = parent_receipt["sequence"].strip().upper()
    parent_hash = parent_receipt["sequence_sha256"].strip().lower()
    if sha256_text(parent_sequence) != parent_hash:
        raise ValueError("parent receipt sequence/hash mismatch")
    potential_hashes = [
        sha256_text(parent_sequence[:position] + residue + parent_sequence[position + 1 :])
        for position, residue in EDIT_OPTIONS
        if parent_sequence[position] != residue
    ]
    parent, existing, historical_edits = asyncio.run(
        _collect_pg_state(
            postgres["run_id"], postgres["candidate_id"], parent_hash, potential_hashes
        )
    )
    if parent.sequence != parent_sequence or parent.sequence_sha256 != parent_hash:
        raise ValueError("PG parent sequence/hash mismatch")
    source_receipt_sha256 = sha256_text(args.parent_receipt.read_text(encoding="utf-8"))
    proposals = build_proposals(parent, source_receipt_sha256, existing, historical_edits)
    if not proposals:
        raise ValueError("no PG-new peripheral proposals survived exact/history exclusion")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    _write_csv(args.output_dir / "proposals.csv", proposals)
    receipt = {
        "schema_version": "ampgent.vegfa-pepmlm-peripheral-rescue-generation.1",
        "target_key": TARGET_KEY,
        "source": SOURCE,
        "operator_id": OPERATOR_ID,
        "seed": SEED,
        "generation": GENERATION,
        "proposal_count": len(proposals),
        "parent_run_id": str(parent.run_id),
        "parent_candidate_id": str(parent.id),
        "parent_identity_kind": "authoritative_candidate",
        "parent_sequence_sha256": parent.sequence_sha256,
        "source_parent_receipt_sha256": source_receipt_sha256,
        "pg_exact_checked_child_count": len(potential_hashes),
        "pg_exact_existing_child_count": len(existing),
        "historical_edit_key_count": len(historical_edits),
        "equal_length_single_residue_count": len(proposals),
        "gpu_rosetta_md_submitted": False,
        "historical_runs_modified": False,
    }
    (args.output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
