from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import gemmi

GPU_PYTHON = Path("/sdd_data/pepagent/envs/gpu-worker-py311-v1/bin/python")
ROSETTA_PYTHON = Path("/sdd_data/pepagent/envs/pyrosetta-quarterly-py311-v1/bin/python")
BOLTZ_CACHE = Path("/sdd_data/pepagent/models/boltz2/cache")
ALLOWED_ROOT = Path("/sdd_data/pepagent/ampgent/structure")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def resolve_target(manifest_path: Path, target_key: str) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    matches = [item for item in manifest["targets"] if item["target_key"] == target_key]
    if len(matches) != 1:
        raise ValueError(f"expected one target {target_key!r}, found {len(matches)}")
    return matches[0]


def gpu2_preflight() -> dict[str, Any]:
    query = subprocess.run(
        [
            "nvidia-smi",
            "-i",
            "2",
            "--query-gpu=index,name,memory.total,memory.used,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    processes = subprocess.run(
        [
            "nvidia-smi",
            "-i",
            "2",
            "--query-compute-apps=pid,process_name,used_memory",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    declarations: list[int] = []
    for environ in Path("/proc").glob("[0-9]*/environ"):
        try:
            values = environ.read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if b"CUDA_VISIBLE_DEVICES=2" in values:
            declarations.append(int(environ.parent.name))
    fields = [item.strip() for item in query.split(",")]
    if len(fields) != 5 or fields[0] != "2":
        raise RuntimeError(f"unexpected GPU2 query: {query!r}")
    if processes or declarations:
        raise RuntimeError(
            "synth GPU2 is not strictly idle: "
            f"processes={processes!r}, declarations={declarations!r}"
        )
    return {
        "index": 2,
        "name": fields[1],
        "memory_total_mib": int(fields[2]),
        "memory_used_mib": int(fields[3]),
        "utilization_percent": int(fields[4]),
        "compute_processes": [],
        "cuda_visible_devices_declarations": [],
    }


def run_logged(command: list[str], log_path: Path, env: dict[str, str] | None = None) -> None:
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            check=False,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {command!r}")


def start_logged(command: list[str], log_path: Path) -> subprocess.Popen[str]:
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    process._ampgent_log_handle = log  # type: ignore[attr-defined]
    return process


def wait_logged(process: subprocess.Popen[str]) -> None:
    return_code = process.wait()
    process._ampgent_log_handle.close()  # type: ignore[attr-defined]
    if return_code != 0:
        raise RuntimeError(
            f"background command failed with exit code {return_code}: {process.args!r}"
        )


def convert_boltz_coordinate(work_dir: Path, destination: Path) -> Path:
    candidates = sorted(work_dir.rglob("*model_0.cif"))
    if not candidates:
        candidates = sorted(work_dir.rglob("*.cif"))
    if not candidates:
        raise FileNotFoundError("Boltz produced no CIF coordinate")
    source = candidates[0]
    structure = gemmi.read_structure(str(source))
    structure.write_pdb(str(destination))
    return source


def rosetta_command(
    request: Path, input_structure: Path, output: Path, work_dir: Path
) -> list[str]:
    return [
        str(ROSETTA_PYTHON),
        "-m",
        "pepagent.model_workers.rosetta_cli",
        "--request",
        str(request),
        "--input-structure",
        str(input_structure),
        "--output",
        str(output),
        "--work-dir",
        str(work_dir),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--pepglad-complex", type=Path, required=True)
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    root = Path(spec["remote_root"]).resolve()
    if ALLOWED_ROOT.resolve() not in root.parents:
        raise ValueError(f"remote root is outside the AMPgent structure tree: {root}")
    root.mkdir(parents=True, exist_ok=True)
    launch_receipt = root / "launch_receipt.json"
    completion_receipt = root / "completion_receipt.json"
    if launch_receipt.exists() or completion_receipt.exists():
        raise FileExistsError(f"exact-once receipt already exists below {root}")

    gpu_preflight = gpu2_preflight()
    target = resolve_target(args.target_manifest, spec["target"]["target_key"])
    if target["sequence_sha256"] != spec["target"]["sequence_sha256"]:
        raise ValueError("target sequence SHA-256 drifted")
    expected_complex_sha = spec["pepglad_complex_candidate"]["input_complex_sha256"]
    if sha256_file(args.pepglad_complex) != expected_complex_sha:
        raise ValueError("PepGLAD complex SHA-256 drifted")

    inputs = root / "inputs"
    logs = root / "logs"
    work = root / "work"
    results = root / "results"
    for directory in (inputs, logs, work, results):
        directory.mkdir(parents=True, exist_ok=True)
    frozen_spec = inputs / "task_spec.json"
    frozen_manifest = inputs / "target_manifest.json"
    frozen_complex = inputs / "pepglad_complex.pdb"
    shutil.copy2(args.spec, frozen_spec)
    shutil.copy2(args.target_manifest, frozen_manifest)
    shutil.copy2(args.pepglad_complex, frozen_complex)

    rosetta = spec["rosetta"]
    existing_request = inputs / "pepglad_rosetta_request.json"
    write_json(
        existing_request,
        {
            "receptor_chains": spec["pepglad_complex_candidate"]["receptor_chains"],
            "peptide_chain": spec["pepglad_complex_candidate"]["peptide_chain"],
            "nstruct": rosetta["nstruct"],
            "parallel_decoys": rosetta["parallel_decoys"],
            "seed": rosetta["seed"],
        },
    )
    boltz_request = inputs / "factorized_boltz_request.json"
    write_json(
        boltz_request,
        {
            "target_sequence": target["sequence"],
            "peptide_sequence": spec["factorized_candidate"]["sequence"],
            **spec["boltz"],
        },
    )
    factorized_request = inputs / "factorized_rosetta_request.json"
    write_json(
        factorized_request,
        {
            "receptor_chains": ["A"],
            "peptide_chain": "B",
            "nstruct": rosetta["nstruct"],
            "parallel_decoys": rosetta["parallel_decoys"],
            "seed": rosetta["seed"] + 1000,
        },
    )

    existing_output = results / "pepglad_rosetta_result.json"
    existing_command = rosetta_command(
        existing_request,
        frozen_complex,
        existing_output,
        work / "pepglad-rosetta",
    )
    boltz_output = results / "factorized_boltz_result.json"
    boltz_work = work / "factorized-boltz"
    boltz_command = [
        str(GPU_PYTHON),
        "-m",
        "pepagent.model_workers.boltz2_cli",
        "--request",
        str(boltz_request),
        "--output",
        str(boltz_output),
        "--work-dir",
        str(boltz_work),
        "--cache-dir",
        str(BOLTZ_CACHE),
    ]
    write_json(
        launch_receipt,
        {
            "schema_version": "ampgent.synth-gpu2-rosetta-launch.1",
            "status": "running",
            "launched_at": utc_now(),
            "pid": os.getpid(),
            "host": spec["host"],
            "gpu_preflight": gpu_preflight,
            "task_spec_sha256": sha256_file(frozen_spec),
            "target_manifest_sha256": sha256_file(frozen_manifest),
            "pepglad_complex_sha256": sha256_file(frozen_complex),
            "boltz_adapter_sha256": (
                "955a0695cc2c230a4f4545abb7f6636138bb0f9463b0f57e9090ad31083bcca4"
            ),
            "rosetta_adapter_sha256": (
                "72df05f2f02fc9f4d591de7c6a03999a1e56d19c7f26dcbc7ac9730f680b7b36"
            ),
            "commands": {
                "pepglad_rosetta": existing_command,
                "factorized_boltz": boltz_command,
            },
            "md_started": False,
        },
    )

    stage_status: dict[str, Any] = {}
    existing_process = start_logged(existing_command, logs / "pepglad_rosetta.log")
    stage_status["pepglad_rosetta_pid"] = existing_process.pid
    factorized_output = results / "factorized_rosetta_result.json"
    factorized_coordinate = inputs / "factorized_boltz_model_0.pdb"
    try:
        gpu_env = dict(os.environ)
        gpu_env["CUDA_VISIBLE_DEVICES"] = "2"
        run_logged(boltz_command, logs / "factorized_boltz.log", gpu_env)
        source_cif = convert_boltz_coordinate(boltz_work, factorized_coordinate)
        stage_status["factorized_boltz"] = {
            "status": "succeeded",
            "result_sha256": sha256_file(boltz_output),
            "source_coordinate": str(source_cif.relative_to(root)),
            "source_coordinate_sha256": sha256_file(source_cif),
            "pdb_sha256": sha256_file(factorized_coordinate),
        }
        factorized_command = rosetta_command(
            factorized_request,
            factorized_coordinate,
            factorized_output,
            work / "factorized-rosetta",
        )
        stage_status["factorized_rosetta_command"] = factorized_command
        run_logged(factorized_command, logs / "factorized_rosetta.log")
        stage_status["factorized_rosetta"] = {
            "status": "succeeded",
            "result_sha256": sha256_file(factorized_output),
        }
    except Exception as error:  # preserve the independent PepGLAD dG result
        stage_status["factorized_lane"] = {"status": "failed", "error": repr(error)}

    try:
        wait_logged(existing_process)
        stage_status["pepglad_rosetta"] = {
            "status": "succeeded",
            "result_sha256": sha256_file(existing_output),
        }
    except Exception as error:
        stage_status["pepglad_rosetta"] = {"status": "failed", "error": repr(error)}

    successful_results: dict[str, Any] = {}
    for key, path in (
        ("pepglad_rosetta", existing_output),
        ("factorized_rosetta", factorized_output),
    ):
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            successful_results[key] = {
                "primary_dG_separated_reu": payload["primary_dG_separated_reu"],
                "minimum_dG_separated_reu": payload["dG_separated_reu"]["minimum"],
                "nstruct": payload["nstruct"],
                "interface": payload["interface"],
                "result_sha256": sha256_file(path),
            }
    final_status = "succeeded" if len(successful_results) == 2 else (
        "partial" if successful_results else "failed"
    )
    write_json(
        completion_receipt,
        {
            "schema_version": "ampgent.synth-gpu2-rosetta-completion.1",
            "status": final_status,
            "completed_at": utc_now(),
            "stages": stage_status,
            "results": successful_results,
            "md_started": False,
        },
    )
    if final_status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
