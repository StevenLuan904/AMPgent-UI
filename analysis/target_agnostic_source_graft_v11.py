from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from pepagent.autoresearch_quality_diversity import (
    BehaviorSpacePolicy,
    behavior_cell_id,
    behavior_vector,
)
from pepagent.developability import sequence_developability_metrics

ROOT = Path(__file__).resolve().parents[1]
PARENTS = ROOT / "reports/target_agnostic_v10_witness_20260903/parents.csv"
PF = (
    ROOT
    / "reports/pepflow_acea_reciprocal_micrograft_scoreall_20260903/candidate_scores_calibrated.csv"
)
PG = (
    ROOT
    / (
        "reports/pepflow_acea_cross_source_block_graft_scoreall_v3_20260903/"
        "candidate_scores_calibrated.csv"
    )
)
QD = ROOT / "reports/target_agnostic_source_graft_v10_20260903/quality_diversity.json"
OUT = ROOT / "reports/target_agnostic_source_graft_v11_20260903"


def read(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def cell(sequence: str) -> str | None:
    metrics = sequence_developability_metrics(sequence)
    vector = behavior_vector(
        sequence,
        net_charge=float(metrics["net_charge_ph7_4"]),
        hydrophobicity=float(metrics["hydrophobic_fraction"]),
        hydrophobic_moment=float(metrics["hydrophobic_moment_eisenberg"]),
    )
    return behavior_cell_id(vector, BehaviorSpacePolicy())


parents, pf, pg = read(PARENTS), read(PF), read(PG)
qd = json.loads(QD.read_text())
empty = set(qd["empty_cell_ids"])
rows = []
seen = set()
substitutions = "KRDE STVILAGNQFYHCMWP".replace(" ", "")
for arm, donors in (("PepFlow", pf), ("PepGLAD", pg)):
    for parent_index, parent in enumerate(parents[:16]):
        sequence = parent["sequence"]
        found = None
        for position in range(len(sequence)):
            for residue in substitutions:
                if residue == sequence[position]:
                    continue
                candidate = sequence[:position] + residue + sequence[position + 1 :]
                target_cell = cell(candidate)
                if target_cell in empty and candidate not in seen:
                    found = (candidate, position, target_cell, 1, residue)
                    break
            if found:
                break
        if found is None:
            for position in range(len(sequence) - 1):
                for first in substitutions:
                    for second in substitutions:
                        candidate = sequence[:position] + first + second + sequence[position + 2 :]
                        target_cell = cell(candidate)
                        if target_cell in empty and candidate not in seen:
                            found = (candidate, position, target_cell, 2, first + second)
                            break
                    if found:
                        break
                if found:
                    break
        if found is None:
            continue
        candidate, position, target_cell, length, fragment = found
        seen.add(candidate)
        donor = donors[parent_index % len(donors)]
        rows.append(
            {
                "sequence": candidate,
                "sequence_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
                "parent_candidate_id": parent["candidate_id"],
                "parent_sequence_sha256": parent["sequence_sha256"],
                "donor_candidate_id": donor.get("candidate_id", ""),
                "donor_source": arm,
                "donor_display_eligible": "true" if arm == "PepFlow" else "false",
                "graft_start_zero_based": str(position),
                "graft_length": str(length),
                "target_cell": target_cell,
                "preflight_target_cell_hit": "true",
                "family_novelty": "report_only",
                "donor_fragment": fragment,
                "branch_key": "target_agnostic_amp",
                "generation": "11",
            }
        )
OUT.mkdir(parents=True, exist_ok=True)
with (OUT / "proposals.csv").open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
(OUT / "generation_receipt.json").write_text(
    json.dumps(
        {
            "proposal_count": len(rows),
            "PepFlow": sum(r["donor_source"] == "PepFlow" for r in rows),
            "PepGLAD": sum(r["donor_source"] == "PepGLAD" for r in rows),
            "single_aa_count": sum(r["graft_length"] == "1" for r in rows),
            "two_aa_count": sum(r["graft_length"] == "2" for r in rows),
            "preflight_target_cell_hit": len(rows),
            "empty_cell_count": len(empty),
            "algorithm": "descriptor_exact_empty_cell_first",
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
