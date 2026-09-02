"""Resumable explicit-solvent OpenMM MD for one Pool-A complex."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import openmm as mm
from openmm import XmlSerializer, app, unit
from openmm.app import (
    CheckpointReporter,
    DCDReporter,
    ForceField,
    Modeller,
    PDBFile,
    Simulation,
    StateDataReporter,
)
from pdbfixer import PDBFixer


def args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--input-pdb", required=True, type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--gpu-index", required=True)
    p.add_argument("--seed", required=True, type=int)
    p.add_argument("--npt-ns", type=float, default=1.0)
    p.add_argument("--production-ns", type=float, default=50.0)
    p.add_argument("--report-interval-steps", type=int, default=5000)
    return p.parse_args()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def steps(ns: float) -> int:
    return max(1, round(ns * 500_000))


def save_state(path: Path, state: mm.State) -> None:
    path.write_text(XmlSerializer.serialize(state), encoding="utf-8")


def reporters(
    sim: Simulation, root: Path, stem: str, interval: int, total: int, append: bool
) -> None:
    sim.reporters.append(DCDReporter(str(root / f"{stem}.dcd"), interval, append=append))
    sim.reporters.append(CheckpointReporter(str(root / f"{stem}.chk"), interval))
    sim.reporters.append(
        StateDataReporter(
            str(root / f"{stem}.log"),
            interval,
            step=True,
            potentialEnergy=True,
            temperature=True,
            speed=True,
            progress=True,
            remainingTime=True,
            totalSteps=total,
            append=append,
        )
    )


def run_stage(
    *,
    topology: app.Topology,
    system: mm.System,
    positions,
    velocities,
    box,
    root: Path,
    stem: str,
    total: int,
    interval: int,
    gpu: str,
    seed: int,
) -> mm.State:
    integrator = mm.LangevinMiddleIntegrator(
        300 * unit.kelvin, 1 / unit.picosecond, 2 * unit.femtoseconds
    )
    integrator.setRandomNumberSeed(seed)
    sim = Simulation(
        topology,
        system,
        integrator,
        mm.Platform.getPlatformByName("CUDA"),
        {"DeviceIndex": gpu, "Precision": "mixed"},
    )
    checkpoint = root / f"{stem}.chk"
    append = checkpoint.exists()
    if append:
        sim.loadCheckpoint(str(checkpoint))
    else:
        sim.context.setPositions(positions)
        if box is not None:
            sim.context.setPeriodicBoxVectors(*box)
        if velocities is None:
            sim.context.setVelocitiesToTemperature(300 * unit.kelvin, seed + 1)
        else:
            sim.context.setVelocities(velocities)
    remaining = max(0, total - sim.currentStep)
    reporters(sim, root, stem, interval, total, append)
    if remaining:
        sim.step(remaining)
    return sim.context.getState(
        getPositions=True, getVelocities=True, getEnergy=True, enforcePeriodicBox=True
    )


def main() -> None:
    a = args()
    source = a.input_pdb.resolve(strict=True)
    root = a.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if (root / "manifest.json").exists():
        return
    fixer = PDBFixer(filename=str(source))
    fixer.findMissingResidues()
    fixer.missingResidues = {}
    fixer.findNonstandardResidues()
    fixer.replaceNonstandardResidues()
    fixer.removeHeterogens(False)
    # Preserve explicit SG-H evidence while PDBFixer resolves missing heavy atoms.
    # Removing hydrogens first can make nearby reduced cysteines look like a
    # disulfide and produce a CYS/CYX topology mismatch during solvation.
    fixer.findMissingAtoms()
    fixer.addMissingAtoms()
    # Rosetta writes explicit hydrogens.  Remove and regenerate them after its
    # inferred disulfide topology is known; retaining an SG hydrogen on an
    # inferred CYX bond makes ff14SB template matching fail.
    existing_hydrogens = [
        atom for atom in fixer.topology.atoms() if atom.element == app.element.hydrogen
    ]
    if existing_hydrogens:
        stripped = Modeller(fixer.topology, fixer.positions)
        stripped.delete(existing_hydrogens)
        fixer.topology = stripped.topology
        fixer.positions = stripped.positions
    fixer.addMissingHydrogens(7.0)
    if len(list(fixer.topology.chains())) < 2:
        raise ValueError("input is not a protein-peptide complex")
    ff = ForceField("amber14/protein.ff14SB.xml", "amber14/tip3p.xml")
    modeller = Modeller(fixer.topology, fixer.positions)
    modeller.addSolvent(
        ff, padding=1.0 * unit.nanometer, model="tip3p", ionicStrength=0.15 * unit.molar
    )
    with (root / "prepared_solvated.pdb").open("w") as f:
        PDBFile.writeFile(modeller.topology, modeller.positions, f, keepIds=True)
    base = ff.createSystem(
        modeller.topology,
        nonbondedMethod=app.PME,
        nonbondedCutoff=1.0 * unit.nanometer,
        constraints=app.HBonds,
    )
    mini = Simulation(
        modeller.topology,
        base,
        mm.LangevinMiddleIntegrator(0 * unit.kelvin, 1 / unit.picosecond, 2 * unit.femtoseconds),
        mm.Platform.getPlatformByName("CUDA"),
        {"DeviceIndex": a.gpu_index, "Precision": "mixed"},
    )
    mini.context.setPositions(modeller.positions)
    mini.minimizeEnergy()
    initial = mini.context.getState(getPositions=True)
    npt_system = ff.createSystem(
        modeller.topology,
        nonbondedMethod=app.PME,
        nonbondedCutoff=1.0 * unit.nanometer,
        constraints=app.HBonds,
    )
    npt_system.addForce(mm.MonteCarloBarostat(1 * unit.bar, 300 * unit.kelvin))
    npt = run_stage(
        topology=modeller.topology,
        system=npt_system,
        positions=initial.getPositions(),
        velocities=None,
        box=None,
        root=root,
        stem="npt",
        total=steps(a.npt_ns),
        interval=a.report_interval_steps,
        gpu=a.gpu_index,
        seed=a.seed,
    )
    save_state(root / "npt_end_state.xml", npt)
    nvt_system = ff.createSystem(
        modeller.topology,
        nonbondedMethod=app.PME,
        nonbondedCutoff=1.0 * unit.nanometer,
        constraints=app.HBonds,
    )
    final = run_stage(
        topology=modeller.topology,
        system=nvt_system,
        positions=npt.getPositions(),
        velocities=npt.getVelocities(),
        box=npt.getPeriodicBoxVectors(),
        root=root,
        stem="production",
        total=steps(a.production_ns),
        interval=a.report_interval_steps,
        gpu=a.gpu_index,
        seed=a.seed + 10,
    )
    save_state(root / "production_end_state.xml", final)
    manifest = {
        "schema_version": "ampgent.pool-a-md.1",
        "status": "succeeded",
        "completed_at": datetime.now(UTC).isoformat(),
        "input_pdb": str(source),
        "input_sha256": digest(source),
        "gpu_index": a.gpu_index,
        "seed": a.seed,
        "npt_ns": a.npt_ns,
        "production_ns": a.production_ns,
        "openmm_version": mm.version.full_version,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
