"""Post-process one Pool-A OpenMM trajectory into auditable interface metrics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import mdtraj as md
import numpy as np

POS = {"ARG": {"NH1", "NH2", "NE"}, "LYS": {"NZ"}, "HIS": {"ND1", "NE2"}}
NEG = {"ASP": {"OD1", "OD2"}, "GLU": {"OE1", "OE2"}}
POLAR = {"N", "O", "S"}
def cli():
    p = argparse.ArgumentParser()
    p.add_argument("--topology", type=Path, required=True)
    p.add_argument("--trajectory", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--receptor-chain", default="A")
    p.add_argument("--peptide-chain", default="B")
    p.add_argument("--frame-ps", type=float, default=10.0)
    p.add_argument("--interaction-stride", type=int, default=10)
    return p.parse_args()


def chain_atoms(top, chain_id):
    chains = [c for c in top.chains if str(c.chain_id) == chain_id]
    if len(chains) != 1:
        raise ValueError(f"chain {chain_id!r} count={len(chains)}")
    return np.array([a.index for a in chains[0].atoms], dtype=int)


def atom_subset(top, atoms, *, heavy=False, backbone=False):
    out = []
    for i in atoms:
        a = top.atom(int(i))
        if heavy and a.element.symbol == "H":
            continue
        if backbone and a.name not in {"N", "CA", "C", "O"}:
            continue
        out.append(i)
    return np.array(out, dtype=int)


def residue_pairs(top, receptor, peptide):
    rr = sorted({top.atom(int(i)).residue.index for i in receptor})
    pr = sorted({top.atom(int(i)).residue.index for i in peptide})
    return np.array([(a, b) for a in rr for b in pr], dtype=int)


def runs(mask):
    best = cur = 0
    for x in mask:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


def image_complex(traj, receptor, peptide):
    """Make receptor and peptide whole and place them in the same periodic image."""
    receptor_set = set(map(int, receptor))
    peptide_set = set(map(int, peptide))
    molecules = traj.topology.find_molecules()
    receptor_molecules = []
    peptide_molecules = []
    for molecule in molecules:
        indices = {atom.index for atom in molecule}
        if indices & receptor_set:
            receptor_molecules.append(molecule)
        if indices & peptide_set:
            peptide_molecules.append(molecule)
    if len(receptor_molecules) != 1 or len(peptide_molecules) != 1:
        raise ValueError(
            "expected one receptor and one peptide molecule, found "
            f"{len(receptor_molecules)} and {len(peptide_molecules)}"
        )
    if receptor_molecules[0] is peptide_molecules[0]:
        raise ValueError("receptor and peptide are covalently connected")
    traj.image_molecules(
        anchor_molecules=receptor_molecules,
        other_molecules=peptide_molecules,
        inplace=True,
    )


def frame_has_water_bridge(frame, receptor_chain, peptide_chain, water_oxygens):
    """Apply Wernet-Nilsson only to waters already near both partners."""
    if not water_oxygens:
        return False
    top = frame.topology
    water_residues = {top.atom(int(index)).residue.index for index in water_oxygens}
    selected = [
        atom.index
        for atom in top.atoms
        if str(atom.residue.chain.chain_id) in {receptor_chain, peptide_chain}
        or atom.residue.index in water_residues
    ]
    shell = frame.atom_slice(selected)
    shell_top = shell.topology
    receptor_set = set(map(int, chain_atoms(shell_top, receptor_chain)))
    peptide_set = set(map(int, chain_atoms(shell_top, peptide_chain)))
    receptor_waters = set()
    peptide_waters = set()
    for donor, _, acceptor in md.wernet_nilsson(
        shell, exclude_water=False, periodic=True
    )[0]:
        donor_atom = shell_top.atom(int(donor))
        acceptor_atom = shell_top.atom(int(acceptor))
        if donor_atom.residue.is_water:
            if int(acceptor) in receptor_set:
                receptor_waters.add(donor_atom.residue.index)
            elif int(acceptor) in peptide_set:
                peptide_waters.add(donor_atom.residue.index)
        if acceptor_atom.residue.is_water:
            if int(donor) in receptor_set:
                receptor_waters.add(acceptor_atom.residue.index)
            elif int(donor) in peptide_set:
                peptide_waters.add(acceptor_atom.residue.index)
    return bool(receptor_waters & peptide_waters)


def main():
    a = cli()
    a.output_dir.mkdir(parents=True, exist_ok=True)
    traj = md.load(str(a.trajectory), top=str(a.topology))
    top = traj.topology
    receptor = chain_atoms(top, a.receptor_chain)
    peptide = chain_atoms(top, a.peptide_chain)
    receptor_bb = atom_subset(top, receptor, backbone=True)
    peptide_bb = atom_subset(top, peptide, backbone=True)
    image_complex(traj, receptor, peptide)
    interaction_source = traj[:]
    reference = traj[0]
    traj.superpose(reference, atom_indices=receptor_bb)
    pairs = residue_pairs(top, receptor, peptide)
    distances, pairs = md.compute_contacts(
        interaction_source, pairs, scheme="closest-heavy", periodic=True
    )
    native_mask = distances[0] <= 0.45
    native_pairs = pairs[native_mask]
    native_dist = distances[:, native_mask]
    contact_occ = (native_dist <= 0.45).mean(axis=0) if native_dist.size else np.array([])
    interface_res = sorted(set(native_pairs[:, 0].tolist())) if len(native_pairs) else []
    interface_bb = np.array(
        [
            x.index
            for r in interface_res
            for x in top.residue(int(r)).atoms
            if x.name in {"N", "CA", "C", "O"}
        ],
        dtype=int,
    )
    rmsd_atoms = np.concatenate([interface_bb, peptide_bb])
    delta = traj.xyz[:, rmsd_atoms, :] - reference.xyz[0, rmsd_atoms, :]
    interface_rmsd_nm = np.sqrt(np.mean(np.sum(delta * delta, axis=2), axis=1))
    peptide_com = traj.xyz[:, peptide_bb, :].mean(axis=1)
    com_shift = np.linalg.norm(peptide_com - peptide_com[0], axis=1)
    q = (native_dist <= 0.45).mean(axis=1) if native_dist.size else np.zeros(traj.n_frames)
    departed = (q < 0.2) & (com_shift > 1.0)
    sustained = runs(departed) * a.frame_ps >= 1000

    atom_pairs = []
    pair_types = []
    for i in receptor:
        ai = top.atom(int(i))
        for j in peptide:
            aj = top.atom(int(j))
            pos_i = ai.residue.name in POS and ai.name in POS[ai.residue.name]
            neg_i = ai.residue.name in NEG and ai.name in NEG[ai.residue.name]
            pos_j = aj.residue.name in POS and aj.name in POS[aj.residue.name]
            neg_j = aj.residue.name in NEG and aj.name in NEG[aj.residue.name]
            if (pos_i and neg_j) or (neg_i and pos_j):
                atom_pairs.append((i, j))
                pair_types.append("salt_bridge")
    ap = np.array(atom_pairs, dtype=int)
    ad = (
        md.compute_distances(interaction_source, ap, periodic=True)
        if len(ap)
        else np.empty((traj.n_frames, 0))
    )
    salt = [k for k, t in enumerate(pair_types) if t == "salt_bridge"]
    salt_any = (ad[:, salt] <= 0.40).any(axis=1) if salt else np.zeros(traj.n_frames, dtype=bool)
    receptor_set = set(map(int, receptor))
    peptide_set = set(map(int, peptide))
    interaction_traj = interaction_source[:: max(1, a.interaction_stride)]
    hbonds = md.wernet_nilsson(interaction_traj, exclude_water=True, periodic=True)
    direct_hbond = np.zeros(interaction_traj.n_frames, dtype=bool)
    for frame, bonds in enumerate(hbonds):
        for donor, _, acceptor in bonds:
            d = int(donor)
            ac = int(acceptor)
            if (d in receptor_set and ac in peptide_set) or (
                d in peptide_set and ac in receptor_set
            ):
                direct_hbond[frame] = True
    water = np.array(
        [atom.index for atom in top.atoms if atom.residue.is_water and atom.name == "O"],
        dtype=int,
    )
    receptor_polar = np.array(
        [index for index in receptor if top.atom(int(index)).element.symbol in POLAR], dtype=int
    )
    peptide_polar = np.array(
        [index for index in peptide if top.atom(int(index)).element.symbol in POLAR], dtype=int
    )
    near_receptor = md.compute_neighbors(
        interaction_traj, 0.35, receptor_polar, haystack_indices=water, periodic=True
    )
    near_peptide = md.compute_neighbors(
        interaction_traj, 0.35, peptide_polar, haystack_indices=water, periodic=True
    )
    water_bridge = np.array(
        [
            frame_has_water_bridge(
                interaction_traj[index],
                a.receptor_chain,
                a.peptide_chain,
                set(map(int, receptor_waters)) & set(map(int, peptide_waters)),
            )
            for index, (receptor_waters, peptide_waters) in enumerate(
                zip(near_receptor, near_peptide, strict=True)
            )
        ],
        dtype=bool,
    )

    contact_rows = []
    for (r, p), occ in zip(native_pairs, contact_occ, strict=True):
        contact_rows.append(
            {
                "receptor_residue": str(top.residue(int(r))),
                "peptide_residue": str(top.residue(int(p))),
                "occupancy": float(occ),
            }
        )
    result = {
        "schema_version": "ampgent.pool-a-md-interface-analysis.2",
        "frame_count": traj.n_frames,
        "frame_ps": a.frame_ps,
        "interface_rmsd_nm": {
            "mean": float(interface_rmsd_nm.mean()),
            "maximum": float(interface_rmsd_nm.max()),
        },
        "native_contact_fraction": {"mean": float(q.mean()), "minimum": float(q.min())},
        "key_contacts": contact_rows,
        "hydrogen_bond_occupancy": float(direct_hbond.mean()),
        "salt_bridge_occupancy": float(salt_any.mean()),
        "water_bridge_occupancy": float(water_bridge.mean()),
        "peptide_departed": bool(sustained),
        "interaction_sample_count": interaction_traj.n_frames,
        "interaction_stride": a.interaction_stride,
        "maximum_departure_duration_ps": float(runs(departed) * a.frame_ps),
        "maximum_peptide_com_shift_nm": float(com_shift.max()),
        "definitions": {
            "key_contact": "native residue pair closest-heavy <=0.45 nm",
            "salt_bridge": "charged side-chain atom distance <=0.40 nm",
            "hydrogen_bond": "MDTraj Wernet-Nilsson geometric criterion",
            "water_bridge": (
                "same water participates in Wernet-Nilsson hydrogen bonds to receptor and peptide "
                "in one frame"
            ),
            "departure": (
                "native-contact fraction <0.2 and peptide COM shift >1.0 nm sustained >=1 ns"
            ),
        },
    }
    (a.output_dir / "interface_analysis.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    np.savetxt(
        a.output_dir / "timeseries.csv",
        np.column_stack(
            [np.arange(traj.n_frames) * a.frame_ps, interface_rmsd_nm, q, com_shift, departed]
        ),
        delimiter=",",
        header="time_ps,interface_rmsd_nm,native_contact_fraction,peptide_com_shift_nm,departed",
        comments="",
    )


if __name__ == "__main__":
    main()
