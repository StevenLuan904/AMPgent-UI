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
OUT = ROOT / "reports/target_agnostic_source_graft_v13_20260903"
SOURCES = (
    ROOT / "reports/target_agnostic_source_graft_v10_20260903/proposals.csv",
    ROOT / "reports/target_agnostic_source_graft_v11_20260903/proposals.csv",
)
FAILED = {"2-aa_micrograft", "hemolysis-debias", "toxin-neutralize"}
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
prior = set()
donors: dict[str, list[dict[str, str]]] = {"PepFlow": [], "PepGLAD": []}
for path in SOURCES:
    for row in read(path):
        prior.add(row["sequence_sha256"])
        if row.get("donor_source") in donors:
            donors[row["donor_source"]].append(row)
prior.update(row["sequence_sha256"] for row in archive)

# Same-domain calibration witness: exact directions and p75 support, independent of target labels.
ordered = {
    key: sorted(float(row[key]) for row in archive)
    for key in ("amp_read_log10_mic_um", "llamp_log10_mic_um", "macrel_amp_probability")
}
def support(row: dict[str, str]) -> int:
    values = (
        (
            len(archive)
            - bisect.bisect_left(
                ordered["amp_read_log10_mic_um"],
                float(row["amp_read_log10_mic_um"]),
            )
        ) / len(archive),
        (
            len(archive)
            - bisect.bisect_left(
                ordered["llamp_log10_mic_um"], float(row["llamp_log10_mic_um"])
            )
        ) / len(archive),
        bisect.bisect_right(
            ordered["macrel_amp_probability"], float(row["macrel_amp_probability"])
        ) / len(archive),
    )
    return sum(value >= 0.75 for value in values)


parents = [
    row
    for row in archive
    if row.get("toxinpred3_label", "").lower() in {"non-toxin", "non_toxin", "non toxin"}
    and row.get("macrel_hemolysis_label", "").lower() == "low"
    and float(row.get("guruprasad_instability_index", "inf")) <= 50
    and support(row) >= 2
]
parents.sort(key=lambda row: (row.get("family_key_80_80", ""), row["sequence_sha256"]))
parents = parents[:31]

rows: list[dict[str, str]] = []
seen = set(prior)
for arm in ("PepFlow", "PepGLAD"):
    donors_for_arm = donors[arm]
    for index in range(16):
        parent = parents[(index * 2 + (arm == "PepGLAD")) % len(parents)]
        sequence = parent["sequence"]
        found = None
        for position, old in enumerate(sequence):
            new = SUBS.get(old)
            if not new:
                continue
            candidate = sequence[:position] + new + sequence[position + 1 :]
            if sha(candidate) in seen:
                continue
            found = (candidate, position, old, new)
            break
        if not found:
            continue
        candidate, position, old, new = found
        seen.add(sha(candidate))
        donor = donors_for_arm[index % len(donors_for_arm)]
        rows.append(
            {
                "sequence": candidate,
                "sequence_sha256": sha(candidate),
                "parent_candidate_id": parent["candidate_id"],
                "parent_sequence_sha256": parent["sequence_sha256"],
                "donor_candidate_id": donor.get("donor_candidate_id", ""),
                "donor_source": arm,
                "donor_display_eligible": donor.get("donor_display_eligible", ""),
                "graft_start_zero_based": str(position),
                "graft_length": "1",
                "from_residue": old,
                "to_residue": new,
                "position_fraction": f"{position / len(sequence):.6f}",
                "operator_rule": "conservative_1aa_same_cell_first",
                "target_cell": cell(candidate),
                "parent_cell": cell(sequence),
                "branch_key": "target_agnostic_amp",
                "generation": "13",
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
            "schema_version": "ampgent.target-agnostic-v13-generation.1",
            "proposal_count": len(rows),
            "source_counts": {
                arm: sum(row["donor_source"] == arm for row in rows) for arm in donors
            },
            "parent_count": len(parents),
            "operator_rule": "conservative_1aa_same_cell_first",
            "excluded_rules": sorted(FAILED),
            "hydrophobicity_hard_gate": False,
            "weighted_score": False,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
