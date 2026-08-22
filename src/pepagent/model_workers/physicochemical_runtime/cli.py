from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
from typing import Any

import modlamp
from Bio.SeqUtils.ProtParam import ProteinAnalysis
from modlamp.descriptors import GlobalDescriptor, PeptideDescriptor

RUNTIME_ID = "physicochemical-developability-modlamp-4.3.2-biopython-v39"
METHOD_VERSION = "2026.08.22-v2"

OUTPUTS: dict[str, tuple[str, str]] = {
    "net_charge_ph7_4": ("net_charge_ph7_4", "elementary_charge"),
    "hydrophobic_ratio_modlamp": ("hydrophobic_ratio", "fraction"),
    "hydrophobic_moment_eisenberg": ("hydrophobic_moment", "dimensionless"),
    "maximum_hydrophobic_run": ("maximum_hydrophobic_run", "residues"),
    "guruprasad_instability_index": (
        "guruprasad_instability_index",
        "dimensionless",
    ),
}

HYDROPHOBIC_RESIDUES = frozenset("AVILMFWY")


def _maximum_hydrophobic_run(sequence: str) -> int:
    maximum = 0
    current = 0
    for residue in sequence:
        if residue in HYDROPHOBIC_RESIDUES:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def _scalar(descriptor: object) -> float:
    return float(descriptor.descriptor[0][0])


def describe(
    sequence: str,
    *,
    ph: float,
    c_terminal_amidated: bool,
    hydrophobic_moment_angle: int,
) -> dict[str, Any]:
    normalized_sequence = sequence.strip().upper()
    if not normalized_sequence:
        raise ValueError("sequence must not be empty")
    invalid_residues = sorted(set(normalized_sequence) - set("ACDEFGHIKLMNPQRSTVWY"))
    if invalid_residues:
        raise ValueError(
            "sequence contains non-canonical residues: " + ", ".join(invalid_residues)
        )
    sequence = normalized_sequence
    global_descriptor = GlobalDescriptor(sequence)
    global_descriptor.calculate_MW(amide=c_terminal_amidated)
    molecular_weight = _scalar(global_descriptor)
    global_descriptor.calculate_charge(ph=ph, amide=c_terminal_amidated)
    net_charge = _scalar(global_descriptor)
    global_descriptor.isoelectric_point(amide=c_terminal_amidated)
    isoelectric_point = _scalar(global_descriptor)
    global_descriptor.hydrophobic_ratio()
    hydrophobic_ratio = _scalar(global_descriptor)

    moment_descriptor = PeptideDescriptor(sequence, "eisenberg")
    moment_descriptor.calculate_moment(
        window=1000,
        angle=hydrophobic_moment_angle,
        modality="max",
    )
    instability_index = float(ProteinAnalysis(sequence).instability_index())
    instability_out_of_domain = len(sequence) < 20
    return {
        "molecular_weight": molecular_weight,
        "net_charge_ph7_4": net_charge,
        "isoelectric_point": isoelectric_point,
        "hydrophobic_ratio": hydrophobic_ratio,
        "hydrophobic_moment": _scalar(moment_descriptor),
        "maximum_hydrophobic_run": _maximum_hydrophobic_run(sequence),
        "guruprasad_instability_index": instability_index,
        "guruprasad_instability_interpretation": (
            "protein_reference_stable_proxy"
            if instability_index <= 40.0
            else "protein_reference_unstable_proxy"
        ),
        "guruprasad_instability_out_of_domain": instability_out_of_domain,
        "assumptions": {
            "ph": ph,
            "termini": (
                "free N-terminus and amidated C-terminus"
                if c_terminal_amidated
                else "free N-terminus and free C-terminus"
            ),
            "hydrophobicity_scale": "Eisenberg consensus",
            "hydrophobic_moment_angle_degrees": hydrophobic_moment_angle,
            "hydrophobic_moment_window": 1000,
            "modlamp_distribution_version": importlib.metadata.version("modlamp"),
            "modlamp_runtime_version": modlamp.__version__,
            "biopython_distribution_version": importlib.metadata.version("biopython"),
            "instability_method": "Guruprasad-Reddy-Pandit dipeptide instability index",
            "instability_reference_doi": "10.1093/protein/4.2.155",
            "instability_protein_reference_boundary": 40.0,
            "instability_short_peptide_ood_below_residues": 20,
        },
        "limitations": [
            "Transparent sequence descriptors; not experimental safety, stability, or potency.",
            "The 100-degree hydrophobic moment is an idealized alpha-helical projection and "
            "does not assert that the peptide forms an alpha helix.",
            "Canonical sequence inference does not represent terminal capping, cyclization, "
            "D-residues, or other chemical modifications.",
            "The Guruprasad index was derived from proteins, not short antimicrobial peptides. "
            "For sequences shorter than 20 residues it is explicitly marked out-of-domain.",
            "The historical value 40 is retained only as a protein-reference interpretation; "
            "it is not a peptide rejection gate and does not predict serum, protease, or "
            "shelf stability.",
        ],
        "method_version": METHOD_VERSION,
    }


def evaluate(request: dict[str, Any]) -> dict[str, Any]:
    plugin = request["plugin"]
    if plugin["name"] != "physicochemical_developability":
        raise ValueError("runtime only accepts physicochemical_developability")
    parameters = plugin.get("parameters", {})
    ph = float(parameters.get("ph", 7.4))
    amidated = bool(parameters.get("c_terminal_amidated", False))
    angle = int(parameters.get("hydrophobic_moment_angle", 100))
    rows: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for candidate in request["candidates"]:
        row = {
            "candidate_id": str(candidate["id"]),
            "sequence": str(candidate["sequence"]),
            "status": "complete",
            **describe(
                str(candidate["sequence"]),
                ph=ph,
                c_terminal_amidated=amidated,
                hydrophobic_moment_angle=angle,
            ),
        }
        rows.append(row)
        records.append(
            {
                "candidate_id": row["candidate_id"],
                "sequence": row["sequence"],
                "status": "complete",
                "observations": [
                    {
                        "metric_name": metric_name,
                        "numeric_value": float(row[source_field]),
                        "text_value": None,
                        "unit": unit,
                    }
                    for metric_name, (source_field, unit) in OUTPUTS.items()
                ],
                "raw": row,
            }
        )
    return {
        "plugin": plugin,
        "contract": {
            "default_trust": "descriptor",
            "maximum_trust": "descriptor",
            "reliability": "descriptor-R2",
            "provider": "builtin",
            "outputs": {
                key: [source_field, unit, "numeric"]
                for key, (source_field, unit) in OUTPUTS.items()
            },
        },
        "candidate_count": len(rows),
        "status": "complete",
        "adapter_version": METHOD_VERSION,
        "runtime_id": RUNTIME_ID,
        "records": records,
        "raw_rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    result = evaluate(request)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "plugin": request["plugin"]["name"],
                "runtime_id": RUNTIME_ID,
                "status": result["status"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
