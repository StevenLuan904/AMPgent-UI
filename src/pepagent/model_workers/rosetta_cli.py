from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import statistics
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.structures.pdb import (
    peptide_backbone_rmsd_after_receptor_alignment,
    prepare_protein_peptide_pdb,
)

ADAPTER_VERSION = "pepagent-pyrosetta-flexpepdock-v3"
PACK_SEPARATED = False
RUN_MANIFEST_SCHEMA = "ampgent.rosetta-resumable-run.1"
RUN_MANIFEST_NAME = "run_manifest.json"
RUN_LOCK_NAME = ".run.lock"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


@contextmanager
def _exclusive_run_lock(path: Path):
    """Hold one cross-process candidate/seed lock until the result is durable."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _canonicalize_dumped_pdb(path: Path) -> None:
    """Remove work-directory identity embedded by Rosetta's energy-table footer."""
    output: list[str] = []
    for line in path.read_text(encoding="ascii").splitlines():
        if line.startswith(("#BEGIN_POSE_ENERGIES_TABLE ", "#END_POSE_ENERGIES_TABLE ")):
            line = f"{line.split(' ', 1)[0]} {path.name}"
        output.append(line)
    path.write_text("\n".join(output) + "\n", encoding="ascii")


def _pyrosetta_init(seed: int, receptor: str, peptide: str, mode: str) -> Any:
    import pyrosetta

    mode_flag = "-flexpep_prepack" if mode == "prepack" else "-pep_refine"
    options = " ".join(
        [
            "-mute all",
            "-constant_seed",
            f"-jran {seed}",
            mode_flag,
            f"-flexPepDocking:receptor_chain {receptor}",
            f"-flexPepDocking:peptide_chain {peptide}",
            "-ex1",
            "-ex2aro",
            "-use_input_sc",
            "-score:weights ref2015",
        ]
    )
    pyrosetta.init(options)
    return pyrosetta


def _score_pose(pyrosetta: Any, pose: Any, interface: str) -> dict[str, float | None]:
    from pyrosetta.rosetta.core.pose import DockingPartners
    from pyrosetta.rosetta.protocols.analysis import InterfaceAnalyzerMover

    scorefxn = pyrosetta.create_score_function("ref2015")
    scorefxn(pose)
    partners = DockingPartners.docking_partners_from_string(interface)
    analyzer = InterfaceAnalyzerMover(partners)
    analyzer.set_scorefunction(scorefxn)
    analyzer.set_pack_input(False)
    analyzer.set_pack_separated(PACK_SEPARATED)
    analyzer.set_compute_packstat(True)
    analyzer.apply(pose)
    scores = {str(key): float(value) for key, value in pose.scores.items()}

    def first(*names: str) -> float | None:
        for name in names:
            if name in scores:
                return scores[name]
        return None

    total_score = float(scorefxn(pose))
    peptide_score = float(analyzer.get_side2_score())
    interface_score = float(analyzer.get_crossterm_interface_energy())
    return {
        "total_score": total_score,
        "peptide_score": peptide_score,
        "dG_separated": float(analyzer.get_interface_dG()),
        "dG_separated_per_dSASA_x100": first("dG_separated/dSASAx100"),
        "dSASA_int": float(analyzer.get_interface_delta_sasa()),
        "interface_hbonds": first("hbonds_int", "I_hb"),
        "packstat": float(analyzer.get_interface_packstat()),
        "interface_score": interface_score,
        # FlexPepDock's standard ranking score is total + peptide + interface score.
        "reweighted_sc": total_score + peptide_score + interface_score,
        "delta_unsat_hbonds": float(analyzer.get_interface_delta_hbond_unsat()),
    }


