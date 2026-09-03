from __future__ import annotations

import bisect
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
ARCHIVE = ROOT / "reports/target_agnostic_quality_combined_round10_20260826T2112.csv"
OUT = ROOT / "reports/target_agnostic_source_graft_v14_20260903"
OUTCOMES = ROOT / "reports/target_agnostic_v13_20260903/operator_outcomes.csv"
SOURCES = (
    ROOT / "reports/target_agnostic_source_graft_v13_20260903/proposals.csv",
    ROOT / "reports/target_agnostic_source_graft_v11_20260903/proposals.csv",
)
SUBS = {
    "K": "R", "R": "K", "D": "E", "E": "D", "S": "T", "T": "S",
    "V": "I", "I": "V", "L": "I", "A": "G", "G": "A",
}


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def sha(sequence: str) -> str:
    return hashlib.sha256(sequence.encode()).hexdigest()


def cell(sequence: str) -> str:
    metrics = sequence_developability_metrics(sequence)
    vector = behavior_vector(
        sequence,
        net_charge=float(metrics["net_charge_ph7_4"]),
        hydrophobicity=float(metrics["hydrophobic_fraction"]),
        hydrophobic_moment=float(metrics["hydrophobic_moment_eisenberg"]),
    )
    return behavior_cell_id(vector, BehaviorSpacePolicy())


archive = read(ARCHIVE)
historical = {row["sequence_sha256"] for row in archive}
tried: set[tuple[str, str, str, str]] = set()
for row in read(OUTCOMES):
    if row["operator"] == "1-aa_micrograft":
        tried.add(
            (
                row["parent_sequence_sha256"], row["position_fraction"],
                row["from_residue"], row["to_residue"],
            )
        )
for path in SOURCES:
    for row in read(path):
        historical.add(row["sequence_sha256"])

keys = ("amp_read_log10_mic_um", "llamp_log10_mic_um", "macrel_amp_probability")
ordered = {key: sorted(float(row[key]) for row in archive) for key in keys}


def support(row: dict[str, str]) -> int:
    ranks = (
        (len(archive) - bisect.bisect_left(ordered[keys[0]], float(row[keys[0]]))) / len(archive),
        (len(archive) - bisect.bisect_left(ordered[keys[1]], float(row[keys[1]]))) / len(archive),
        bisect.bisect_right(ordered[keys[2]], float(row[keys[2]])) / len(archive),
    )
    return sum(rank >= 0.75 for rank in ranks)


parents = [
    row for row in archive
    if row.get("toxinpred3_label", "").lower() in {"non-toxin", "non_toxin", "non toxin"}
    and row.get("macrel_hemolysis_label", "").lower() == "low"
    and float(row.get("guruprasad_instability_index", "inf")) <= 50
    and support(row) >= 2
]
parents.sort(key=lambda row: (row.get("family_key_80_80", ""), row["sequence_sha256"]))
donors = {"PepFlow": [], "PepGLAD": []}
for row in read(SOURCES[0]):
    if row.get("donor_source") in donors:
        donors[row["donor_source"]].append(row)
rows: list[dict[str, str]] = []
seen = set(historical)
for arm in ("PepFlow", "PepGLAD"):
    for index in range(16):
        parent = parents[(index * 2 + (arm == "PepGLAD")) % len(parents)]
        sequence = parent["sequence"]
        found = None
        for position, old in enumerate(sequence):
            new = SUBS.get(old)
            if not new:
                continue
            fraction = f"{position / len(sequence):.6f}"
            if (parent["sequence_sha256"], fraction, old, new) in tried:
                continue
            candidate = sequence[:position] + new + sequence[position + 1 :]
            if sha(candidate) in seen:
                continue
            found = candidate, position, old, new
            break
        if not found:
            continue
        candidate, position, old, new = found
        seen.add(sha(candidate))
        donor = donors[arm][index % len(donors[arm])]
        rows.append({
            "sequence": candidate, "sequence_sha256": sha(candidate),
            "parent_candidate_id": parent["candidate_id"],
            "parent_sequence_sha256": parent["sequence_sha256"],
            "donor_candidate_id": donor.get("donor_candidate_id", ""),
            "donor_source": arm, "donor_display_eligible": donor.get("donor_display_eligible", ""),
            "graft_start_zero_based": str(position), "graft_length": "1",
            "from_residue": old, "to_residue": new,
            "position_fraction": f"{position / len(sequence):.6f}",
            "operator_rule": "validated_1aa_same_cell_or_quality_improvement",
            "target_cell": cell(candidate), "parent_cell": cell(sequence),
            "branch_key": "target_agnostic_amp", "generation": "14",
        })
OUT.mkdir(parents=True, exist_ok=True)
with (OUT / "proposals.csv").open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
(OUT / "generation_receipt.json").write_text(json.dumps({
    "schema_version": "ampgent.target-agnostic-v14-generation.1",
    "proposal_count": len(rows),
    "source_counts": {
        arm: sum(row["donor_source"] == arm for row in rows) for arm in donors
    },
    "parent_count": len(parents), "operator_rule": "validated_1aa_same_cell_or_quality_improvement",
    "excluded_rules": ["2-aa_micrograft", "hemolysis-debias", "toxin-neutralize"],
    "hydrophobicity_hard_gate": False, "weighted_score": False,
}, indent=2) + "\n", encoding="utf-8")
