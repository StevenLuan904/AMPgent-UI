from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/target_agnostic_source_graft_v12_20260903"
FILES = [
    ROOT / "reports/target_agnostic_source_graft_v10_20260903/candidate_scores_calibrated.csv",
    ROOT / "reports/target_agnostic_source_graft_v11_20260903/candidate_scores_calibrated.csv",
]


def read(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def failure(row):
    result = []
    if row.get("toxinpred3_label", "").lower() != "non-toxin":
        result.append("toxinpred3")
    if (
        row.get("macrel_hemolysis_label", "").lower() != "low"
        or float(row.get("macrel_hemolysis_probability", 0)) > 0.5
    ):
        result.append("macrel_hemolysis")
    try:
        if float(row.get("guruprasad_instability_index", "inf")) > 50:
            result.append("guruprasad_gt50")
    except ValueError:
        result.append("guruprasad_nonfinite")
    return result


diagnostic = []
proposals = []
seen = set()
for path in FILES:
    for row in read(path):
        if (
            int(row.get("activity_model_support_count_calibrated", 0)) < 2
            or row.get("display_eligible", "").lower() == "true"
        ):
            continue
        gates = failure(row)
        diagnostic.append(
            {
                "round": path.parent.name,
                "source": row.get("donor_source", "unknown"),
                "sequence_sha256": row["sequence_sha256"],
                "failure_combo": "+".join(gates),
            }
        )
        sequence = row["sequence"]
        child = sequence
        edit = ""
        if "toxinpred3" in gates:
            for index, residue in enumerate(child):
                if residue in "KR":
                    child = child[:index] + "D" + child[index + 1 :]
                    edit = f"toxin-neutralize:{index}:{residue}>D"
                    break
        elif "macrel_hemolysis" in gates:
            for index, residue in enumerate(child):
                if residue in "AFILMVWY":
                    child = child[:index] + "S" + child[index + 1 :]
                    edit = f"hemolysis-debias:{index}:{residue}>S"
                    break
        elif gates:
            child = "S" + child[1:]
            edit = "stability-rescue:0> S"
        digest = hashlib.sha256(child.encode()).hexdigest()
        if child == sequence or digest in seen:
            continue
        seen.add(digest)
        proposals.append(
            {
                "sequence": child,
                "sequence_sha256": digest,
                "parent_candidate_id": row.get("candidate_id", ""),
                "parent_sequence_sha256": row["sequence_sha256"],
                "donor_source": row.get("donor_source", "unknown"),
                "failure_combo": "+".join(gates),
                "rescue_edit": edit,
                "branch_key": "target_agnostic_amp",
                "generation": "12",
            }
        )
        if len(proposals) >= 32:
            break
    if len(proposals) >= 32:
        break
OUT.mkdir(parents=True, exist_ok=True)
with (OUT / "failure_diagnostic.csv").open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(
        stream, fieldnames=["round", "source", "sequence_sha256", "failure_combo"]
    )
    writer.writeheader()
    writer.writerows(diagnostic)
with (OUT / "proposals.csv").open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(proposals[0]))
    writer.writeheader()
    writer.writerows(proposals)
(OUT / "generation_receipt.json").write_text(
    json.dumps(
        {
            "input_high_activity_display_false": len(diagnostic),
            "proposal_count": len(proposals),
            "limit": 32,
            "operator": "failure_gate_targeted_safety_stability_rescue",
            "hard_gates_unchanged": True,
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