def _run_stage(args: argparse.Namespace) -> None:
    pyrosetta = _pyrosetta_init(
        args.seed, "".join(args.receptor_chain), args.peptide_chain, args.stage
    )
    pose = pyrosetta.pose_from_file(str(args.input_structure))
    from pyrosetta.rosetta.protocols.flexpep_docking import FlexPepDockingProtocol

    protocol = FlexPepDockingProtocol()
    protocol.apply(pose)
    args.output_structure.parent.mkdir(parents=True, exist_ok=True)
    pose.dump_pdb(str(args.output_structure))
    _canonicalize_dumped_pdb(args.output_structure)
    if args.stage == "refine":
        metrics = _score_pose(
            pyrosetta, pose, f"{''.join(args.receptor_chain)}_{args.peptide_chain}"
        )
        args.output_json.write_text(
            json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8"
        )


def _run_child(command: list[str]) -> None:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"PyRosetta child failed ({completed.returncode}): "
            f"{completed.stdout[-2000:]}\n{completed.stderr[-4000:]}"
        )


def _summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "count": float(len(ordered)),
        "minimum": ordered[0],
        "median": float(statistics.median(ordered)),
        "maximum": ordered[-1],
    }


def _run_manifest(request: dict[str, Any], input_structure: Path) -> dict[str, Any]:
    return {
        "schema_version": RUN_MANIFEST_SCHEMA,
        "request_sha256": sha256_json(request),
        "input_structure_sha256": sha256_file(input_structure),
    }


def _bind_run_manifest(work_dir: Path, expected: dict[str, Any]) -> dict[str, Any]:
    path = work_dir / RUN_MANIFEST_NAME
    if path.exists():
        try:
            observed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Rosetta resume manifest is unreadable") from error
        if not isinstance(observed, dict) or any(
            observed.get(field) != value for field, value in expected.items()
        ):
            raise ValueError("Rosetta deterministic work scope has different input identity")
        return observed
    if (work_dir / "decoys").exists():
        raise ValueError("Rosetta decoys exist without a bound resume manifest")
    _atomic_write_json(path, expected)
    return expected


