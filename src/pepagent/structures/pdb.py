from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np

BACKBONE_ATOMS = frozenset({"N", "CA", "C", "O"})


def prepare_protein_peptide_pdb(
    source: Path,
    destination: Path,
    receptor_chains: list[str],
    peptide_chain: str,
) -> dict[str, int]:
    """Write one canonical ATOM-only model with receptor chains before the peptide."""
    requested = [*receptor_chains, peptide_chain]
    if len(set(requested)) != len(requested):
        raise ValueError("receptor and peptide chain identifiers must be distinct")
    if any(len(chain) != 1 for chain in requested):
        raise ValueError("PDB preparation currently requires one-character chain identifiers")

    by_chain: dict[str, list[str]] = defaultdict(list)
    in_first_model = True
    saw_model = False
    for line in source.read_text(encoding="ascii", errors="replace").splitlines():
        record = line[:6].strip()
        if record == "MODEL":
            if saw_model:
                in_first_model = False
            else:
                saw_model = True
            continue
        if record == "ENDMDL":
            if saw_model:
                break
            continue
        if not in_first_model or record != "ATOM" or len(line) < 54:
            continue
        chain = line[21:22]
        if chain not in requested:
            continue
        altloc = line[16:17]
        if altloc not in {" ", "A"}:
            continue
        by_chain[chain].append(f"{line[:16]} {line[17:]}")

    missing = [chain for chain in requested if not by_chain[chain]]
    if missing:
        raise ValueError(f"requested chains contain no ATOM records: {missing}")

    output: list[str] = []
    for chain in requested:
        output.extend(by_chain[chain])
        output.append("TER")
    output.append("END")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(output) + "\n", encoding="ascii")
    return {chain: len(by_chain[chain]) for chain in requested}


def _atom_coordinates(
    path: Path, chains: set[str], atom_names: set[str]
) -> dict[tuple, np.ndarray]:
    coordinates: dict[tuple, np.ndarray] = {}
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if not line.startswith("ATOM") or len(line) < 54:
            continue
        chain = line[21:22]
        atom_name = line[12:16].strip()
        altloc = line[16:17]
        if chain not in chains or atom_name not in atom_names or altloc not in {" ", "A"}:
            continue
        key = (chain, line[22:26].strip(), line[26:27], atom_name)
        coordinates[key] = np.array(
            [float(line[30:38]), float(line[38:46]), float(line[46:54])], dtype=float
        )
    return coordinates


def _kabsch(mobile: np.ndarray, reference: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mobile_center = mobile.mean(axis=0)
    reference_center = reference.mean(axis=0)
    covariance = (mobile - mobile_center).T @ (reference - reference_center)
    left, _, right = np.linalg.svd(covariance)
    correction = np.eye(3)
    correction[-1, -1] = np.sign(np.linalg.det(left @ right))
    rotation = left @ correction @ right
    translation = reference_center - mobile_center @ rotation
    return rotation, translation


def peptide_backbone_rmsd_after_receptor_alignment(
    model: Path,
    native: Path,
    receptor_chains: list[str],
    peptide_chain: str,
) -> float:
    """Return peptide backbone RMSD after least-squares alignment on receptor C-alpha atoms."""
    receptor_set = set(receptor_chains)
    model_receptor = _atom_coordinates(model, receptor_set, {"CA"})
    native_receptor = _atom_coordinates(native, receptor_set, {"CA"})
    receptor_keys = sorted(model_receptor.keys() & native_receptor.keys())
    if len(receptor_keys) < 3:
        raise ValueError("fewer than three matched receptor C-alpha atoms")

    mobile_receptor = np.stack([model_receptor[key] for key in receptor_keys])
    reference_receptor = np.stack([native_receptor[key] for key in receptor_keys])
    rotation, translation = _kabsch(mobile_receptor, reference_receptor)

    model_peptide = _atom_coordinates(model, {peptide_chain}, set(BACKBONE_ATOMS))
    native_peptide = _atom_coordinates(native, {peptide_chain}, set(BACKBONE_ATOMS))
    peptide_keys = sorted(model_peptide.keys() & native_peptide.keys())
    if len(peptide_keys) < 3:
        raise ValueError("fewer than three matched peptide backbone atoms")
    mobile = np.stack([model_peptide[key] for key in peptide_keys])
    reference = np.stack([native_peptide[key] for key in peptide_keys])
    aligned = mobile @ rotation + translation
    return float(np.sqrt(np.mean(np.sum((aligned - reference) ** 2, axis=1))))
