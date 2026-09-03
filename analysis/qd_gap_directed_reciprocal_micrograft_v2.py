"""Bounded QD-gap-directed 1–2 aa reciprocal micrografts (CPU only)."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from autoresearch_safety_rescue_variants import _historical_sequence_sha256s

from pepagent.autoresearch_quality_diversity import (
    BehaviorSpacePolicy,
    behavior_cell_id,
    behavior_vector,
)
from pepagent.developability import sequence_developability_metrics
from pepagent.handoff_metrics import physicochemical_descriptors
from pepagent.provenance.hashing import sha256_file, sha256_json


def _phi(sequence: str) -> list[float]:
    sequence_developability_metrics(sequence)
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


def _indices(cell_id: str) -> tuple[int, int, int, int]:
    return tuple(int(part[1:]) for part in cell_id.split("-"))  # type: ignore[return-value]


def nearest_empty_cell(archive: dict[str, Any]) -> str:
    empty = list(archive["empty_cell_ids"])
    covered = list(archive["covered_cell_ids"])
    if not empty or not covered:
        raise ValueError("archive must contain covered and empty cells")
    return min(
        empty,
        key=lambda cell: (
            min(
                sum(abs(a - b) for a, b in zip(_indices(cell), _indices(source), strict=True))
                for source in covered
            ),
            _indices(cell),
        ),
    )


def _cell_distance(cell_id: str | None, target_cell: str) -> int:
    if cell_id is None:
        return 99
    return sum(abs(a - b) for a, b in zip(_indices(cell_id), _indices(target_cell), strict=True))


def _safe_parent(row: dict[str, str], target: str) -> bool:
    return (
        row.get("branch_key", "").lower() == target
        and row.get("display_eligible", "").lower() == "true"
        and int(row.get("activity_model_support_count_calibrated") or 0) >= 2
        and row.get("excellent_sequence_stage_calibrated", "").lower() == "true"
    )


def generate(
    acceptors: list[dict[str, str]],
    donor_rows: list[dict[str, str]],
    history: set[str],
    archive: dict[str, Any],
    target: str,
    limit: int = 16,
) -> list[dict[str, Any]]:
    policy = BehaviorSpacePolicy.model_validate(archive["policy"])
    target_cell = nearest_empty_cell(archive)
    donors: dict[str, dict[str, str]] = {}
    for row in donor_rows:
        sequence = row.get("donor_sequence", "").upper()
        if sequence:
            donors.setdefault(sequence, row)
    proposals: list[dict[str, Any]] = []
    seen = set(history)
    for parent in sorted(
        acceptors, key=lambda row: (row.get("sequence", ""), row.get("candidate_id", ""))
    ):
        sequence = parent["sequence"].upper()
        for donor_sequence, donor in sorted(donors.items()):
            for length in (1, 2):
                fragment = donor_sequence[
                    (len(donor_sequence) - length) // 2 : (len(donor_sequence) - length) // 2
                    + length
                ]
                for position in range(0, len(sequence) - length + 1):
                    child = sequence[:position] + fragment + sequence[position + length :]
                    child_hash = hashlib.sha256(child.encode()).hexdigest()
                    if child == sequence or child_hash in seen:
                        continue
                    before, after = _phi(sequence), _phi(child)
                    descriptor = physicochemical_descriptors(child, ph=7.4)
                    vector = behavior_vector(
                        child,
                        net_charge=descriptor["net_charge_ph7_4"],
                        hydrophobicity=descriptor["hydrophobic_ratio"],
                        hydrophobic_moment=descriptor["hydrophobic_moment"],
                    )
                    actual_cell = behavior_cell_id(vector, policy)
                    proposals.append(
                        {
                            "sequence": child,
                            "sequence_sha256": child_hash,
                            "branch_key": target,
                            "generation": "1",
                            "proposal_mode": "qd-gap-directed-reciprocal-micrograft-v2",
                            "parent_sequence": sequence,
                            "parent_candidate_id": parent.get("candidate_id", ""),
                            "donor_candidate_id": donor.get("donor_candidate_id", ""),
                            "donor_sequence": donor_sequence,
                            "donor_fragment": fragment,
                            "micrograft_length": length,
                            "acceptor_start_zero_based": position,
                            "target_cell": target_cell,
                            "actual_cell_preflight": actual_cell or "outside_behavior_space",
                            "target_cell_hit_preflight": str(actual_cell == target_cell).lower(),
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
                            "preflight_cell_distance": _cell_distance(actual_cell, target_cell),
                        }
                    )
                    seen.add(child_hash)
    proposals.sort(
        key=lambda row: (
            row["preflight_cell_distance"],
            row["target_cell_hit_preflight"] != "true",
            row["sequence"],
        )
    )
    return proposals[:limit]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptor-csv", type=Path, required=True)
    parser.add_argument("--prior-proposals-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--target-key", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    with args.acceptor_csv.open(encoding="utf-8-sig", newline="") as stream:
        acceptors = [row for row in csv.DictReader(stream) if _safe_parent(row, args.target_key)]
    with args.prior_proposals_csv.open(encoding="utf-8-sig", newline="") as stream:
        donors = list(csv.DictReader(stream))
    archive = json.loads(args.archive_json.read_text(encoding="utf-8"))
    history = asyncio.run(_historical_sequence_sha256s())
    proposals = generate(acceptors, donors, history, archive, args.target_key)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "proposals.csv"
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(proposals[0]) if proposals else ["sequence"]
        )
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.qd-gap-directed-reciprocal-micrograft.2",
        "operator_id": "qd-gap-directed-reciprocal-micrograft-v2",
        "target_key": args.target_key,
        "acceptor_count": len(acceptors),
        "proposal_count": len(proposals),
        "max_target_total": 16,
        "target_cell": nearest_empty_cell(archive),
        "preflight_target_cell_hit_count": sum(
            row["target_cell_hit_preflight"] == "true" for row in proposals
        ),
        "prior_proposals_sha256": sha256_file(args.prior_proposals_csv),
        "archive_sha256": sha256_file(args.archive_json),
        "proposal_csv_sha256": sha256_file(output),
        "history_size": len(history),
        "weighted_q_plus_lambda_d_used": False,
        "gpu_rosetta_md_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (args.output_dir / "selection_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
