"""Descriptor-exact QD gap targeting with bounded conservative substitutions."""

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

ALPHABET = "ACDEFGHIKLMNPQRSTVWY"


def phi(sequence: str) -> list[float]:
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


def _safe_parent(row: dict[str, str], target: str) -> bool:
    return (
        row.get("branch_key", "").lower() == target
        and row.get("display_eligible", "").lower() == "true"
        and int(row.get("activity_model_support_count_calibrated") or 0) >= 2
        and row.get("excellent_sequence_stage_calibrated", "").lower() == "true"
    )


def _candidate_cell(sequence: str, policy: BehaviorSpacePolicy) -> str | None:
    descriptor = physicochemical_descriptors(sequence, ph=7.4)
    vector = behavior_vector(
        sequence,
        net_charge=descriptor["net_charge_ph7_4"],
        hydrophobicity=descriptor["hydrophobic_ratio"],
        hydrophobic_moment=descriptor["hydrophobic_moment"],
    )
    return behavior_cell_id(vector, policy)


def generate(
    parents: list[dict[str, str]],
    prior_rows: list[dict[str, str]],
    history: set[str],
    archive: dict[str, Any],
    target: str,
    limit: int = 16,
) -> list[dict[str, Any]]:
    policy = BehaviorSpacePolicy.model_validate(archive["policy"])
    empty_cells = set(archive["empty_cell_ids"])
    donors = sorted(
        {row.get("donor_sequence", "").upper() for row in prior_rows if row.get("donor_sequence")}
    )
    fragments = sorted(
        {
            donor[(len(donor) - length) // 2 : (len(donor) - length) // 2 + length]
            for donor in donors
            for length in (1, 2, 3)
        }
    )
    seen = set(history)
    by_cell: dict[str, list[dict[str, Any]]] = {}
    for parent in sorted(parents, key=lambda row: row["sequence"]):
        sequence = parent["sequence"].upper()
        replacements = [
            (index, residue, 1)
            for index in range(len(sequence))
            for residue in ALPHABET
            if residue != sequence[index]
        ]
        replacements += [
            (index, fragment, len(fragment))
            for fragment in fragments
            for index in range(len(sequence) - len(fragment) + 1)
            if sequence[index : index + len(fragment)] != fragment
        ]
        for index, replacement, length in replacements:
            child = sequence[:index] + replacement + sequence[index + length :]
            digest = hashlib.sha256(child.encode()).hexdigest()
            cell = _candidate_cell(child, policy)
            if child == sequence or digest in seen or cell not in empty_cells:
                continue
            before, after = phi(sequence), phi(child)
            row = {
                "sequence": child,
                "sequence_sha256": digest,
                "branch_key": target,
                "generation": "1",
                "proposal_mode": "qd-gap-directed-reciprocal-micrograft-v3",
                "parent_sequence": sequence,
                "parent_candidate_id": parent.get("candidate_id", ""),
                "donor_source": "PepFlow",
                "donor_fragment": replacement,
                "acceptor_start_zero_based": index,
                "micrograft_length": length,
                "target_cell": cell,
                "actual_cell_preflight": cell,
                "target_cell_hit_preflight": "true",
                "delta_phi_skill": json.dumps(
                    {
                        "axes": [
                            "charge_density",
                            "hydrophobicity",
                            "hydrophobic_moment",
                            "length",
                        ],
                        "acceptor_to_child": [a - b for a, b in zip(after, before, strict=True)],
                    },
                    sort_keys=True,
                ),
            }
            by_cell.setdefault(cell, []).append(row)
            seen.add(digest)
    for rows in by_cell.values():
        rows.sort(key=lambda row: row["sequence"])
    proposals: list[dict[str, Any]] = []
    for cell in sorted(by_cell):
        proposals.extend(by_cell[cell][: max(0, (limit + len(by_cell) - 1) // len(by_cell))])
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
        parents = [row for row in csv.DictReader(stream) if _safe_parent(row, args.target_key)]
    with args.prior_proposals_csv.open(encoding="utf-8-sig", newline="") as stream:
        prior = list(csv.DictReader(stream))
    archive = json.loads(args.archive_json.read_text(encoding="utf-8"))
    history = asyncio.run(_historical_sequence_sha256s())
    proposals = generate(parents, prior, history, archive, args.target_key)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "proposals.csv"
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(proposals[0]) if proposals else ["sequence"]
        )
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.qd-gap-directed-reciprocal-micrograft.3",
        "operator_id": "qd-gap-directed-reciprocal-micrograft-v3",
        "target_key": args.target_key,
        "acceptor_count": len(parents),
        "proposal_count": len(proposals),
        "max_target_total": 16,
        "preflight_target_cell_hit_count": len(proposals),
        "target_cells_hit": sorted({row["target_cell"] for row in proposals}),
        "prior_proposals_sha256": sha256_file(args.prior_proposals_csv),
        "archive_sha256": sha256_file(args.archive_json),
        "proposal_csv_sha256": sha256_file(output),
        "weighted_q_plus_lambda_d_used": False,
        "gpu_rosetta_md_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (args.output_dir / "selection_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
