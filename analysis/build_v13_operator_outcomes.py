from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/target_agnostic_v13_20260903"
ROUNDS = ["v10", "v11", "v12", "v13", "v14"]


def read(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def sequence_hash(sequence: str) -> str:
    return hashlib.sha256(sequence.strip().upper().encode()).hexdigest()


outcomes = []
archive_path = ROOT / "reports/target_agnostic_quality_combined_round10_20260826T2112.csv"
archive_rows = {row["sequence_sha256"]: row for row in read(archive_path)}
for label in ROUNDS:
    base = ROOT / f"reports/target_agnostic_source_graft_{label}_20260903"
    if label in {"v13", "v14"}:
        base = ROOT / f"reports/target_agnostic_source_graft_{label}_20260903"
    proposals = {row["sequence_sha256"]: row for row in read(base / "proposals.csv")}
    scores = read(base / "candidate_scores_calibrated.csv")
    qd = json.loads((base / "quality_diversity.json").read_text())
    contribution = {row["candidate_id"]: row["contribution"] for row in qd["contributions"]}
    for row in scores:
        proposal = proposals.get(row["sequence_sha256"], {})
        parent = proposal.get("parent_sequence_sha256", "")
        parent_sequence = ""
        archive_path = ROOT / "reports/target_agnostic_quality_combined_round10_20260826T2112.csv"
        parent_row = archive_rows.get(parent, {})
        parent_sequence = parent_row.get("sequence", "")
        delta_phi = {}
        phi_keys = (
            "net_charge_ph7_4", "hydrophobic_ratio_modlamp", "hydrophobic_moment_eisenberg"
        )
        for key in phi_keys:
            if parent_row.get(key) and row.get(key):
                delta_phi[key] = float(row[key]) - float(parent_row[key])
        if parent_sequence and row.get("sequence"):
            delta_phi["length"] = len(row["sequence"]) - len(parent_sequence)
        position = proposal.get("graft_start_zero_based", "")
        position_fraction = ""
        from_residue = ""
        to_residue = ""
        if position.isdigit() and parent_sequence:
            index = int(position)
            position_fraction = f"{index / len(parent_sequence):.6f}"
            from_residue = parent_sequence[index]
            to_residue = row["sequence"][index]
        outcomes.append(
            {
                "round": label,
                "sequence_sha256": row["sequence_sha256"],
                "parent_sequence_sha256": parent,
                "source": proposal.get("donor_source", row.get("donor_source", "")),
                "operator": proposal.get(
                    "rescue_edit", f"{proposal.get('graft_length', '1')}-aa_micrograft"
                ),
                "position_fraction": position_fraction,
                "from_residue": from_residue,
                "to_residue": to_residue,
                "delta_phi": json.dumps(delta_phi, separators=(",", ":")),
                "display": row.get("display_eligible", ""),
                "support": row.get("activity_model_support_count_calibrated", ""),
                "qd_contribution": contribution.get(row["sequence_sha256"], "not_in_qd"),
            }
        )
OUT.mkdir(parents=True, exist_ok=True)
with (OUT / "operator_outcomes.csv").open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(outcomes[0]))
    writer.writeheader()
    writer.writerows(outcomes)
summary = {}
for row in outcomes:
    key = row["operator"]
    item = summary.setdefault(key, {"input": 0, "display": 0, "support_ge_2": 0, "effective_qd": 0})
    item["input"] += 1
    item["display"] += row["display"].lower() == "true"
    item["support_ge_2"] += int(row["support"] or 0) >= 2
    item["effective_qd"] += row["qd_contribution"] in {"empty_cell", "incumbent_replacement"}
(OUT / "operator_outcome_summary.json").write_text(
    json.dumps(summary, indent=2) + "\n", encoding="utf-8"
)
