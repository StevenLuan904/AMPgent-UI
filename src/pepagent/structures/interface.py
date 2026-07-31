from __future__ import annotations

from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np

from pepagent.structures.pdb import peptide_backbone_rmsd_after_receptor_alignment


def _cross_chain_atoms(path: Path) -> tuple[dict[int, np.ndarray], np.ndarray]:
    receptor: dict[int, list[list[float]]] = {}
    peptide: list[list[float]] = []
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if not line.startswith("ATOM") or len(line) < 54:
            continue
        chain = line[21:22]
        if chain not in {"A", "B"} or line[16:17] not in {" ", "A"}:
            continue
        atom = line[12:16].strip()
        if atom.startswith("H"):
            continue
        coordinates = [
            float(line[30:38]),
            float(line[38:46]),
            float(line[46:54]),
        ]
        if chain == "A":
            residue = int(line[22:26])
            receptor.setdefault(residue, []).append(coordinates)
        else:
            peptide.append(coordinates)
    if not receptor or not peptide:
        raise ValueError("interface audit requires receptor chain A and peptide chain B")
    return (
        {residue: np.asarray(atoms, dtype=float) for residue, atoms in receptor.items()},
        np.asarray(peptide, dtype=float),
    )


def audit_protein_peptide_interface(
    path: Path,
    pocket_residues: list[int],
    contact_distance: float = 5.0,
    clash_distance: float = 1.5,
) -> dict[str, Any]:
    receptor, peptide = _cross_chain_atoms(path)
    contacted: list[int] = []
    minimum = float("inf")
    clash_count = 0
    for residue, atoms in receptor.items():
        distances = np.linalg.norm(atoms[:, None, :] - peptide[None, :, :], axis=2)
        residue_minimum = float(distances.min())
        minimum = min(minimum, residue_minimum)
        if residue_minimum <= contact_distance:
            contacted.append(residue)
        clash_count += int(np.count_nonzero(distances < clash_distance))
    pocket = set(pocket_residues)
    pocket_contacts = sorted(pocket.intersection(contacted))
    off_pocket_contacts = sorted(set(contacted) - pocket)
    total_contacts = len(contacted)
    return {
        "contact_distance_angstrom": contact_distance,
        "clash_distance_angstrom": clash_distance,
        "minimum_interface_distance_angstrom": minimum,
        "contacted_receptor_residues": sorted(contacted),
        "pocket_contacted_residues": pocket_contacts,
        "off_pocket_contacted_residues": off_pocket_contacts,
        "pocket_contact_count": len(pocket_contacts),
        "pocket_coverage_fraction": (len(pocket_contacts) / len(pocket) if pocket else 0.0),
        "off_pocket_contact_fraction": (
            len(off_pocket_contacts) / total_contacts if total_contacts else 0.0
        ),
        "cross_chain_clash_count": clash_count,
    }


def pose_cluster_fraction(paths: list[Path], threshold_angstrom: float) -> dict[str, Any]:
    if not paths:
        raise ValueError("at least one pose is required")
    if len(paths) == 1:
        return {
            "largest_cluster_fraction": 1.0,
            "pairwise_rmsd_angstrom": [],
            "threshold_angstrom": threshold_angstrom,
        }
    pairwise: list[dict[str, Any]] = []
    neighbors = [1] * len(paths)
    for (first_index, first), (second_index, second) in combinations(enumerate(paths), 2):
        rmsd = peptide_backbone_rmsd_after_receptor_alignment(first, second, ["A"], "B")
        pairwise.append({"first": first_index, "second": second_index, "rmsd_angstrom": rmsd})
        if rmsd <= threshold_angstrom:
            neighbors[first_index] += 1
            neighbors[second_index] += 1
    return {
        "largest_cluster_fraction": max(neighbors) / len(paths),
        "pairwise_rmsd_angstrom": pairwise,
        "threshold_angstrom": threshold_angstrom,
    }
