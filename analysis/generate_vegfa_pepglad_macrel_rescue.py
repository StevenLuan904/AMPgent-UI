"""Generate a bounded PepGLAD-derived, one-residue Macrel rescue batch."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
from uuid import UUID

from generate_angpt1_pepglad_source_expansion import (
    phi,
    prior_edits,
    read_rows,
    sha256_text,
)
from sqlalchemy import select

from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory

SEED = 20260904
TARGET_KEY = "vegfa"
OPERATOR_ID = "vegfa-pepglad-macrel-rescue-1aa-v1"
GENERATION = 4
RESCUE_RESIDUES = ("E", "S", "T")


def _donor_rows(path: Path) -> dict[str, dict[str, str]]:
    selected: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(read_rows(path), start=2):
        source = (row.get("donor_source") or "").strip().casefold()
        fragment = (row.get("donor_fragment") or "").strip().upper()
        donor_id = (row.get("donor_candidate_id") or "").strip()
        if source != "pepglad" or not donor_id or not fragment.isalpha():
            continue
        for offset, residue in enumerate(fragment):
            if residue in RESCUE_RESIDUES:
                key = residue
                selected.setdefault(
                    key,
                    {
                        "donor_candidate_id": donor_id,
                        "donor_source": "PepGLAD",
                        "donor_fragment": fragment,
                        "donor_residue": residue,
                        "donor_fragment_offset": str(offset),
                        "donor_artifact": path.as_posix(),
                        "donor_row_number": str(row_number),
                        "donor_row_sha256": sha256_text(
                            json.dumps(row, sort_keys=True, separators=(",", ":"))
                        ),
                    },
                )
    missing = sorted(set(RESCUE_RESIDUES) - set(selected))
    if missing:
        raise ValueError(f"PepGLAD donor artifact lacks rescue residues: {','.join(missing)}")
    return selected


async def _resolve_parents(
    parent_hashes: list[str], parent_run_id: str
) -> dict[str, Candidate]:
    run_uuid = UUID(parent_run_id)
    async with SessionFactory() as session:
        rows = list(
            await session.scalars(
                select(Candidate)
                .where(
                    Candidate.run_id == run_uuid,
                    Candidate.sequence_sha256.in_(parent_hashes),
                )
                .order_by(Candidate.created_at, Candidate.id)
            )
        )
    resolved: dict[str, Candidate] = {}
    for candidate in rows:
        if candidate.sequence_sha256 in resolved:
            raise ValueError(
                f"parent sequence is not unique within run: {candidate.sequence_sha256}"
            )
        resolved[candidate.sequence_sha256] = candidate
    return resolved


async def _candidate_hashes(hashes: list[str]) -> set[str]:
    async with SessionFactory() as session:
        return set(
            await session.scalars(
                select(Candidate.sequence_sha256).where(
                    Candidate.sequence_sha256.in_(hashes)
                )
        )
    )


async def _candidate_edit_keys(parent_hashes: list[str]) -> set[tuple[str, int, str]]:
    async with SessionFactory() as session:
        rows = list(
            (
                await session.execute(
                    select(Candidate.sequence_sha256, Candidate.metadata_json).where(
                        Candidate.metadata_json["parent_sequence_sha256"].as_string().in_(
                            parent_hashes
                        )
                    )
                )
            ).all()
        )
    edits: set[tuple[str, int, str]] = set()
    for child_hash, metadata in rows:
        del child_hash
        parent_hash = str(metadata.get("parent_sequence_sha256") or "")
        position = metadata.get("acceptor_start_zero_based")
        to_residue = metadata.get("to_residue")
        if parent_hash and isinstance(position, int) and to_residue:
            edits.add((parent_hash, position, str(to_residue)))
    return edits


async def _collect_pg_state(
    parent_hashes: list[str], potential_hashes: list[str], parent_run_id: str
) -> tuple[dict[str, Candidate], set[str], set[tuple[str, int, str]]]:
    run_uuid = UUID(parent_run_id)
    async with SessionFactory() as session:
        parent_rows = list(
            await session.scalars(
                select(Candidate)
                .where(
                    Candidate.run_id == run_uuid,
                    Candidate.sequence_sha256.in_(parent_hashes),
                )
                .order_by(Candidate.created_at, Candidate.id)
            )
        )
        child_hashes = set(
            await session.scalars(
                select(Candidate.sequence_sha256).where(
                    Candidate.sequence_sha256.in_(potential_hashes)
                )
            )
        )
        edit_rows = list(
            (
                await session.execute(
                    select(Candidate.sequence_sha256, Candidate.metadata_json).where(
                        Candidate.metadata_json["parent_sequence_sha256"].as_string().in_(
                            parent_hashes
                        )
                    )
                )
            ).all()
        )
    parents: dict[str, Candidate] = {}
    for candidate in parent_rows:
        if candidate.sequence_sha256 in parents:
            raise ValueError(
                f"parent sequence is not unique within run: {candidate.sequence_sha256}"
            )
        parents[candidate.sequence_sha256] = candidate
    edits: set[tuple[str, int, str]] = set()
    for child_hash, metadata in edit_rows:
        del child_hash
        parent_hash = str(metadata.get("parent_sequence_sha256") or "")
        position = metadata.get("acceptor_start_zero_based")
        to_residue = metadata.get("to_residue")
        if parent_hash and isinstance(position, int) and to_residue:
            edits.add((parent_hash, position, str(to_residue)))
    return parents, {str(item) for item in child_hashes}, edits


def build_rescue_proposals(
    diagnosis_rows: list[dict[str, str]],
    parent_candidates: dict[str, Candidate],
    donor_by_residue: dict[str, dict[str, str]],
    historical_hashes: set[str],
    *,
    parent_run_id: str,
    source_scored_batch_key: str = "",
    source_generation_receipt_sha256: str = "",
    source_score_all_receipt_sha256: str = "",
    limit: int = 12,
    historical_edits: set[tuple[str, int, str]] | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen = set(historical_hashes)
    historical_edits = historical_edits or set()
    parent_options: list[tuple[dict[str, str], list[dict[str, str]]]] = []
    for diagnosis in diagnosis_rows:
        parent_sequence = diagnosis["sequence"].strip().upper()
        parent_sha = diagnosis["sequence_sha256"].strip().lower()
        parent = parent_candidates.get(parent_sha)
        if parent is not None and (
            parent.sequence != parent_sequence or parent.sequence_sha256 != parent_sha
        ):
            raise ValueError("authoritative parent sequence/hash mismatch")
        w_positions = [i for i, residue in enumerate(parent_sequence) if residue == "W"]
        if len(w_positions) < 2:
            raise ValueError("Macrel rescue parent must expose two N-terminal tryptophans")
        options: list[dict[str, str]] = []
        for position in w_positions:
            for residue in RESCUE_RESIDUES:
                child = parent_sequence[:position] + residue + parent_sequence[position + 1 :]
                child_sha = sha256_text(child)
                if child_sha in seen or (parent_sha, position, residue) in historical_edits:
                    continue
                before, after = phi(parent_sequence), phi(child)
                donor = donor_by_residue[residue]
                options.append(
                    {
                    "sequence": child,
                    "sequence_sha256": child_sha,
                    "branch_key": TARGET_KEY,
                    "target_key": TARGET_KEY,
                    "generation": str(GENERATION),
                    "seed": str(SEED),
                    "operator_id": OPERATOR_ID,
                    "proposal_mode": "pepglad_macrel_safety_rescue_1aa",
                    "parent_run_id": "",
                    "parent_candidate_id": "",
                    "parent_identity_kind": "scored_proposal",
                    "source_proposal_id": f"proposal:{parent_sha}",
                    "source_scored_batch_key": source_scored_batch_key,
                    "calibration_reference_run_id": parent_run_id,
                    "source_generation_receipt_sha256": source_generation_receipt_sha256,
                    "source_score_all_receipt_sha256": source_score_all_receipt_sha256,
                    "parent_sequence": parent_sequence,
                    "parent_sequence_sha256": parent_sha,
                    "parent_qd_cell": "q4-h2-m3-l2",
                    "acceptor_start_zero_based": str(position),
                    "from_residue": "W",
                    "to_residue": residue,
                    **donor,
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
                            "polar_or_weakly_polar_real_pepglad_residue_to_lower_macrel_risk"
                        ),
                    "parent_identity_status": "proposal_only_unmaterialized",
                    "history_gate": "postgresql_exact_sequence_and_prior_edit_passed",
                    }
                )
        parent_options.append((diagnosis, options))
    # Round-robin parent families before taking the bounded prefix.  This keeps
    # the rescue batch from being determined by diagnosis/hash sort order.
    for offset in range(max((len(options) for _, options in parent_options), default=0)):
        for _, options in parent_options:
            if offset < len(options):
                row = options[offset]
                rows.append(row)
                seen.add(row["sequence_sha256"])
                if len(rows) >= limit:
                    return rows
    return rows


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnosis-csv", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, required=True)
    parser.add_argument("--parent-run-id", required=True)
    parser.add_argument("--source-generation-receipt", type=Path, required=True)
    parser.add_argument("--source-score-all-receipt", type=Path, required=True)
    parser.add_argument("--source-scored-batch-key", required=True)
    parser.add_argument("--history-csv", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    diagnosis_rows = read_rows(args.diagnosis_csv)
    if len(diagnosis_rows) != 4:
        raise ValueError("this rescue round requires the four diagnosed support-positive parents")
    parent_hashes = sorted(row["sequence_sha256"].strip().lower() for row in diagnosis_rows)
    potential_hashes = [
        sha256_text(
            diagnosis["sequence"][:position]
            + residue
            + diagnosis["sequence"][position + 1 :]
        )
        for diagnosis in diagnosis_rows
        for position, value in enumerate(diagnosis["sequence"])
        if value == "W"
        for residue in RESCUE_RESIDUES
    ]
    parent_candidates, pg_existing_hashes, pg_edit_keys = asyncio.run(
        _collect_pg_state(parent_hashes, potential_hashes, args.parent_run_id)
    )
    local_edit_keys = prior_edits(args.history_csv)
    historical_hashes = set(pg_existing_hashes)
    source_generation_receipt_sha256 = sha256_text(
        args.source_generation_receipt.read_text(encoding="utf-8")
    )
    source_score_all_receipt_sha256 = sha256_text(
        args.source_score_all_receipt.read_text(encoding="utf-8")
    )
    proposals = build_rescue_proposals(
        diagnosis_rows,
        parent_candidates,
        _donor_rows(args.donor_csv),
        historical_hashes,
        parent_run_id=args.parent_run_id,
        source_generation_receipt_sha256=source_generation_receipt_sha256,
        source_score_all_receipt_sha256=source_score_all_receipt_sha256,
        source_scored_batch_key=args.source_scored_batch_key,
        limit=args.limit,
        historical_edits=pg_edit_keys | local_edit_keys,
    )
    if not proposals:
        raise ValueError("no PG-new rescue proposals survived exact/history exclusion")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    _write_csv(args.output_dir / "proposals.csv", proposals)
    receipt = {
        "schema_version": "ampgent.vegfa-pepglad-macrel-rescue-generation.1",
        "target_key": TARGET_KEY,
        "source": "PepGLAD",
        "operator_id": OPERATOR_ID,
        "seed": SEED,
        "generation": GENERATION,
        "proposal_count": len(proposals),
        "parent_run_id": "",
        "parent_candidate_ids": [],
        "parent_identity_kind": "scored_proposal",
        "source_proposal_ids": sorted({row["source_proposal_id"] for row in proposals}),
        "source_scored_batch_key": args.source_scored_batch_key,
        "calibration_reference_run_id": args.parent_run_id,
        "source_generation_receipt_sha256": source_generation_receipt_sha256,
        "source_score_all_receipt_sha256": source_score_all_receipt_sha256,
        "parent_identity_status_counts": {"proposal_only_unmaterialized": len(proposals)},
        "parent_pg_exact_match_count": len(parent_candidates),
        "parent_count": len({row["parent_sequence_sha256"] for row in proposals}),
        "donor_residues": list(RESCUE_RESIDUES),
        "historical_exact_hash_count": len(historical_hashes),
        "historical_edit_key_count": len(pg_edit_keys | local_edit_keys),
        "pg_exact_checked_child_count": len(potential_hashes),
        "pg_exact_existing_child_count": len(pg_existing_hashes),
        "equal_length_single_residue_count": len(proposals),
        "gpu_rosetta_md_submitted": False,
        "historical_runs_modified": False,
    }
    (args.output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
