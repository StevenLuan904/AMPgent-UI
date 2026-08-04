from __future__ import annotations

from typing import Any

import Bio
from Bio.SeqUtils.ProtParam import ProteinAnalysis

from pepagent.handoff_metrics import physicochemical_descriptors

CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
HYDROPHOBIC_RESIDUES = frozenset("AVILMFWY")
SEQUENCE_DEVELOPABILITY_VERSION = "3.0"
INSTABILITY_METHOD = "Guruprasad-Reddy-Pandit-1990-via-Biopython-ProtParam"
AMP_DESCRIPTOR_METHOD = (
    "Biopython-ProtParam-1.85-and-modlAMP-4.3.2-Eisenberg-alpha-helix"
)


def sequence_developability_metrics(sequence: str) -> dict[str, Any]:
    """Return transparent sequence flags; these are not experimental stability claims."""
    normalized = "".join(sequence.split()).upper()
    if not normalized:
        raise ValueError("peptide sequence cannot be empty")
    invalid = sorted(set(normalized) - CANONICAL_AMINO_ACIDS)
    if invalid:
        raise ValueError(f"peptide contains non-canonical amino acids: {''.join(invalid)}")

    maximum_run = 0
    current_run = 0
    maximum_identical_run = 0
    current_identical_run = 0
    previous_residue: str | None = None
    hydrophobic_count = 0
    for residue in normalized:
        current_identical_run = (
            current_identical_run + 1 if residue == previous_residue else 1
        )
        maximum_identical_run = max(maximum_identical_run, current_identical_run)
        previous_residue = residue
        if residue in HYDROPHOBIC_RESIDUES:
            hydrophobic_count += 1
            current_run += 1
            maximum_run = max(maximum_run, current_run)
        else:
            current_run = 0

    protein_analysis = ProteinAnalysis(normalized)
    physicochemical = physicochemical_descriptors(
        normalized,
        ph=7.4,
        c_terminal_amidated=False,
        hydrophobic_moment_angle=100,
    )
    return {
        "instability_index": float(protein_analysis.instability_index()),
        "hydrophobic_fraction": hydrophobic_count / len(normalized),
        "maximum_hydrophobic_run": maximum_run,
        "maximum_identical_residue_run": maximum_identical_run,
        "molecular_weight_da": float(protein_analysis.molecular_weight()),
        "net_charge_ph7_4": float(protein_analysis.charge_at_pH(7.4)),
        "isoelectric_point": float(protein_analysis.isoelectric_point()),
        "gravy": float(protein_analysis.gravy()),
        "hydrophobic_moment_eisenberg": float(
            physicochemical["hydrophobic_moment"]
        ),
        "cationic_residue_fraction": (
            normalized.count("K") + normalized.count("R")
        )
        / len(normalized),
        "hydrophobic_residue_count": hydrophobic_count,
        "sequence_length": len(normalized),
        "hydrophobic_alphabet": "".join(sorted(HYDROPHOBIC_RESIDUES)),
        "instability_method": INSTABILITY_METHOD,
        "amp_descriptor_method": AMP_DESCRIPTOR_METHOD,
        "descriptor_assumptions": physicochemical["assumptions"],
        "biopython_version": Bio.__version__,
        "method_version": SEQUENCE_DEVELOPABILITY_VERSION,
        "limitations": [
            "Early sequence-only developability screen; not a solubility or aggregation assay.",
            "The instability index is a dipeptide-composition proxy for protein in-vivo stability; "
            "it is not a measured peptide degradation half-life or chemical-stability assay.",
            "The conventional index threshold of 40 was not calibrated specifically for short "
            "therapeutic peptides and must be reported with this limitation.",
            "GRAVY, charge, pI, molecular weight, and cationic fraction are deterministic "
            "descriptors, not predictions of antimicrobial activity.",
            "The Eisenberg hydrophobic moment is an idealized 100-degree alpha-helical "
            "projection and does not assert that the peptide forms an alpha helix.",
        ],
    }
