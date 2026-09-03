"""Bounded reciprocal PepFlow 1–2 aa micrografts onto active AceA backbones."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from pathlib import Path

from autoresearch_safety_rescue_variants import _historical_sequence_sha256s

from pepagent.autoresearch_quality_diversity import behavior_vector
from pepagent.developability import sequence_developability_metrics
from pepagent.handoff_metrics import physicochemical_descriptors


def rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def safe_active(row: dict[str, str]) -> bool:
    return (
        row.get("target_key") == "acea"
        and row.get("display_eligible") == "True"
        and row.get("formal_metrics_complete") == "True"
        and row.get("toxinpred3_label") == "Non-Toxin"
        and row.get("macrel_hemolysis_label") == "low"
        and float(row.get("activity_model_support_count") or 0) >= 2
        and 10 <= len(row.get("sequence", "")) <= 30
    )


def phi(sequence: str):
    sequence_developability_metrics(sequence)
    d = physicochemical_descriptors(sequence, ph=7.4)
    v = behavior_vector(
        sequence,
        net_charge=d["net_charge_ph7_4"],
        hydrophobicity=d["hydrophobic_ratio"],
        hydrophobic_moment=d["hydrophobic_moment"],
    )
    return [v.charge_density, v.hydrophobicity, v.hydrophobic_moment, v.length]


def generate(acceptors, pepflow, history, limit=32):
    fragments = []
    for parent in sorted(
        pepflow, key=lambda x: (x.get("family_key_80_80", ""), x.get("sequence", ""))
    ):
        seq = parent.get("sequence", "").upper()
        if not 10 <= len(seq) <= 30:
            continue
        for length in (1, 2):
            start = (len(seq) - length) // 2
            fragments.append((parent, start, seq[start : start + length]))
    proposals = []
    seen = set(history)
    for acceptor in sorted(acceptors, key=lambda x: (x.get("family_key_80_80", ""), x["sequence"])):
        seq = acceptor["sequence"].upper()
        for donor, donor_start, fragment in fragments:
            length = len(fragment)
            if len(seq) < length:
                continue
            # Conservative positions: termini-adjacent and central; no hydrophobic gate.
            positions = sorted({0, max(0, (len(seq) - length) // 2), len(seq) - length})
            for position in positions:
                child = seq[:position] + fragment + seq[position + length :]
                sha = hashlib.sha256(child.encode()).hexdigest()
                if child == seq or sha in seen:
                    continue
                before, after = phi(seq), phi(child)
                proposals.append(
                    {
                        "sequence": child,
                        "sequence_sha256": sha,
                        "branch_key": "acea",
                        "proposal_mode": "reciprocal-source-micrograft",
                        "parent_sequence": seq,
                        "parent_candidate_id": acceptor.get("candidate_id", ""),
                        "acceptor_family_key_80_80": acceptor.get("family_key_80_80", ""),
                        "donor_candidate_id": donor.get("candidate_id", ""),
                        "donor_source": donor.get("generator_id", "pepflow"),
                        "donor_sequence": donor["sequence"],
                        "donor_fragment": fragment,
                        "donor_start_zero_based": donor_start,
                        "acceptor_start_zero_based": position,
                        "micrograft_length": length,
                        "conservative_rule": "terminus_or_center_equal_length_substitution",
                        "delta_phi": json.dumps(
                            {
                                "axes": [
                                    "net_charge_over_length",
                                    "hydrophobic_ratio",
                                    "hydrophobic_moment",
                                    "length",
                                ],
                                "acceptor_to_child": [
                                    a - b for a, b in zip(after, before, strict=True)
                                ],
                            },
                            sort_keys=True,
                        ),
                        "historical_exact_replay": "passed_postgresql_exact_history_gate",
                    }
                )
                seen.add(sha)
                if len(proposals) >= limit:
                    return proposals
    return proposals


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--acceptor-csv", type=Path, required=True)
    p.add_argument("--pepflow-csv", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    acceptors = [r for r in rows(args.acceptor_csv) if safe_active(r)]
    pepflow = rows(args.pepflow_csv)
    history = asyncio.run(_historical_sequence_sha256s())
    proposals = generate(acceptors, pepflow, history)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "proposals.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(proposals[0]) if proposals else ["sequence"])
        w.writeheader()
        w.writerows(proposals)
    (args.output_dir / "selection_receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "ampgent.reciprocal-source-micrograft.1",
                "operator_id": "reciprocal-source-micrograft-v1",
                "acceptor_count": len(acceptors),
                "pepflow_source_count": len(pepflow),
                "proposal_count": len(proposals),
                "historical_sequence_exclusion_count": len(history),
                "max_total": 32,
                "gpu_rosetta_md_submitted": False,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
