"""Run single-trajectory AmberTools MM/GBSA with residue decomposition."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import subprocess
from pathlib import Path

import parmed as pmd
from openmm import app, unit


def cli():
    p = argparse.ArgumentParser()
    p.add_argument("--topology-pdb", type=Path, required=True)
    p.add_argument("--trajectory", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--amberhome", type=Path, required=True)
    p.add_argument("--receptor-chain", default="A")
    p.add_argument("--peptide-chain", default="B")
    p.add_argument("--startframe", type=int, default=501)
    p.add_argument("--endframe", type=int, default=9999999)
    p.add_argument("--interval", type=int, default=25)
    p.add_argument("--bootstrap-replicates", type=int, default=2000)
    return p.parse_args()


def run(cmd, cwd, env, log):
    with log.open("a") as h:
        h.write("COMMAND " + " ".join(map(str, cmd)) + "\n")
        done = subprocess.run(
            list(map(str, cmd)), cwd=cwd, env=env, stdout=h, stderr=subprocess.STDOUT
        )
    if done.returncode:
        raise RuntimeError(f"command failed ({done.returncode}): {cmd[0]}")


def residue_counts(pdb, receptor_chain, peptide_chain):
    seen = {receptor_chain: [], peptide_chain: []}
    keys = {receptor_chain: set(), peptide_chain: set()}
    with pdb.open(errors="replace") as h:
        for line in h:
            if not line.startswith(("ATOM  ", "HETATM")):
                continue
            chain = line[21:22].strip()
            key = (line[22:26], line[26:27])
            if chain in keys and key not in keys[chain]:
                keys[chain].add(key)
                seen[chain].append(key)
    if not seen[receptor_chain] or not seen[peptide_chain]:
        raise ValueError("receptor/peptide chains absent")
    return len(seen[receptor_chain]), len(seen[peptide_chain])


def write_amber_topology(source, destination):
    pdb = app.PDBFile(str(source))
    ff = app.ForceField("amber14/protein.ff14SB.xml", "amber14/tip3p.xml")
    system = ff.createSystem(
        pdb.topology,
        nonbondedMethod=app.PME,
        nonbondedCutoff=1.0 * unit.nanometer,
        constraints=None,
        rigidWater=False,
    )
    structure = pmd.openmm.load_topology(pdb.topology, system, xyz=pdb.positions)
    structure.save(str(destination), overwrite=True)
    return len(structure.atoms)


def energy_values(path):
    rows = list(csv.reader(path.open(newline="")))
    values = []
    delta = False
    for i, row in enumerate(rows):
        if row and row[0].strip().upper().startswith("DELTA ENERGY TERMS"):
            delta = True
            continue
        if not delta:
            continue
        normalized = [x.strip().upper().replace(" ", "_") for x in row]
        if "DELTA_TOTAL" not in normalized:
            continue
        col = normalized.index("DELTA_TOTAL")
        for data in rows[i + 1 :]:
            if not data or not any(x.strip() for x in data):
                break
            try:
                values.append(float(data[col]))
            except (ValueError, IndexError):
                continue
        break
    if not values:
        raise ValueError("no per-frame DELTA TOTAL vector found")
    return values


def write_residue_means(source, destination):
    rows = list(csv.reader(source.open(newline="")))
    header = None
    data = []
    for i, row in enumerate(rows):
        if (
            len(row) >= 2
            and row[0].strip().upper() == "DELTA"
            and row[1].strip() == "Total Energy Decomposition:"
        ):
            header = rows[i + 1]
            for item in rows[i + 2 :]:
                if not item or not any(x.strip() for x in item):
                    break
                data.append(item)
            break
    if not header or not data:
        raise ValueError("DELTA residue decomposition absent")
    groups = {}
    for row in data:
        key = (row[1].strip(), row[2].strip())
        groups.setdefault(key, []).append([float(x) for x in row[3:]])
    with destination.open("w", newline="") as h:
        out = csv.writer(h)
        out.writerow(["residue", "location", *["mean_" + x for x in header[3:]]])
        for key, values in groups.items():
            out.writerow([*key, *[sum(x) / len(x) for x in zip(*values, strict=True)]])
    return len(groups)


def block_ci(values, reps, seed=20260903):
    n = len(values)
    block = max(1, round(math.sqrt(n)))
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(values[(start + k) % n] for k in range(block))
        means.append(sum(sample[:n]) / n)
    means.sort()
    return block, means[int(0.025 * reps)], means[min(reps - 1, int(0.975 * reps))]


def main():
    a = cli()
    out = a.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    log = out / "mmgbsa.log"
    if (out / "mmgbsa_analysis.json").exists():
        return
    env = os.environ.copy()
    env["AMBERHOME"] = str(a.amberhome)
    env["PATH"] = str(a.amberhome / "bin") + os.pathsep + env.get("PATH", "")
    nr, np_ = residue_counts(a.topology_pdb, a.receptor_chain, a.peptide_chain)
    ligand_start = nr + 1
    ligand_end = nr + np_
    solvated = out / "solvated.prmtop"
    topology_atom_count = write_amber_topology(a.topology_pdb, solvated)
    run(
        [
            a.amberhome / "bin/ante-MMPBSA.py",
            "-p",
            "solvated.prmtop",
            "-c",
            "complex.prmtop",
            "-r",
            "receptor.prmtop",
            "-l",
            "ligand.prmtop",
            "-s",
            ":WAT,HOH,Na+,Cl-,NA,CL",
            "-m",
            f":1-{nr}",
            "--radii",
            "mbondi2",
        ],
        out,
        env,
        log,
    )
    inp = out / "mmpbsa.in"
    inp.write_text(
        f"&general\n startframe={a.startframe}, endframe={a.endframe}, "
        f"interval={a.interval}, keep_files=0, "
        "strip_mask=':WAT,HOH,Na+,Cl-,NA,CL',\n/\n"
        "&gb\n igb=5, saltcon=0.150,\n/\n"
        "&decomp\n idecomp=1, dec_verbose=1, print_res='all', csv_format=1,\n/\n"
    )
    run(
        [
            a.amberhome / "bin/MMPBSA.py",
            "-O",
            "-i",
            inp,
            "-sp",
            "solvated.prmtop",
            "-cp",
            "complex.prmtop",
            "-rp",
            "receptor.prmtop",
            "-lp",
            "ligand.prmtop",
            "-y",
            a.trajectory.resolve(),
            "-o",
            "summary.dat",
            "-do",
            "decomposition_summary.dat",
            "-eo",
            "energies.csv",
            "-deo",
            "decomposition.csv",
        ],
        out,
        env,
        log,
    )
    values = energy_values(out / "energies.csv")
    block, lo, hi = block_ci(values, a.bootstrap_replicates)
    decomposition_residue_count = write_residue_means(
        out / "decomposition.csv", out / "residue_decomposition_mean.csv"
    )
    result = {
        "schema_version": "ampgent.pool-a-mmgbsa.1",
        "method": "single-trajectory MM/GBSA",
        "ambertools_environment": str(a.amberhome),
        "igb": 5,
        "saltcon_molar": 0.15,
        "frame_count": len(values),
        "startframe": a.startframe,
        "endframe": a.endframe,
        "interval": a.interval,
        "mean_binding_energy_kcal_mol": sum(values) / len(values),
        "moving_block_length_frames": block,
        "bootstrap_replicates": a.bootstrap_replicates,
        "confidence_interval_95_kcal_mol": [lo, hi],
        "receptor_residue_count": nr,
        "peptide_residue_range": [ligand_start, ligand_end],
        "topology_atom_count": topology_atom_count,
        "decomposition_residue_count": decomposition_residue_count,
        "limitations": [
            "within-trajectory block-bootstrap CI; not independent replicate uncertainty",
            "computed binding estimate; not experimental affinity",
        ],
    }
    (out / "mmgbsa_analysis.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
