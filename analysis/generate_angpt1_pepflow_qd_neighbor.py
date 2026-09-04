"""Generate a bounded, PG-bound ANGPT1 PepFlow one-residue QD-neighbor batch."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from autoresearch_safety_rescue_variants import _historical_sequence_sha256s
from sqlalchemy import select

from pepagent.autoresearch_quality_diversity import behavior_vector
from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory
from pepagent.handoff_metrics import physicochemical_descriptors

OPERATOR_ID = "angpt1-pepflow-qd-neighbor-1aa-v2"
SEED = 20260904
PARENT_RUN_ID = uuid.UUID("195dfbb1-cf6b-5d09-bb50-c1bd14590721")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _true(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _support(metadata: dict[str, Any]) -> int:
    return int(metadata.get("activity_model_support_count", 0) or 0)


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


def _historical_edits(path: Path) -> set[tuple[str, int, str]]:
    if not path.exists():
        return set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return {
            (
                row.get("parent_sequence_sha256", ""),
                int(
                    row.get(
                        "acceptor_start_zero_based",
                        row.get("edit_position_zero_based", "-1"),
                    )
                ),
                row.get("donor_fragment", row.get("to_residue", "")),
            )
            for row in csv.DictReader(stream)
            if row.get("parent_sequence_sha256")
        }


def _donor_fragments(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    fragments: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        source = row.get("donor_source", "").strip().lower()
        fragment = row.get("donor_fragment", "").strip().upper()
        donor_sequence = row.get("donor_sequence", "").strip().upper()
        if source != "pepflow" or len(fragment) != 1 or not donor_sequence:
            continue
        key = (row.get("donor_candidate_id", ""), fragment)
        fragments.setdefault(
            key,
            {
                "donor_candidate_id": row.get("donor_candidate_id", ""),
                "donor_source": "PepFlow",
                "donor_sequence": donor_sequence,
                "donor_fragment": fragment,
            },
        )
    return sorted(
        fragments.values(),
        key=lambda row: (row["donor_candidate_id"], row["donor_fragment"]),
    )


def build_proposals(
    parents: Iterable[dict[str, str]],
    donors: Iterable[dict[str, str]],
    history: set[str],
    historical_edits: set[tuple[str, int, str]],
    *,
    limit: int = 12,
) -> list[dict[str, str]]:
    proposals: list[dict[str, str]] = []
    seen = set(history)
    parent_rows = sorted(
        parents, key=lambda row: (row["qd_cell"], row["sequence_sha256"])
    )
    donor_rows = sorted(
        donors,
        key=lambda row: (row["donor_candidate_id"], row["donor_fragment"]),
    )
    for parent in parent_rows:
        sequence = parent["sequence"].upper()
        parent_sha = parent["sequence_sha256"]
        for donor in donor_rows:
            residue = donor["donor_fragment"]
            for position in sorted({0, len(sequence) // 2, len(sequence) - 1}):
                if sequence[position] == residue:
                    continue
                if (parent_sha, position, residue) in historical_edits:
                    continue
                child = sequence[:position] + residue + sequence[position + 1 :]
                child_sha = sha256_text(child)
                if child_sha in seen:
                    continue
                before, after = _phi(sequence), _phi(child)
                proposals.append(
                    {
                        "sequence": child,
                        "sequence_sha256": child_sha,
                        "branch_key": "angpt1",
                        "target_key": "angpt1",
                        "generation": "2",
                        "seed": str(SEED),
                        "operator_id": OPERATOR_ID,
                        "proposal_mode": "pepflow_same_domain_1aa_qd_neighbor",
                        "parent_run_id": str(PARENT_RUN_ID),
                        "parent_candidate_id": parent["candidate_id"],
                        "parent_sequence": sequence,
                        "parent_sequence_sha256": parent_sha,
                        "parent_qd_cell": parent["qd_cell"],
                        "acceptor_start_zero_based": str(position),
                        "from_residue": sequence[position],
                        "to_residue": residue,
                        "donor_candidate_id": donor["donor_candidate_id"],
                        "donor_source": donor["donor_source"],
                        "donor_sequence": donor["donor_sequence"],
                        "donor_fragment": residue,
                        "actual_cell_preflight": "pending_formal12",
                        "target_cell_hit_preflight": "pending_formal12",
                        "delta_phi": json.dumps(
                            {
                                "axes": [
                                    "charge_density",
                                    "hydrophobicity",
                                    "hydrophobic_moment",
                                    "length",
                                ],
                                "acceptor_to_child": [
                                    after_value - before_value
                                    for before_value, after_value in zip(
                                        before, after, strict=True
                                    )
                                ],
                            },
                            sort_keys=True,
                        ),
                        "history_gate": "postgresql_exact_sequence_and_prior_edit_passed",
                        "parent_display_eligible": parent["display_eligible"],
                        "parent_activity_support_calibrated": parent[
                            "activity_support_calibrated"
                        ],
                    }
                )
                seen.add(child_sha)
                if len(proposals) >= limit:
                    return proposals
    return proposals


async def _load_pg_parents(qd_path: Path) -> list[dict[str, str]]:
    qd = json.loads(await asyncio.to_thread(qd_path.read_text, encoding="utf-8"))
    elite_hashes = {str(item.get("candidate_id", "")) for item in qd.get("elites", [])}
    elite_by_hash = {
        sha256_text(str(item.get("sequence", "")).strip().upper()): item
        for item in qd.get("elites", [])
        if item.get("sequence")
    }
    async with SessionFactory() as session:
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == PARENT_RUN_ID)
                .order_by(Candidate.created_at, Candidate.id)
            )
        )
    selected: list[dict[str, str]] = []
    for candidate in candidates:
        metadata = dict(candidate.metadata_json or {})
        item = elite_by_hash.get(candidate.sequence_sha256)
        if item is None and candidate.sequence_sha256 not in elite_hashes:
            continue
        if not _true(metadata.get("display_eligible")) or _support(metadata) < 2:
            continue
        selected.append(
            {
                "candidate_id": str(candidate.id),
                "sequence": candidate.sequence,
                "sequence_sha256": candidate.sequence_sha256,
                "display_eligible": "true",
                "activity_support_calibrated": str(_support(metadata)),
                "qd_cell": str(item.get("cell_id", "unresolved")),
            }
        )
    return selected


async def run(args: argparse.Namespace) -> None:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    parents = await _load_pg_parents(args.qd_json)
    history = await _historical_sequence_sha256s()
    proposals = build_proposals(
        parents,
        _donor_fragments(args.donor_csv),
        history,
        _historical_edits(args.historical_proposals),
        limit=args.limit,
    )
    if not proposals:
        raise RuntimeError("no PG-new ANGPT1 PepFlow proposals survived exact gates")
    with (output_dir / "proposals.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(proposals[0]))
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.angpt1-pepflow-qd-neighbor-generation.1",
        "target_key": "angpt1",
        "source": "PepFlow",
        "operator_id": OPERATOR_ID,
        "seed": SEED,
        "parent_run_id": str(PARENT_RUN_ID),
        "parent_count": len(parents),
        "donor_fragment_count": len(_donor_fragments(args.donor_csv)),
        "proposal_count": len(proposals),
        "historical_sequence_count": len(history),
        "historical_edit_count": len(_historical_edits(args.historical_proposals)),
        "pg_exact_gate": "sequence_and_prior_edit",
        "gpu_rosetta_md_submitted": False,
        "parent_identity_basis": (
            "authoritative Candidate UUID from one exact PG run; no sequence-only "
            "cross-run parent inference"
        ),
    }
    (output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qd-json", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, required=True)
    parser.add_argument("--historical-proposals", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=12)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
