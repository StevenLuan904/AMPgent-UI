"""Bounded FGF2 QD-gap v5: one-aa versus two-aa empty-cell edits."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from autoresearch_safety_rescue_variants import _historical_sequence_sha256s

from pepagent.autoresearch_quality_diversity import (
    BehaviorSpacePolicy,
    behavior_cell_id,
    behavior_vector,
)
from pepagent.handoff_metrics import physicochemical_descriptors
from pepagent.provenance.hashing import sha256_file, sha256_json

ALPHABET = "ACDEFGHIKLMNPQRSTVWY"


def phi(sequence: str) -> list[float]:
    d = physicochemical_descriptors(sequence, ph=7.4)
    v = behavior_vector(
        sequence,
        net_charge=d["net_charge_ph7_4"],
        hydrophobicity=d["hydrophobic_ratio"],
        hydrophobic_moment=d["hydrophobic_moment"],
    )
    return [v.charge_density, v.hydrophobicity, v.hydrophobic_moment, float(v.length)]


def cell(sequence: str, policy: BehaviorSpacePolicy) -> str:
    d = physicochemical_descriptors(sequence, ph=7.4)
    v = behavior_vector(
        sequence,
        net_charge=d["net_charge_ph7_4"],
        hydrophobicity=d["hydrophobic_ratio"],
        hydrophobic_moment=d["hydrophobic_moment"],
    )
    return behavior_cell_id(v, policy)


def eligible_parents(rows: list[dict[str, str]], archive: dict[str, Any]) -> list[dict[str, str]]:
    elite_ids = {str(row["candidate_id"]) for row in archive["elites"]}
    elite_ids |= {f"proposal-{candidate_id}" for candidate_id in elite_ids}
    elite_sequences = {str(row["sequence"]).upper() for row in archive["elites"]}
    return sorted(
        (
            row
            for row in rows
            if (
                row.get("candidate_id") in elite_ids
                or row.get("sequence", "").upper() in elite_sequences
            )
            and row.get("display_eligible", "").lower() == "true"
            and row.get("formal_12_complete", "").lower() == "true"
            and int(row.get("activity_model_support_count_calibrated") or 0) >= 2
        ),
        key=lambda row: (row.get("candidate_id", ""), row["sequence"]),
    )


def generate_arm(
    parents: list[dict[str, str]],
    history: set[str],
    archive: dict[str, Any],
    arm: str,
    limit: int = 16,
) -> list[dict[str, Any]]:
    policy = BehaviorSpacePolicy.model_validate(archive["policy"])
    empty = set(archive["empty_cell_ids"])
    seen = set(history)
    by_cell: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for parent in parents:
        original = parent["sequence"].upper()
        edits = []
        if arm == "A_one_aa":
            edits = [
                (index, original[index], residue, 1)
                for index in range(len(original))
                for residue in ALPHABET
                if residue != original[index]
            ]
        else:
            edits = [
                (index, original[index : index + 2], fragment, 2)
                for index in range(len(original) - 1)
                for fragment in sorted({
                    row["sequence"][index : index + 2].upper()
                    for row in parents
                    if len(row["sequence"]) == len(original)
                })
                if fragment != original[index : index + 2]
            ]
        for index, source, replacement, length in edits:
            child = original[:index] + replacement + original[index + length :]
            digest = hashlib.sha256(child.encode()).hexdigest()
            target_cell = cell(child, policy)
            if child == original or digest in seen or target_cell not in empty:
                continue
            before, after = phi(original), phi(child)
            row = {
                "sequence": child,
                "sequence_sha256": digest,
                "branch_key": "fgf2",
                "generation": "1",
                "arm": arm,
                "proposal_mode": "fgf2-qd-gap-v5",
                "parent_sequence": original,
                "parent_candidate_id": parent.get("candidate_id", ""),
                "acceptor_start_zero_based": index,
                "source_fragment": source,
                "replacement_fragment": replacement,
                "micrograft_length": length,
                "target_cell": target_cell,
                "actual_cell_preflight": target_cell,
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
            by_cell[target_cell].append(row)
            seen.add(digest)
    result = []
    for target_cell in sorted(by_cell):
        by_cell[target_cell].sort(key=lambda row: row["sequence"])
        result.append(by_cell[target_cell][0])
    return result[:limit]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    parents_rows = list(csv.DictReader(args.parent_csv.open(encoding="utf-8-sig", newline="")))
    archive = json.loads(args.archive_json.read_text(encoding="utf-8"))
    parents = eligible_parents(parents_rows, archive)
    history = asyncio.run(_historical_sequence_sha256s())
    proposals = []
    for arm in ("A_one_aa", "B_two_aa"):
        arm_rows = generate_arm(parents, history, archive, arm)
        proposals.extend(arm_rows)
        history.update(row["sequence_sha256"] for row in arm_rows)
    proposals.sort(key=lambda row: (row["arm"], row["target_cell"], row["sequence"]))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "proposals.csv"
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(proposals[0]) if proposals else ["sequence"]
        )
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.fgf2-qd-gap-v5.1",
        "operator_id": "fgf2-qd-gap-v5",
        "arm_limits": {"A_one_aa": 16, "B_two_aa": 16},
        "parent_count": len(parents),
        "proposal_count": len(proposals),
        "arm_counts": {
            arm: sum(row["arm"] == arm for row in proposals)
            for arm in ("A_one_aa", "B_two_aa")
        },
        "preflight_target_cell_hit_count": len(proposals),
        "target_cells_hit": sorted({row["target_cell"] for row in proposals}),
        "parent_csv_sha256": sha256_file(args.parent_csv),
        "archive_sha256": sha256_file(args.archive_json),
        "proposal_csv_sha256": sha256_file(output),
        "historical_sequence_exclusion_count": len(history),
        "weighted_q_plus_lambda_d_used": False,
        "gpu_rosetta_md_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (args.output_dir / "selection_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
