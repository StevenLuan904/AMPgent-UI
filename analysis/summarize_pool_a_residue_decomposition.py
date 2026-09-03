"""Summarize peptide-residue MM/GBSA decomposition for completed Pool-A MD."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path

AA3_TO_AA1 = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HID": "H",
    "HIE": "H",
    "HIP": "H",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "PHE": "F",
    "PRO": "P",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
}
ENERGY_COLUMNS = {
    "mean_Internal": "internal_kcal_mol",
    "mean_van der Waals": "van_der_waals_kcal_mol",
    "mean_Electrostatic": "electrostatic_kcal_mol",
    "mean_Polar Solvation": "polar_solvation_kcal_mol",
    "mean_Non-Polar Solv.": "nonpolar_solvation_kcal_mol",
    "mean_TOTAL": "total_kcal_mol",
}


def residue_identity(label: str) -> tuple[str, int]:
    match = re.fullmatch(r"\s*([A-Za-z0-9]+)\s+(\d+)\s*", label)
    if match is None:
        raise ValueError(f"malformed residue label: {label!r}")
    return match.group(1).upper(), int(match.group(2))


def finite(value: str, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"non-finite {label}")
    return result


def candidate_decomposition(candidate: dict, evidence_root: Path) -> dict | None:
    if candidate["mmgbsa_complete"] != "True":
        return None
    if candidate["mmgbsa_postgresql_ingested"] != "True":
        return None
    root = evidence_root / candidate["target_key"] / candidate["candidate_id"]
    analysis = json.loads(
        (root / "analysis/mmgbsa/mmgbsa_analysis.json").read_text(encoding="utf-8")
    )
    start, end = [int(value) for value in analysis["peptide_residue_range"]]
    sequence = candidate["sequence"]
    if end - start + 1 != len(sequence):
        raise ValueError(f"peptide range/sequence mismatch for {candidate['candidate_id']}")
    rows = []
    with (root / "analysis/mmgbsa/residue_decomposition_mean.csv").open(
        newline="", encoding="utf-8"
    ) as stream:
        for source in csv.DictReader(stream):
            residue_name, global_index = residue_identity(source["residue"])
            if not start <= global_index <= end:
                continue
            position = global_index - start + 1
            expected_aa = sequence[position - 1]
            observed_aa = AA3_TO_AA1.get(residue_name)
            if observed_aa != expected_aa:
                raise ValueError(
                    f"peptide residue identity mismatch for {candidate['candidate_id']} "
                    f"at position {position}: {residue_name} != {expected_aa}"
                )
            row = {
                "target_key": candidate["target_key"],
                "run_id": candidate["run_id"],
                "candidate_id": candidate["candidate_id"],
                "sequence": sequence,
                "sequence_sha256": candidate["sequence_sha256"],
                "peptide_position": position,
                "amino_acid": expected_aa,
                "global_residue_index": global_index,
            }
            for source_key, output_key in ENERGY_COLUMNS.items():
                row[output_key] = finite(
                    source[source_key], f"{candidate['candidate_id']} {source_key}"
                )
            rows.append(row)
    if len(rows) != len(sequence):
        raise ValueError(f"missing peptide decomposition rows for {candidate['candidate_id']}")
    rows.sort(key=lambda row: row["peptide_position"])
    favorable = sorted(rows, key=lambda row: row["total_kcal_mol"])[:5]
    unfavorable = sorted(rows, key=lambda row: row["total_kcal_mol"], reverse=True)[:5]
    return {
        "target_key": candidate["target_key"],
        "run_id": candidate["run_id"],
        "candidate_id": candidate["candidate_id"],
        "sequence": sequence,
        "sequence_sha256": candidate["sequence_sha256"],
        "peptide_residue_count": len(rows),
        "peptide_total_decomposition_kcal_mol": sum(
            row["total_kcal_mol"] for row in rows
        ),
        "top_favorable_residues": favorable,
        "top_unfavorable_residues": unfavorable,
        "residues": rows,
    }


def summarize(candidates: list[dict], evidence_root: Path) -> dict:
    complete = []
    for candidate in candidates:
        result = candidate_decomposition(candidate, evidence_root)
        if result is not None:
            complete.append(result)
    return {
        "schema_version": "ampgent.pool-a-peptide-residue-decomposition.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "pool_a_candidate_count": len(candidates),
        "decomposition_complete_count": len(complete),
        "decomposition_pending_count": len(candidates) - len(complete),
        "lower_total_energy_interpreted_as_more_favorable": True,
        "candidates": complete,
        "limitations": [
            "computed MM/GBSA decomposition; not experimental residue energetics",
            "per-residue terms are interpretation aids and are not independent mutations",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--residue-csv", type=Path, required=True)
    args = parser.parse_args()
    with args.candidates.open(newline="", encoding="utf-8") as stream:
        candidates = list(csv.DictReader(stream))
    payload = summarize(candidates, args.evidence_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    residues = [row for item in payload["candidates"] for row in item["residues"]]
    args.residue_csv.parent.mkdir(parents=True, exist_ok=True)
    temporary_csv = args.residue_csv.with_name(
        f".{args.residue_csv.name}.{os.getpid()}.tmp"
    )
    with temporary_csv.open("w", newline="", encoding="utf-8") as stream:
        fields = list(residues[0]) if residues else ["candidate_id"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(residues)
    temporary_csv.replace(args.residue_csv)


if __name__ == "__main__":
    main()
