"""Bounded QD/new-family 3--4 aa cross-source grafts for PBP2a and GyrA."""

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
from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.sequence_family import cluster_sequence_families


def _safe(row: dict[str, str]) -> bool:
    return (
        row.get("display_eligible", "").lower() == "true"
        and row.get("formal_12_complete", "").lower() == "true"
        and int(row.get("activity_model_support_count_calibrated") or 0) >= 2
        and row.get("excellent_sequence_stage_calibrated", "").lower() == "true"
    )


def _phi(sequence: str) -> tuple[float, float, float, float]:
    d = physicochemical_descriptors(sequence, ph=7.4)
    v = behavior_vector(
        sequence,
        net_charge=d["net_charge_ph7_4"],
        hydrophobicity=d["hydrophobic_ratio"],
        hydrophobic_moment=d["hydrophobic_moment"],
    )
    return v.charge_density, v.hydrophobicity, v.hydrophobic_moment, float(v.length)


def _cell(sequence: str, policy: BehaviorSpacePolicy) -> str:
    d = physicochemical_descriptors(sequence, ph=7.4)
    v = behavior_vector(
        sequence,
        net_charge=d["net_charge_ph7_4"],
        hydrophobicity=d["hydrophobic_ratio"],
        hydrophobic_moment=d["hydrophobic_moment"],
    )
    return behavior_cell_id(v, policy)


def generate(parents, donors, archive, history, target, limit):
    policy = BehaviorSpacePolicy.model_validate(archive["policy"])
    empty = set(archive["empty_cell_ids"])
    archive_sequences = [str(item["sequence"]).upper() for item in archive["elites"]]
    family_rows = cluster_sequence_families(archive_sequences)
    existing_families = {item.family_key for item in family_rows}
    parent_rows = [row for row in parents if _safe(row)]
    donor_rows = [
        row
        for row in donors
        if _safe(row)
        and any(
            token in (row.get("donor_source", "") + row.get("proposal_mode", "")).lower()
            for token in ("pepflow", "pepglad")
        )
    ]
    donor_assignments = cluster_sequence_families([row["sequence"].upper() for row in donor_rows])
    family_by_sequence = {item.sequence: item.family_key for item in donor_assignments}
    for row in donor_rows:
        row["donor_family_key_80_80"] = family_by_sequence[row["sequence"].upper()]
    result, seen = [], set(history)
    by_cell = {}
    for parent in sorted(parent_rows, key=lambda row: row["sequence"]):
        acceptor = parent["sequence"].upper()
        for donor in sorted(
            donor_rows, key=lambda row: (row["donor_family_key_80_80"], row["sequence"])
        ):
            donor_seq = donor["sequence"]
            for length in (4, 3):
                fragment = donor_seq[
                    (len(donor_seq) - length) // 2 : (len(donor_seq) - length) // 2 + length
                ].upper()
                for start in sorted({0, (len(acceptor) - length) // 2, len(acceptor) - length}):
                    child = acceptor[:start] + fragment + acceptor[start + length :]
                    digest = hashlib.sha256(child.encode()).hexdigest()
                    if child == acceptor or digest in seen:
                        continue
                    child_assignment = next(
                        item
                        for item in cluster_sequence_families(archive_sequences + [child])
                        if item.sequence == child
                    )
                    child_family = child_assignment.family_key
                    if child_family in existing_families or _cell(child, policy) not in empty:
                        continue
                    before, after = _phi(acceptor), _phi(child)
                    row = {
                        "sequence": child,
                        "sequence_sha256": digest,
                        "branch_key": target,
                        "generation": "1",
                        "proposal_mode": "qd-new-family-block-graft-v7",
                        "parent_sequence": acceptor,
                        "parent_candidate_id": parent.get("candidate_id", ""),
                        "donor_candidate_id": donor.get(
                            "donor_candidate_id", donor.get("candidate_id", "")
                        ),
                        "donor_source": donor.get("donor_source", ""),
                        "donor_family_key_80_80": donor["donor_family_key_80_80"],
                        "donor_fragment": fragment,
                        "acceptor_start_zero_based": start,
                        "micrograft_length": length,
                        "target_cell": _cell(child, policy),
                        "actual_cell_preflight": _cell(child, policy),
                        "target_cell_hit_preflight": "true",
                        "new_family_preflight": "true",
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
                    }
                    by_cell.setdefault(row["target_cell"], row)
                    seen.add(digest)
    for target_cell in sorted(by_cell):
        result.append(by_cell[target_cell])
    return result[:limit], len(parent_rows), len(donor_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("--parent-csv", type=Path, required=True)
    parser.add_argument("--donor-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    wrapper = json.loads(args.archive_json.read_text(encoding="utf-8"))
    archive = wrapper.get("branches", {}).get(args.target, wrapper)
    history = asyncio.run(_historical_sequence_sha256s())
    proposals, parent_count, donor_count = generate(
        list(csv.DictReader(args.parent_csv.open(encoding="utf-8-sig", newline=""))),
        list(csv.DictReader(args.donor_csv.open(encoding="utf-8-sig", newline=""))),
        archive,
        history,
        args.target,
        args.limit,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "proposals.csv"
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(proposals[0]) if proposals else ["sequence"]
        )
        writer.writeheader()
        writer.writerows(proposals)
    receipt = {
        "schema_version": "ampgent.qd-new-family-block-graft-v7.1",
        "operator_id": "qd-new-family-block-graft-v7",
        "target_key": args.target,
        "parent_count": parent_count,
        "donor_count": donor_count,
        "proposal_count": len(proposals),
        "preflight_new_family_count": len(proposals),
        "preflight_target_cell_hit_count": len(proposals),
        "parent_csv_sha256": sha256_file(args.parent_csv),
        "donor_csv_sha256": sha256_file(args.donor_csv),
        "archive_sha256": sha256_file(args.archive_json),
        "proposal_csv_sha256": sha256_file(output),
        "weighted_q_plus_lambda_d_used": False,
        "gpu_rosetta_md_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (args.output_dir / "selection_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
