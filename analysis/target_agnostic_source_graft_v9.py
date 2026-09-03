"""Bounded, low-perturbation target-agnostic v9 proposal generator."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from pepagent.developability import sequence_developability_metrics

ROOT = Path(__file__).resolve().parents[1]
V8 = ROOT / "reports/target_agnostic_source_graft_v8_20260903/candidate_scores_calibrated.csv"
PEPFLOW = (
    ROOT
    / "reports/pepflow_acea_reciprocal_micrograft_scoreall_20260903/candidate_scores_calibrated.csv"
)
PEPGLAD = (
    ROOT
    / (
        "reports/pepflow_acea_cross_source_block_graft_scoreall_v3_20260903/"
        "candidate_scores_calibrated.csv"
    )
)
ARCHIVE = ROOT / "reports/target_agnostic_quality_combined_round10_20260826T2112.csv"
OUT = ROOT / "reports/target_agnostic_source_graft_v9_20260903"


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def descriptor(row: dict[str, str]) -> tuple[float, float, float, int]:
    metrics = sequence_developability_metrics(row["sequence"])
    return (
        float(metrics["net_charge_ph7_4"]),
        float(metrics["hydrophobic_fraction"]),
        float(metrics["hydrophobic_moment_eisenberg"]),
        len(row["sequence"]),
    )


def diagnostic(v8: list[dict[str, str]], archive: list[dict[str, str]]) -> dict[str, object]:
    by_id = {row.get("candidate_id", ""): row for row in archive}
    deltas: list[dict[str, object]] = []
    for row in v8:
        parent = by_id.get(row.get("parent_candidate_id", ""))
        if not parent:
            continue
        child = descriptor(row)
        base = descriptor(parent)
        deltas.append(
            {
                "arm": row["donor_source"],
                "delta": [child[i] - base[i] for i in range(4)],
                "activity_delta": {
                    key: float(row[key]) - float(parent[key])
                    for key in (
                        "llamp_log10_mic_um",
                        "amp_read_log10_mic_um",
                        "macrel_amp_probability",
                    )
                    if key in parent
                },
            }
        )
    return {"v8_count": len(v8), "matched_parent_count": len(deltas), "deltas": deltas}


def safe_parents(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row.get("display_eligible", "").lower() == "true"
        and int(float(row.get("activity_model_support_count_calibrated", 0))) >= 2
        and float(row.get("guruprasad_instability_index", 999)) <= 50
    ]


def child(
    parent: dict[str, str], position: int, residue: str, source: str, donor: str
) -> dict[str, str] | None:
    sequence = parent["sequence"].upper()
    if sequence[position] == residue:
        return None
    sequence = sequence[:position] + residue + sequence[position + 1 :]
    metrics = sequence_developability_metrics(sequence)
    payload = {
        "sequence": sequence,
        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
        "parent_candidate_id": parent.get("candidate_id", ""),
        "donor_candidate_id": donor,
        "donor_source": source,
        "donor_display_eligible": "true" if source == "PepFlow" else "false",
        "graft_start_zero_based": str(position),
        "graft_length": "1",
        "family_novelty_policy": "report_only",
        "preflight_descriptor": json.dumps(
            {
                "net_charge_ph7_4": metrics["net_charge_ph7_4"],
                "hydrophobic_ratio_modlamp": metrics["hydrophobic_fraction"],
                "hydrophobic_moment_eisenberg": metrics["hydrophobic_moment_eisenberg"],
                "length": len(sequence),
            },
            sort_keys=True,
        ),
    }
    return payload


def main() -> None:
    v8, pepflow, pepglad, archive = map(read, (V8, PEPFLOW, PEPGLAD, ARCHIVE))
    parents = safe_parents(pepflow)
    if len(parents) < 16:
        raise ValueError(f"only {len(parents)} eligible PepFlow parents")
    donors = pepglad[:12]
    rows: list[dict[str, str]] = []
    conservative = {
        "K": "R",
        "R": "K",
        "D": "E",
        "E": "D",
        "S": "T",
        "T": "S",
        "V": "I",
        "I": "V",
        "L": "I",
        "A": "G",
        "G": "A",
    }
    for arm, limit in (("PepFlow", 16), ("PepGLAD", 16)):
        for index, parent in enumerate(parents):
            position = (index * 3 + (0 if arm == "PepFlow" else 1)) % len(parent["sequence"])
            donor = (
                pepflow[index % len(pepflow)] if arm == "PepFlow" else donors[index % len(donors)]
            )
            residue = donor["sequence"][position % len(donor["sequence"])]
            residue = (
                residue
                if residue != parent["sequence"][position]
                else conservative.get(residue, "A")
            )
            item = child(parent, position, residue, arm, donor.get("candidate_id", ""))
            if item and item["sequence_sha256"] not in {row["sequence_sha256"] for row in rows}:
                rows.append(item)
            if sum(row["donor_source"] == arm for row in rows) >= limit:
                break
    OUT.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with (OUT / "proposals.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (OUT / "diagnostic.json").write_text(
        json.dumps(diagnostic(v8, archive), indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "generation_receipt.json").write_text(
        json.dumps(
            {
                "proposal_count": len(rows),
                "pepflow": sum(r["donor_source"] == "PepFlow" for r in rows),
                "pepglad": sum(r["donor_source"] == "PepGLAD" for r in rows),
                "parents": len(parents),
                "algorithm": "one_residue_low_perturbation_descriptor_report_only",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
