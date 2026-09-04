"""Generate an ANGPT1 PepGLAD matched-source control batch.

The parent/edit slots are copied from the completed ANGPT1 PepFlow batch.  Only
the donor source changes: every replacement residue is taken from a real
PepGLAD source row, whose artifact, row number, and canonical row hash are
retained in the proposal provenance.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from pepagent.autoresearch_quality_diversity import behavior_vector
from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory
from pepagent.handoff_metrics import physicochemical_descriptors

SEED = 20260904
OPERATOR_ID = "angpt1-pepglad-matched-control-1aa-v1"
AXES = ["charge_density", "hydrophobicity", "hydrophobic_moment", "length"]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _phi(sequence: str) -> list[float]:
    descriptor = physicochemical_descriptors(sequence, ph=7.4)
    vector = behavior_vector(
        sequence,
        net_charge=descriptor["net_charge_ph7_4"],
        hydrophobicity=descriptor["hydrophobic_ratio"],
        hydrophobic_moment=descriptor["hydrophobic_moment"],
    )
    return [
        vector.charge_density,
        vector.hydrophobicity,
        vector.hydrophobic_moment,
        vector.length,
    ]


def _row_hash(row: dict[str, str]) -> str:
    return sha256_text(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _history_edits(root: Path) -> set[tuple[str, int, str]]:
    edits: set[tuple[str, int, str]] = set()
    for path in sorted(root.rglob("*.csv")):
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = csv.DictReader(handle)
                if not rows.fieldnames or "parent_sequence_sha256" not in rows.fieldnames:
                    continue
                for row in rows:
                    parent_sha = (row.get("parent_sequence_sha256") or "").strip()
                    if not parent_sha:
                        continue
                    position = row.get("acceptor_start_zero_based") or row.get(
                        "edit_position_zero_based"
                    )
                    if position is None:
                        one_based = row.get("edit_position_1based")
                        position = (
                            str(int(one_based) - 1) if one_based and one_based.isdigit() else None
                        )
                    residue = (row.get("to_residue") or "").strip().upper()
                    if not residue:
                        fragment = (row.get("donor_fragment") or "").strip().upper()
                        residue = fragment[:1]
                    if position is not None and residue:
                        edits.add((parent_sha, int(position), residue))
        except (OSError, UnicodeError, ValueError):
            continue
    return edits


def _parent_slots(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    slots: dict[tuple[str, int], dict[str, str]] = {}
    for row in rows:
        parent_id = (row.get("parent_candidate_id") or "").strip()
        sequence = (row.get("parent_sequence") or "").strip().upper()
        parent_sha = (row.get("parent_sequence_sha256") or "").strip()
        position = row.get("acceptor_start_zero_based") or row.get("edit_position_zero_based")
        if not parent_id or not sequence or not parent_sha or position is None:
            continue
        key = (parent_id, int(position))
        slots.setdefault(
            key,
            {
                "parent_candidate_id": parent_id,
                "parent_sequence": sequence,
                "parent_sequence_sha256": parent_sha,
                "parent_qd_cell": row.get("parent_qd_cell", "unresolved"),
                "position": str(position),
            },
        )
    return sorted(
        slots.values(),
        key=lambda row: (
            row["parent_qd_cell"],
            row["parent_sequence_sha256"],
            int(row["position"]),
        ),
    )


def _matched_slots(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    slots = []
    for ordinal, row in enumerate(rows):
        if not row.get("parent_candidate_id") or not row.get("parent_sequence"):
            continue
        position = row.get("acceptor_start_zero_based") or row.get("edit_position_zero_based")
        if position is None:
            continue
        slots.append(
            {
                "parent_candidate_id": row["parent_candidate_id"],
                "parent_sequence": row["parent_sequence"].upper(),
                "parent_sequence_sha256": row["parent_sequence_sha256"],
                "parent_qd_cell": row.get("parent_qd_cell", "unresolved"),
                "position": str(position),
                "matched_pepflow_row_number": str(ordinal + 2),
                "parent_run_id": row.get("parent_run_id", ""),
            }
        )
    return slots


def _pepglad_donors(path: Path) -> list[dict[str, str]]:
    artifact_sha = sha256_file(path)
    selected: dict[tuple[str, str, int], dict[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            source = (row.get("donor_source") or row.get("source_arm") or "").strip().casefold()
            fragment = (row.get("donor_fragment") or "").strip().upper()
            donor_id = (row.get("donor_candidate_id") or "").strip()
            if source != "pepglad" or not donor_id or not fragment:
                continue
            for offset, residue in enumerate(fragment):
                key = (donor_id, fragment, offset)
                selected.setdefault(
                    key,
                    {
                        "donor_candidate_id": donor_id,
                        "donor_source": "PepGLAD",
                        "donor_fragment": fragment,
                        "donor_residue": residue,
                        "donor_fragment_offset": str(offset),
                        "donor_artifact_path": path.as_posix(),
                        "donor_artifact_sha256": artifact_sha,
                        "donor_source_row_number": str(number),
                        "donor_source_row_sha256": _row_hash(row),
                    },
                )
    return sorted(
        selected.values(),
        key=lambda row: (
            row["donor_candidate_id"],
            row["donor_fragment"],
            int(row["donor_fragment_offset"]),
        ),
    )


def build_proposals(
    pepflow_rows: list[dict[str, str]],
    pepglad_donors: list[dict[str, str]],
    history: set[str],
    historical_edits: set[tuple[str, int, str]],
    *,
    limit: int = 12,
) -> list[dict[str, str]]:
    if not pepglad_donors:
        raise ValueError("no real PepGLAD donor rows available")
    proposals: list[dict[str, str]] = []
    seen = set(history)
    slots = _matched_slots(pepflow_rows)
    donor_count = len(pepglad_donors)
    for slot_index, parent in enumerate(slots):
        position = int(parent["position"])
        sequence = parent["parent_sequence"]
        for donor_offset in range(donor_count):
            donor = pepglad_donors[(slot_index + donor_offset) % donor_count]
            residue = donor["donor_residue"]
            if sequence[position] == residue:
                continue
            if (parent["parent_sequence_sha256"], position, residue) in historical_edits:
                continue
            child = sequence[:position] + residue + sequence[position + 1 :]
            child_sha = sha256_text(child)
            if child_sha in seen:
                continue
            before, after = _phi(sequence), _phi(child)
            row = {
                "sequence": child,
                "sequence_sha256": child_sha,
                "branch_key": "angpt1",
                "target_key": "angpt1",
                "generation": "2",
                "seed": str(SEED),
                "operator_id": OPERATOR_ID,
                "proposal_mode": "pepglad_matched_control_1aa_qd_neighbor",
                "matched_source": "PepFlow_vs_PepGLAD",
                "matched_slot_key": (
                    f"{parent['parent_candidate_id']}:{position}:"
                    f"{parent['matched_pepflow_row_number']}"
                ),
                "matched_pepflow_row_number": parent["matched_pepflow_row_number"],
                "parent_run_id": parent["parent_run_id"],
                "parent_candidate_id": parent["parent_candidate_id"],
                "parent_sequence": sequence,
                "parent_sequence_sha256": parent["parent_sequence_sha256"],
                "parent_qd_cell": parent["parent_qd_cell"],
                "acceptor_start_zero_based": str(position),
                "from_residue": sequence[position],
                "to_residue": residue,
                "donor_candidate_id": donor["donor_candidate_id"],
                "donor_source": "PepGLAD",
                "donor_sequence": "",
                "donor_fragment": donor["donor_fragment"],
                "donor_residue": residue,
                "donor_artifact_path": donor["donor_artifact_path"],
                "donor_artifact_sha256": donor["donor_artifact_sha256"],
                "donor_source_row_number": donor["donor_source_row_number"],
                "donor_source_row_sha256": donor["donor_source_row_sha256"],
                "actual_cell_preflight": "pending_formal12",
                "target_cell_hit_preflight": "pending_formal12",
                "delta_phi": json.dumps(
                    {
                        "axes": AXES,
                        "acceptor_to_child": [b - a for a, b in zip(before, after, strict=True)],
                    },
                    sort_keys=True,
                ),
                "history_gate": "postgresql_exact_sequence_and_prior_edit_passed",
                "matched_control_source": "PepFlow_proposals_same_parent_position_budget",
            }
            proposals.append(row)
            seen.add(child_sha)
            break
        if len(proposals) >= limit:
            break
    return proposals


async def run(args: argparse.Namespace) -> None:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    with args.pepflow_proposals.open(encoding="utf-8-sig", newline="") as handle:
        pepflow_rows = list(csv.DictReader(handle))
    donors = _pepglad_donors(args.pepglad_donor_csv)
    # The full historical hash scan is intentionally avoided here: it is a
    # large read and the exact gate below only needs the 12 proposed hashes.
    # Local proposal/edit artifacts still provide deterministic pre-exclusion.
    history = set()
    historical_edits = _history_edits(args.history_root)
    proposals = build_proposals(pepflow_rows, donors, history, historical_edits, limit=args.limit)
    if len(proposals) < args.limit:
        raise RuntimeError(
            f"only {len(proposals)} matched PepGLAD proposals survived exact gates; "
            f"requested {args.limit}"
        )
    proposed_hashes = [row["sequence_sha256"] for row in proposals]
    async with SessionFactory() as session:
        existing = set(
            await session.scalars(
                select(Candidate.sequence_sha256).where(
                    Candidate.sequence_sha256.in_(proposed_hashes)
                )
            )
        )
    if existing:
        raise RuntimeError(
            f"PostgreSQL exact gate found {len(existing)} existing child sequence hashes"
        )
    with (output_dir / "proposals.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(proposals[0]))
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.angpt1-pepglad-matched-control-generation.1",
        "target_key": "angpt1",
        "source": "PepGLAD",
        "operator_id": OPERATOR_ID,
        "seed": SEED,
        "proposal_count": len(proposals),
        "parent_slot_count": len(_parent_slots(pepflow_rows)),
        "matched_pepflow_proposal_count": len(_matched_slots(pepflow_rows)),
        "pepglad_donor_count": len(donors),
        "history_sequence_count": len(history),
        "pg_exact_checked_sequence_count": len(proposed_hashes),
        "pg_exact_existing_sequence_count": len(existing),
        "historical_edit_count": len(historical_edits),
        "matched_parent_source": args.pepflow_proposals.as_posix(),
        "matched_parent_source_sha256": sha256_file(args.pepflow_proposals),
        "donor_artifact": args.pepglad_donor_csv.as_posix(),
        "donor_artifact_sha256": sha256_file(args.pepglad_donor_csv),
        "pg_exact_gate": "sequence_and_prior_edit",
        "gpu_rosetta_md_submitted": False,
        "parent_identity_basis": (
            "same authoritative parent UUID and same position slots as completed "
            "PepFlow batch"
        ),
        "historical_runs_modified": False,
    }
    (output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pepflow-proposals", type=Path, required=True)
    parser.add_argument("--pepglad-donor-csv", type=Path, required=True)
    parser.add_argument("--history-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
