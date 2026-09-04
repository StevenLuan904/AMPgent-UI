"""Generate a bounded ANGPT1 PepGLAD one-residue source expansion."""

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
OPERATOR_ID = "acea-pepglad-source-expansion-1aa-v1"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def phi(sequence: str) -> list[float]:
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
        float(vector.length),
    ]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def prior_edits(paths: list[Path]) -> set[tuple[str, int, str]]:
    edits: set[tuple[str, int, str]] = set()
    for path in paths:
        if not path.exists():
            continue
        try:
            rows = read_rows(path)
        except (OSError, UnicodeError, ValueError):
            continue
        for row in rows:
            parent = (row.get("parent_sequence") or "").strip().upper()
            child = (row.get("sequence") or "").strip().upper()
            if len(parent) != len(child) or not parent:
                continue
            changed = [
                i for i, pair in enumerate(zip(parent, child, strict=True)) if pair[0] != pair[1]
            ]
            if len(changed) == 1:
                edits.add((sha256_text(parent), changed[0], child[changed[0]]))
    return edits


def donors(path: Path) -> list[dict[str, str]]:
    selected: dict[tuple[str, str, int], dict[str, str]] = {}
    for row_number, row in enumerate(read_rows(path), start=2):
        if (row.get("donor_source") or row.get("source_arm") or "").strip().casefold() != "pepglad":
            continue
        donor_id = (row.get("donor_candidate_id") or "").strip()
        fragment = (row.get("donor_fragment") or "").strip().upper()
        if not donor_id or not fragment.isalpha():
            continue
        for offset, residue in enumerate(fragment):
            selected.setdefault(
                (donor_id, fragment, offset),
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
    return sorted(
        selected.values(),
        key=lambda row: (
            row["donor_candidate_id"],
            row["donor_fragment"],
            row["donor_fragment_offset"],
        ),
    )


def build_proposals(
    parent_rows: list[dict[str, str]],
    donor_rows: list[dict[str, str]],
    history: set[str],
    edits: set[tuple[str, int, str]],
    *,
    target_key: str,
    parent_run_id: str,
    limit: int = 12,
) -> list[dict[str, str]]:
    if not donor_rows:
        raise ValueError("no real PepGLAD one-residue donor asset")
    proposals: list[dict[str, str]] = []
    seen = set(history)
    parents = {}
    for row in parent_rows:
        parent_id = row.get("parent_candidate_id", "").strip()
        sequence = row.get("parent_sequence", "").strip().upper()
        parent_sha = row.get("parent_sequence_sha256", "").strip()
        if parent_id and sequence and parent_sha:
            parents[parent_id] = {
                "candidate_id": parent_id,
                "sequence": sequence,
                "sequence_sha256": parent_sha,
                "qd_cell": row.get("parent_qd_cell", "unresolved"),
            }
    for parent in sorted(
        parents.values(), key=lambda row: (row["qd_cell"], row["sequence_sha256"])
    ):
        sequence = parent["sequence"]
        for position in list(range(len(sequence))) + [len(sequence) // 2, 0, len(sequence) - 1]:
            for donor in donor_rows:
                residue = donor["donor_residue"]
                if (
                    residue == sequence[position]
                    or (parent["sequence_sha256"], position, residue) in edits
                ):
                    continue
                child = sequence[:position] + residue + sequence[position + 1 :]
                child_sha = sha256_text(child)
                if child_sha in seen:
                    continue
                before, after = phi(sequence), phi(child)
                proposals.append(
                    {
                        "sequence": child,
                        "sequence_sha256": child_sha,
                        "branch_key": target_key,
                        "target_key": target_key,
                        "generation": "3",
                        "seed": str(SEED),
                        "operator_id": OPERATOR_ID,
                        "proposal_mode": "pepglad_source_1aa_qd_neighbor",
                        "parent_run_id": parent_run_id,
                        "parent_candidate_id": parent["candidate_id"],
                        "parent_sequence": sequence,
                        "parent_sequence_sha256": parent["sequence_sha256"],
                        "parent_qd_cell": parent["qd_cell"],
                        "acceptor_start_zero_based": str(position),
                        "from_residue": sequence[position],
                        "to_residue": residue,
                        "donor_candidate_id": donor["donor_candidate_id"],
                        "donor_source": "PepGLAD",
                        "donor_fragment": donor["donor_fragment"],
                        "donor_residue": residue,
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
                                    a - b for a, b in zip(after, before, strict=True)
                                ],
                            },
                            sort_keys=True,
                        ),
                        "history_gate": "postgresql_exact_sequence_and_prior_edit_passed",
                    }
                )
                seen.add(child_sha)
                if len(proposals) >= limit:
                    return proposals
    return proposals


async def pg_new(hashes: list[str]) -> int:
    async with SessionFactory() as session:
        found = list(
            await session.scalars(
                select(Candidate.sequence_sha256).where(Candidate.sequence_sha256.in_(hashes))
            )
        )
    return len(set(found))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-proposals", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, required=True)
    parser.add_argument("--history-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-key", default="acea")
    parser.add_argument("--parent-run-id", required=True)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    parent_rows = read_rows(args.parent_proposals)
    donor_rows = donors(args.donor_csv)
    history_paths = sorted(args.history_root.rglob("*.csv"))
    edits = prior_edits(history_paths)
    proposals = build_proposals(
        parent_rows,
        donor_rows,
        set(),
        edits,
        target_key=args.target_key,
        parent_run_id=args.parent_run_id,
        limit=args.limit,
    )
    if len(proposals) != args.limit:
        raise RuntimeError(f"only {len(proposals)} PG-new proposals survived prior-edit exclusion")
    existing = asyncio.run(pg_new([row["sequence_sha256"] for row in proposals]))
    if existing:
        raise RuntimeError(f"PostgreSQL exact gate found {existing} proposed sequences")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    csv_path = args.output_dir / "proposals.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(proposals[0]))
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.angpt1-pepglad-source-expansion-generation.1",
        "target_key": args.target_key,
        "source": "PepGLAD",
        "operator_id": OPERATOR_ID,
        "seed": SEED,
        "generation": 3,
        "proposal_count": len(proposals),
        "parent_run_id": args.parent_run_id,
        "parent_count": len({row["parent_candidate_id"] for row in proposals}),
        "donor_count": len(donor_rows),
        "historical_edit_count": len(edits),
        "pg_exact_checked_count": len(proposals),
        "pg_exact_existing_count": 0,
        "gpu_rosetta_md_submitted": False,
        "historical_runs_modified": False,
    }
    (args.output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
