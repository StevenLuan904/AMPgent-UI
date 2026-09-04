"""Build a bounded ANGPT1 PepMLM target-conditioned QD-neighbor batch.

The parent rows are authoritative candidates from one PG run.  PepMLM rows
are used only as source donors; no ordinary mutation is relabelled as model
output.  A proposal is admitted to this preflight only when its descriptor
cell is an empty cell in the frozen ANGPT1 archive.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path

from autoresearch_safety_rescue_variants import _historical_sequence_sha256s

from pepagent.autoresearch_quality_diversity import (
    BehaviorSpacePolicy,
    behavior_cell_id,
    behavior_vector,
)
from pepagent.handoff_metrics import physicochemical_descriptors

OPERATOR_ID = "angpt1-pepmlm-qd-neighbor-1aa-v1"
SOURCE = "PepMLM-target-conditioned"
TARGET = "angpt1"
SEED = 20260904
LIMIT = 12


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()


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
        float(vector.length),
    ]


def _archive(path: Path) -> tuple[set[str], BehaviorSpacePolicy, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    branch = payload.get("branches", {}).get(TARGET, payload)
    covered = {str(item) for item in branch.get("covered_cell_ids", [])}
    policy = BehaviorSpacePolicy(**branch.get("policy", payload.get("policy", {})))
    return covered, policy, _sha(path.read_text(encoding="utf-8"))


def _donors(path: Path) -> list[dict[str, str]]:
    rows = _read(path)
    rows = [
        row
        for row in rows
        if row.get("display_eligible", "").casefold() == "true"
        and int(row.get("activity_model_support_count_calibrated", "0") or 0) >= 2
    ]
    rows.sort(key=lambda row: (row.get("sequence_sha256", ""), row.get("candidate_id", "")))
    rows = rows[:8]
    result: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        sequence = row.get("sequence", "").strip().upper()
        donor_id = row.get("candidate_id", "")
        if not sequence or not donor_id:
            continue
        # Source donor residues are deterministic, source-traceable, and do
        # not use the parent sequence as a pseudo model prediction.
        for residue in sorted(set(sequence)):
            result.setdefault(
                (donor_id, residue),
                {
                    "donor_candidate_id": donor_id,
                    "donor_source": SOURCE,
                    "donor_sequence": sequence,
                    "donor_residue": residue,
                    "donor_artifact": str(path),
                    "donor_sequence_sha256": _sha(sequence),
                },
            )
    return sorted(
        result.values(),
        key=lambda row: (row["donor_candidate_id"], row["donor_residue"]),
    )


def build(
    parents: list[dict[str, str]],
    donors: list[dict[str, str]],
    history: set[str],
    covered_cells: set[str],
    policy: BehaviorSpacePolicy,
    *,
    parent_run_id: str,
    limit: int = LIMIT,
) -> list[dict[str, str]]:
    if limit < 1 or limit > LIMIT:
        raise ValueError("limit must be between 1 and 12")
    seen = set(history)
    proposals: list[dict[str, str]] = []
    # Keep each authoritative parent represented before taking another row.
    parent_rows = sorted(
        {
            (
                row.get("parent_candidate_id", ""),
                row.get("parent_sequence", ""),
                row.get("parent_sequence_sha256", ""),
                row.get("parent_qd_cell", ""),
            )
            for row in parents
            if row.get("parent_candidate_id") and row.get("parent_sequence")
        }
    )
    donor_rows = donors
    per_parent: list[list[dict[str, str]]] = []
    for parent_id, parent_sequence, parent_sha, parent_cell in parent_rows:
        sequence = parent_sequence.upper()
        if parent_sha != _sha(sequence):
            raise ValueError("parent sequence hash drifted")
        candidates: list[dict[str, str]] = []
        local_seen: set[str] = set()
        for donor in donor_rows:
            residue = donor["donor_residue"]
            for position in (0, 1, len(sequence) - 2, len(sequence) - 1):
                if sequence[position] == residue:
                    continue
                child = sequence[:position] + residue + sequence[position + 1 :]
                child_sha = _sha(child)
                if child_sha in seen or child_sha in local_seen:
                    continue
                descriptor = physicochemical_descriptors(child, ph=7.4)
                vector = behavior_vector(
                    child,
                    net_charge=descriptor["net_charge_ph7_4"],
                    hydrophobicity=descriptor["hydrophobic_ratio"],
                    hydrophobic_moment=descriptor["hydrophobic_moment"],
                )
                cell = behavior_cell_id(vector, policy)
                if cell is None or cell in covered_cells:
                    continue
                before, after = _phi(sequence), _phi(child)
                candidates.append({
                    "sequence": child,
                    "sequence_sha256": child_sha,
                    "branch_key": TARGET,
                    "target_key": TARGET,
                    "generation": "1",
                    "seed": str(SEED),
                    "operator_id": OPERATOR_ID,
                    "proposal_mode": "pepmlm_target_conditioned_1aa_qd_neighbor",
                    "source": SOURCE,
                    "parent_run_id": parent_run_id,
                    "parent_candidate_id": parent_id,
                    "parent_sequence": sequence,
                    "parent_sequence_sha256": parent_sha,
                    "parent_qd_cell": parent_cell,
                    "acceptor_start_zero_based": str(position),
                    "from_residue": sequence[position],
                    "to_residue": residue,
                    **donor,
                    "actual_cell_preflight": cell,
                    "target_cell_hit_preflight": "true",
                    "delta_phi": json.dumps(
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
                })
                local_seen.add(child_sha)
        candidates.sort(
            key=lambda row: (
                row["actual_cell_preflight"],
                row["sequence_sha256"],
                row["donor_candidate_id"],
            )
        )
        per_parent.append(candidates)
    index = 0
    while len(proposals) < limit and any(index < len(items) for items in per_parent):
        for candidates in per_parent:
            if index >= len(candidates) or len(proposals) >= limit:
                continue
            row = candidates[index]
            proposals.append(row)
            seen.add(row["sequence_sha256"])
        index += 1
    return proposals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-csv", type=Path, required=True)
    parser.add_argument("--pepmlm-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--history-csv", type=Path)
    parser.add_argument("--parent-run-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=LIMIT)
    args = parser.parse_args()
    covered, policy, archive_sha = _archive(args.archive_json)
    history = (
        {row.get("sequence_sha256", "") for row in _read(args.history_csv)}
        if args.history_csv
        else asyncio.run(_historical_sequence_sha256s())
    )
    proposals = build(
        _read(args.parent_csv),
        _donors(args.pepmlm_csv),
        history,
        covered,
        policy,
        parent_run_id=args.parent_run_id,
        limit=args.limit,
    )
    if len(proposals) < args.limit:
        raise RuntimeError(f"only {len(proposals)} PG-new empty-cell proposals available")
    args.output_dir.mkdir(parents=False, exist_ok=False)
    with (args.output_dir / "proposals.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(proposals[0]))
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.angpt1-pepmlm-qd-neighbor-generation.1",
        "target_key": TARGET,
        "source": SOURCE,
        "operator_id": OPERATOR_ID,
        "seed": SEED,
        "parent_run_id": args.parent_run_id,
        "parent_count": len({row["parent_candidate_id"] for row in proposals}),
        "donor_count": len({row["donor_candidate_id"] for row in proposals}),
        "proposal_count": len(proposals),
        "registered_empty_cell_count": policy.total_cell_count - len(covered),
        "preflight_empty_cell_hit_count": len({row["actual_cell_preflight"] for row in proposals}),
        "archive_sha256": archive_sha,
        "historical_sequence_exclusion_count": len(history),
        "gpu_rosetta_md_submitted": False,
    }
    (args.output_dir / "generation_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
