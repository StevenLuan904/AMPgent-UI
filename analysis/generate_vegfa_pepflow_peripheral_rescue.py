"""Generate a bounded VEGFA PepFlow peripheral-rescue source-control batch."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path
from uuid import UUID

from generate_angpt1_pepglad_source_expansion import phi, sha256_text
from sqlalchemy import select

from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory

SEED = 20260904
TARGET_KEY = "vegfa"
SOURCE = "PepFlow"
OPERATOR_ID = "vegfa-pepflow-peripheral-rescue-1aa-v1"
GENERATION = 6
MAX_PROPOSALS = 8


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def donor_rows(path: Path) -> list[dict[str, str]]:
    """Extract unique real PepFlow residue donors with row-level provenance."""
    selected: dict[tuple[str, str, str], dict[str, str]] = {}
    for row_number, row in enumerate(_read(path), start=2):
        if (row.get("donor_source") or "").strip().casefold() != "pepflow":
            continue
        donor_id = (row.get("donor_candidate_id") or "").strip()
        fragment = (row.get("donor_fragment") or "").strip().upper()
        sequence = (row.get("donor_sequence") or "").strip().upper()
        if not donor_id or not fragment.isalpha() or not sequence:
            continue
        for offset, residue in enumerate(fragment):
            selected.setdefault(
                (donor_id, fragment, str(offset)),
                {
                    "donor_candidate_id": donor_id,
                    "donor_source": SOURCE,
                    "donor_sequence": sequence,
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
    return sorted(
        selected.values(),
        key=lambda row: (
            row["donor_residue"],
            row["donor_candidate_id"],
            row["donor_fragment"],
            row["donor_fragment_offset"],
        ),
    )


def reference_slots(path: Path) -> list[dict[str, str]]:
    """Read the completed PepMLM arm as the frozen matched-control slot list."""
    rows = _read(path)
    if len(rows) > MAX_PROPOSALS:
        rows = rows[:MAX_PROPOSALS]
    required = {"acceptor_start_zero_based", "from_residue", "to_residue"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("reference PepMLM proposals lack peripheral slot fields")
    return rows


def history_state(paths: list[Path]) -> tuple[set[str], set[tuple[int, str]]]:
    hashes: set[str] = set()
    edits: set[tuple[int, str]] = set()
    for path in paths:
        for row in _read(path):
            sequence = (row.get("sequence") or "").strip().upper()
            sequence_sha = (row.get("sequence_sha256") or "").strip().lower()
            if sequence:
                hashes.add(sequence_sha or sha256_text(sequence))
            position = (row.get("acceptor_start_zero_based") or "").strip()
            residue = (row.get("to_residue") or "").strip().upper()
            if position.isdigit() and residue:
                edits.add((int(position), residue))
    return hashes, edits


def _donor_for_slot(
    position: int,
    from_residue: str,
    slot_index: int,
    donors: list[dict[str, str]],
) -> dict[str, str] | None:
    eligible = [row for row in donors if row["donor_residue"] != from_residue]
    if not eligible:
        return None
    # Deterministic rotation gives the same slot budget while spreading donors.
    return eligible[slot_index % len(eligible)]


def build_proposals(
    parent: Candidate,
    reference: list[dict[str, str]],
    donors: list[dict[str, str]],
    historical_hashes: set[str],
    historical_edits: set[tuple[int, str]],
    *,
    source_generation_receipt_sha256: str,
    source_score_receipt_sha256: str,
    reference_proposals_sha256: str,
) -> list[dict[str, str]]:
    if not donors:
        raise ValueError("no real PepFlow donor rows")
    seen = set(historical_hashes)
    rows: list[dict[str, str]] = []
    for slot_index, slot in enumerate(reference):
        position = int(slot["acceptor_start_zero_based"])
        if position < 0 or position >= len(parent.sequence):
            raise ValueError("reference edit position is outside authoritative parent")
        if parent.sequence[position] != slot["from_residue"]:
            raise ValueError("reference slot does not match authoritative parent")
        donor = _donor_for_slot(position, parent.sequence[position], slot_index, donors)
        if donor is None:
            continue
        to_residue = donor["donor_residue"]
        if (position, to_residue) in historical_edits:
            continue
        child = parent.sequence[:position] + to_residue + parent.sequence[position + 1 :]
        child_sha = sha256_text(child)
        if child_sha in seen:
            continue
        before, after = phi(parent.sequence), phi(child)
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
                "proposal_mode": "pepflow_peripheral_source_control_1aa",
                "parent_run_id": str(parent.run_id),
                "parent_candidate_id": str(parent.id),
                "parent_identity_kind": "authoritative_candidate",
                "source_proposal_id": "",
                "source_parent_run_id": str(parent.run_id),
                "source_parent_candidate_id": str(parent.id),
                "source_parent_receipt_sha256": source_generation_receipt_sha256,
                "source_generation_receipt_sha256": source_generation_receipt_sha256,
                "source_score_receipt_sha256": source_score_receipt_sha256,
                "source_reference_proposals_sha256": reference_proposals_sha256,
                "parent_sequence": parent.sequence,
                "parent_sequence_sha256": parent.sequence_sha256,
                "parent_qd_cell": "q4-h2-m3-l2",
                "acceptor_start_zero_based": str(position),
                "from_residue": parent.sequence[position],
                "to_residue": to_residue,
                "donor_candidate_id": donor["donor_candidate_id"],
                "donor_source": donor["donor_source"],
                "donor_sequence": donor["donor_sequence"],
                "donor_fragment": donor["donor_fragment"],
                "donor_residue": donor["donor_residue"],
                "donor_fragment_offset": donor["donor_fragment_offset"],
                "donor_artifact": donor["donor_artifact"],
                "donor_row_number": donor["donor_row_number"],
                "donor_row_sha256": donor["donor_row_sha256"],
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
                            after_value - before_value
                            for after_value, before_value in zip(after, before, strict=True)
                        ],
                    },
                    sort_keys=True,
                ),
                "rescue_reason": "keep_N_terminal_WW_and_match_PepMLM_peripheral_slots",
                "history_gate": "postgresql_exact_sequence_and_parent_edit_passed",
            }
        )
        seen.add(child_sha)
        if len(rows) >= MAX_PROPOSALS:
            break
    return rows


async def collect_pg_state(
    parent_run_id: str,
    parent_candidate_id: str,
    parent_sequence_sha256: str,
    potential_hashes: list[str],
) -> tuple[Candidate, set[str], set[tuple[int, str]]]:
    async with SessionFactory() as session:
        parent = await session.scalar(
            select(Candidate).where(
                Candidate.run_id == UUID(parent_run_id),
                Candidate.id == UUID(parent_candidate_id),
                Candidate.sequence_sha256 == parent_sequence_sha256,
            )
        )
        if parent is None:
            raise ValueError("authoritative VEGFA parent identity not found")
        existing = set(
            await session.scalars(
                select(Candidate.sequence_sha256).where(
                    Candidate.sequence_sha256.in_(potential_hashes)
                )
            )
        )
        metadata_rows = list(
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
    for metadata in metadata_rows:
        position = metadata.get("acceptor_start_zero_based")
        residue = metadata.get("to_residue")
        if isinstance(position, int) and residue:
            edits.add((position, str(residue)))
    return parent, {str(item) for item in existing}, edits


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-receipt", type=Path, required=True)
    parser.add_argument("--reference-proposals", type=Path, required=True)
    parser.add_argument("--source-generation-receipt", type=Path, required=True)
    parser.add_argument("--source-score-receipt", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--history-csv", type=Path, action="append", default=[])
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--preflight-output", type=Path)
    args = parser.parse_args()
    parent_receipt = json.loads(args.parent_receipt.read_text(encoding="utf-8"))
    parent_info = parent_receipt["parent"]
    parent_sequence = str(parent_info["sequence"]).strip().upper()
    parent_hash = str(parent_info["sequence_sha256"]).strip().lower()
    if sha256_text(parent_sequence) != parent_hash:
        raise ValueError("parent receipt sequence/hash mismatch")
    reference = reference_slots(args.reference_proposals)
    donors = donor_rows(args.donor_csv)
    history_hashes, history_edits = history_state(args.history_csv)
    potential_hashes = []
    for index, row in enumerate(reference):
        position = int(row["acceptor_start_zero_based"])
        donor = _donor_for_slot(position, row["from_residue"], index, donors)
        if donor is not None and donor["donor_residue"] != row["from_residue"]:
            potential_hashes.append(
                sha256_text(
                    parent_sequence[:position]
                    + donor["donor_residue"]
                    + parent_sequence[position + 1 :]
                )
            )
    parent, existing, historical_edits = asyncio.run(
        collect_pg_state(
            parent_info["run_id"],
            parent_info["candidate_id"],
            parent_hash,
            potential_hashes,
        )
    )
    if args.preflight_only:
        if args.preflight_output is None:
            raise ValueError("--preflight-output is required with --preflight-only")
        payload = {
            "schema_version": "ampgent.vegfa-pepflow-peripheral-rescue-preflight.1",
            "current_phase": "preflight",
            "target_key": TARGET_KEY,
            "source": SOURCE,
            "operator_id": OPERATOR_ID,
            "seed": SEED,
            "parent_run_id": str(parent.run_id),
            "parent_candidate_id": str(parent.id),
            "parent_sequence_sha256": parent.sequence_sha256,
            "reference_slot_count": len(reference),
            "matched_slot_positions": sorted(
                {int(row["acceptor_start_zero_based"]) for row in reference}
            ),
            "matched_edit_budget": "one_residue_equal_length",
            "donor_artifact": args.donor_csv.as_posix(),
            "donor_artifact_sha256": hashlib.sha256(args.donor_csv.read_bytes()).hexdigest(),
            "donor_row_count": len(donors),
            "donor_source_values": sorted({row["donor_source"] for row in donors}),
            "potential_child_hash_count": len(potential_hashes),
            "pg_exact_existing_child_count": len(existing),
            "historical_edit_key_count": len(historical_edits | history_edits),
            "historical_sequence_hash_count": len(history_hashes),
            "pg_new_formable_count": max(
                0, len(set(potential_hashes) - existing - history_hashes)
            ),
            "generation_started": False,
            "score_all_started": False,
            "materialization_started": False,
        }
        args.preflight_output.parent.mkdir(parents=True, exist_ok=True)
        args.preflight_output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return
    generation_sha = hashlib.sha256(
        args.source_generation_receipt.read_bytes()
    ).hexdigest()
    score_sha = hashlib.sha256(args.source_score_receipt.read_bytes()).hexdigest()
    reference_sha = hashlib.sha256(args.reference_proposals.read_bytes()).hexdigest()
    proposals = build_proposals(
        parent,
        reference,
        donors,
        existing | history_hashes,
        historical_edits | history_edits,
        source_generation_receipt_sha256=generation_sha,
        source_score_receipt_sha256=score_sha,
        reference_proposals_sha256=reference_sha,
    )
    if not proposals:
        raise RuntimeError(
            "no PG-new PepFlow peripheral proposals survived exact/history exclusion"
        )
    args.output_dir.mkdir(parents=True, exist_ok=False)
    _write_csv(args.output_dir / "proposals.csv", proposals)
    receipt = {
        "schema_version": "ampgent.vegfa-pepflow-peripheral-rescue-generation.1",
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
        "reference_source": "VEGFA PepMLM peripheral rescue",
        "reference_proposals_path": args.reference_proposals.as_posix(),
        "reference_proposals_sha256": reference_sha,
        "reference_slot_count": len(reference),
        "matched_slot_positions": sorted(
            {int(row["acceptor_start_zero_based"]) for row in reference}
        ),
        "matched_edit_budget": "one_residue_equal_length",
        "donor_artifact": args.donor_csv.as_posix(),
        "donor_artifact_sha256": hashlib.sha256(args.donor_csv.read_bytes()).hexdigest(),
        "donor_row_count": len(donors),
        "donor_source_values": sorted({row["donor_source"] for row in donors}),
        "source_generation_receipt_sha256": generation_sha,
        "source_score_receipt_sha256": score_sha,
        "pg_exact_checked_child_count": len(potential_hashes),
        "pg_exact_existing_child_count": len(existing),
        "historical_edit_key_count": len(historical_edits | history_edits),
        "historical_sequence_hash_count": len(history_hashes),
        "equal_length_single_residue_count": len(proposals),
        "gpu_rosetta_md_submitted": False,
        "historical_runs_modified": False,
    }
    (args.output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
