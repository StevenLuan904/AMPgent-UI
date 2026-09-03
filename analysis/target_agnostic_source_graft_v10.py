from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from pepagent.developability import sequence_developability_metrics

ROOT = Path(__file__).resolve().parents[1]
WITNESS = ROOT / "reports/target_agnostic_v10_witness_20260903/parents.csv"
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
OUT = ROOT / "reports/target_agnostic_source_graft_v10_20260903"


def read(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


parents, pepflow, pepglad = read(WITNESS), read(PF), read(PG)
rows = []
for arm, donors in (("PepFlow", pepflow), ("PepGLAD", pepglad)):
    for index, parent in enumerate(parents[:16]):
        sequence = parent["sequence"]
        position = (index * 5 + (0 if arm == "PepFlow" else 1)) % len(sequence)
        donor = donors[index % len(donors)]
        residue = donor["sequence"][position % len(donor["sequence"])]
        if residue == sequence[position]:
            residue = {"K": "R", "R": "K", "D": "E", "E": "D", "S": "T", "T": "S"}.get(residue, "A")
        child = sequence[:position] + residue + sequence[position + 1 :]
        metrics = sequence_developability_metrics(child)
        rows.append(
            {
                "sequence": child,
                "sequence_sha256": hashlib.sha256(child.encode()).hexdigest(),
                "parent_candidate_id": parent["candidate_id"],
                "parent_sequence_sha256": parent["sequence_sha256"],
                "donor_candidate_id": donor.get("candidate_id", ""),
                "donor_source": arm,
                "donor_display_eligible": "true" if arm == "PepFlow" else "false",
                "graft_start_zero_based": str(position),
                "graft_length": "1",
                "branch_key": "target_agnostic_amp",
                "generation": "10",
                "family_novelty": "report_only",
                "preflight_net_charge_ph7_4": str(metrics["net_charge_ph7_4"]),
                "preflight_hydrophobic_fraction": str(metrics["hydrophobic_fraction"]),
                "preflight_hydrophobic_moment": str(metrics["hydrophobic_moment_eisenberg"]),
                "preflight_length": str(len(child)),
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
            "PepFlow": 16,
            "PepGLAD": 16,
            "parent_count": 16,
            "control_policy": "reuse_existing_authoritative_pg_identity",
            "target_key": "target_agnostic_amp",
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