def _safe_artifact_path(work_dir: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("Rosetta result has an invalid artifact path")
    resolved_root = work_dir.resolve()
    resolved = (work_dir / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("Rosetta result artifact escapes its deterministic work scope") from error
    return resolved


def _load_completed_result(
    output_path: Path,
    *,
    work_dir: Path,
    seed: int,
    nstruct: int,
    prepacked_sha256: str | None,
) -> dict[str, Any] | None:
    if not output_path.exists():
        return None
    try:
        result = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(result, dict):
        return None
    decoys = result.get("decoys")
    if (
        result.get("schema_version") != "1.0"
        or int(result.get("seed", -1)) != seed
        or int(result.get("nstruct", -1)) != nstruct
        or not isinstance(decoys, list)
        or len(decoys) != nstruct
        or not prepacked_sha256
        or result.get("prepacked_input_sha256") != prepacked_sha256
    ):
        return None
    prepacked = work_dir / "input.prepacked.pdb"
    if not prepacked.is_file() or sha256_file(prepacked) != prepacked_sha256:
        return None
    for index, decoy in enumerate(decoys, start=1):
        if not isinstance(decoy, dict):
            return None
        if int(decoy.get("index", -1)) != index or int(decoy.get("seed", -1)) != seed + index:
            return None
        try:
            structure = _safe_artifact_path(work_dir, decoy.get("structure"))
        except ValueError:
            return None
        if not structure.is_file() or sha256_file(structure) != decoy.get("structure_sha256"):
            return None
    return result


def _load_decoy_checkpoint(
    *,
    index: int,
    seed: int,
    work_dir: Path,
    prepacked_sha256: str,
) -> dict[str, Any] | None:
    decoy_path = work_dir / "decoys" / f"decoy_{index + 1:04d}.pdb"
    metric_path = work_dir / "decoys" / f"decoy_{index + 1:04d}.json"
    if not decoy_path.is_file() or not metric_path.is_file():
        return None
    try:
        metrics = json.loads(metric_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(metrics, dict):
        return None
    checkpoint_prepacked = metrics.pop("_checkpoint_prepacked_sha256", None)
    expected_seed = seed + index + 1
    if (
        checkpoint_prepacked != prepacked_sha256
        or int(metrics.get("index", -1)) != index + 1
        or int(metrics.get("seed", -1)) != expected_seed
        or metrics.get("structure") != str(decoy_path.relative_to(work_dir))
        or metrics.get("structure_sha256") != sha256_file(decoy_path)
        or "dG_separated" not in metrics
        or "peptide_bb_rmsd" not in metrics
    ):
        return None
    return metrics


def _run(args: argparse.Namespace) -> None:
    request = json.loads(args.request.read_text(encoding="utf-8"))
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with _exclusive_run_lock(args.work_dir / RUN_LOCK_NAME):
        _run_locked(args, request)


def _run_locked(args: argparse.Namespace, request: dict[str, Any]) -> None:
    receptor_chains = [str(chain) for chain in request["receptor_chains"]]
    peptide_chain = str(request["peptide_chain"])
    nstruct = int(request.get("nstruct", 20))
    parallel_decoys = int(request.get("parallel_decoys", 1))
    seed = int(request["seed"])
    if nstruct < 1:
        raise ValueError("nstruct must be positive")
    if parallel_decoys < 1:
        raise ValueError("parallel_decoys must be positive")

    run_manifest = _bind_run_manifest(
        args.work_dir,
        _run_manifest(request, args.input_structure),
    )
    completed = _load_completed_result(
        args.output,
        work_dir=args.work_dir,
        seed=seed,
        nstruct=nstruct,
        prepacked_sha256=run_manifest.get("prepacked_input_sha256"),
    )
    if completed is not None:
        return
    prepared = args.work_dir / "input.prepared.pdb"
    atom_counts = prepare_protein_peptide_pdb(
        args.input_structure, prepared, receptor_chains, peptide_chain
    )
    native = args.work_dir / "native.prepared.pdb"
    native_source = Path(request.get("native_structure") or args.input_structure)
    prepare_protein_peptide_pdb(native_source, native, receptor_chains, peptide_chain)

    prepacked = args.work_dir / "input.prepacked.pdb"
    child_base = [
        sys.executable,
        "-m",
        "pepagent.model_workers.rosetta_cli",
        "--peptide-chain",
        peptide_chain,
    ]
    for chain in receptor_chains:
        child_base.extend(["--receptor-chain", chain])
    bound_prepacked_sha256 = run_manifest.get("prepacked_input_sha256")
    if not (
        isinstance(bound_prepacked_sha256, str)
        and prepacked.is_file()
        and sha256_file(prepacked) == bound_prepacked_sha256
    ):
        _run_child(
            [
                *child_base,
                "--stage",
                "prepack",
                "--seed",
                str(seed),
                "--input-structure",
                str(prepared),
                "--output-structure",
                str(prepacked),
            ]
        )
        generated_prepacked_sha256 = sha256_file(prepacked)
        if (
            isinstance(bound_prepacked_sha256, str)
            and generated_prepacked_sha256 != bound_prepacked_sha256
        ):
            raise ValueError("Rosetta prepack identity drifted during checkpoint recovery")
        if bound_prepacked_sha256 is None:
            run_manifest = {
                **run_manifest,
                "prepacked_input_sha256": generated_prepacked_sha256,
            }
            _atomic_write_json(args.work_dir / RUN_MANIFEST_NAME, run_manifest)
    prepacked_sha256 = sha256_file(prepacked)

    def refine_one(index: int) -> dict[str, Any]:
        decoy_seed = seed + index + 1
        decoy_path = args.work_dir / "decoys" / f"decoy_{index + 1:04d}.pdb"
        metric_path = args.work_dir / "decoys" / f"decoy_{index + 1:04d}.json"
        decoy_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint = _load_decoy_checkpoint(
            index=index,
            seed=seed,
            work_dir=args.work_dir,
            prepacked_sha256=prepacked_sha256,
        )
        if checkpoint is not None:
            return checkpoint
        _run_child(
            [
                *child_base,
                "--stage",
                "refine",
                "--seed",
                str(decoy_seed),
                "--input-structure",
                str(prepacked),
                "--output-structure",
                str(decoy_path),
                "--output-json",
                str(metric_path),
            ]
        )
        metrics = json.loads(metric_path.read_text(encoding="utf-8"))
        metrics.update(
            {
                "index": index + 1,
                "seed": decoy_seed,
                "structure": str(decoy_path.relative_to(args.work_dir)),
                "structure_sha256": sha256_file(decoy_path),
                "peptide_bb_rmsd": peptide_backbone_rmsd_after_receptor_alignment(
                    decoy_path, native, receptor_chains, peptide_chain
                ),
            }
        )
        _atomic_write_json(
            metric_path,
            {**metrics, "_checkpoint_prepacked_sha256": prepacked_sha256},
        )
        return metrics

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(parallel_decoys, nstruct)
    ) as executor:
        decoys = list(executor.map(refine_one, range(nstruct)))

    ranked_by_dg = sorted(
        decoys,
        key=lambda item: (
            item["dG_separated"] is None,
            item["dG_separated"] if item["dG_separated"] is not None else float("inf"),
        ),
    )
    rank_metric = (
        "reweighted_sc"
        if all(item.get("reweighted_sc") is not None for item in decoys)
        else "total_score"
    )
    ranked = sorted(decoys, key=lambda item: float(item[rank_metric]))
    top_count = min(10, len(ranked))
    top_dgs = [
        float(item["dG_separated"])
        for item in ranked[:top_count]
        if item["dG_separated"] is not None
    ]
    if not top_dgs:
        raise RuntimeError("InterfaceAnalyzer produced no dG_separated values")
    dgs = [float(item["dG_separated"]) for item in decoys if item["dG_separated"] is not None]
    rmsds = [float(item["peptide_bb_rmsd"]) for item in decoys]
    result = {
        "schema_version": "1.0",
        "adapter_version": ADAPTER_VERSION,
        "engine": "PyRosetta/FlexPepDock+InterfaceAnalyzer",
        "score_function": "ref2015",
        "interface": f"{''.join(receptor_chains)}_{peptide_chain}",
        "seed": seed,
        "nstruct": nstruct,
        "parallel_decoys": parallel_decoys,
        "pack_input": False,
        "pack_separated": PACK_SEPARATED,
        "input_sha256": sha256_file(args.input_structure),
        "prepared_input_sha256": sha256_file(prepared),
        "prepacked_input_sha256": sha256_file(prepacked),
        "native_sha256": sha256_file(native),
        "atom_counts": atom_counts,
        "dG_separated_reu": _summary(dgs),
        "primary_dG_separated_reu": float(statistics.median(top_dgs)),
        "primary_aggregation": {
            "rank_metric": rank_metric,
            "top_decoy_count": top_count,
            "aggregation": "median",
        },
        "peptide_bb_rmsd_angstrom": _summary(rmsds),
        "best_decoy": ranked[0],
        "minimum_dG_decoy": ranked_by_dg[0],
        "decoys": decoys,
        "artifacts": [
            str(path.relative_to(args.work_dir))
            for path in sorted(args.work_dir.rglob("*"))
            if path.is_file()
            and path.name not in {RUN_LOCK_NAME, RUN_MANIFEST_NAME}
            and not path.name.endswith(".tmp")
        ],
        "limitations": [
            "dG_separated is reported in Rosetta energy units, not experimental kcal/mol",
            "FlexPepDock refinement assumes the starting peptide is already near the binding site",
            "absolute Kd conversion is forbidden without target-family calibration",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(args.output, result)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--stage", choices=["prepack", "refine"])
    parser.add_argument("--seed", type=int)
    parser.add_argument("--input-structure", type=Path, required=True)
    parser.add_argument("--output-structure", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--receptor-chain", action="append", default=[])
    parser.add_argument("--peptide-chain")
    args = parser.parse_args()
    if args.stage:
        _run_stage(args)
    else:
        if not args.request or not args.output or not args.work_dir:
            parser.error("--request, --output and --work-dir are required for an orchestrated run")
        _run(args)


if __name__ == "__main__":
    main()
