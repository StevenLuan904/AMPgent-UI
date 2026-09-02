from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import gemmi

GPU_PYTHON = Path("/sdd_data/pepagent/envs/gpu-worker-py311-v1/bin/python")
ROSETTA_PYTHON = Path("/sdd_data/pepagent/envs/pyrosetta-quarterly-py311-v1/bin/python")
BOLTZ_CACHE = Path("/sdd_data/pepagent/models/boltz2/cache")
ALLOWED_ROOT = Path("/sdd_data/pepagent/ampgent/structure")
REQUIRED_TRUE = (
    "display_eligible",
    "formal_12_complete",
    "structure_queue_selected",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_rows(
    path: Path, *, allow_all_pool_missing: bool = False
) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("frozen candidate batch is empty")
    if len({(row["branch_key"], row["sequence_sha256"]) for row in rows}) != len(rows):
        raise ValueError("candidate target-sequence identities are not unique")
    if len({row["candidate_id"] for row in rows}) != len(rows):
        raise ValueError("candidate ids are not globally unique")
    branches = sorted({row["branch_key"] for row in rows})
    allowed_branches = {"acea", "angpt1", "fgf2", "gyra", "pbp2a", "vegfa"}
    if not branches or not set(branches).issubset(allowed_branches):
        raise ValueError(f"candidate branch identity drifted: {branches!r}")
    for row in rows:
        if not allow_all_pool_missing and any(
            row.get(field, "").strip().lower() != "true" for field in REQUIRED_TRUE
        ):
            raise ValueError(
                f"candidate {row.get('candidate_id')} failed a frozen hard gate"
            )
        try:
            instability = float(row.get("guruprasad_instability_index", ""))
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"candidate {row.get('candidate_id')} lacks a numeric Guruprasad instability index"
            ) from error
        if not math.isfinite(instability) or (
            not allow_all_pool_missing and instability > 50.0
        ):
            raise ValueError(
                f"candidate {row.get('candidate_id')} fails the strict Guruprasad "
                "instability <=50 hard gate"
            )
        if row.get("rosetta_dg_receipt_status") != "missing":
            raise ValueError(f"candidate {row.get('candidate_id')} is not pending Rosetta dG")
        sequence = row["sequence"].strip().upper()
        if hashlib.sha256(sequence.encode()).hexdigest() != row["sequence_sha256"]:
            raise ValueError(f"candidate {row.get('candidate_id')} sequence hash drifted")
    return sorted(rows, key=lambda row: (row["branch_key"], int(row["proposal_rank"])))


def load_targets(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    targets = {str(item["target_key"]): item for item in payload["targets"]}
    if sorted(targets) != ["acea", "angpt1", "fgf2", "gyra", "pbp2a", "vegfa"]:
        raise ValueError("target manifest does not contain the exact six branches")
    for key, item in targets.items():
        sequence = "".join(item["sequence"].split()).upper()
        if hashlib.sha256(sequence.encode()).hexdigest() != item["sequence_sha256"]:
            raise ValueError(f"target manifest hash drifted for {key}")
        item["sequence"] = sequence
    return targets


def gpu_preflight(
    indices: list[int], *, allowed_idle_declaration_pids: set[int] | None = None
) -> list[dict[str, Any]]:
    allowed_idle_declaration_pids = allowed_idle_declaration_pids or set()
    observations: list[dict[str, Any]] = []
    for index in indices:
        query = subprocess.run(
            [
                "nvidia-smi",
                "-i",
                str(index),
                "--query-gpu=index,uuid,memory.used,utilization.gpu",
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
                str(index),
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
            if f"CUDA_VISIBLE_DEVICES={index}".encode() in values:
                declarations.append(int(environ.parent.name))
        fields = [value.strip() for value in query.split(",")]
        if len(fields) != 4 or int(fields[0]) != index:
            raise RuntimeError(f"unexpected GPU query for {index}: {query!r}")
        unexpected_declarations = sorted(
            set(declarations) - allowed_idle_declaration_pids
        )
        if (
            int(fields[2]) > 256
            or int(fields[3]) > 5
            or processes
            or unexpected_declarations
        ):
            raise RuntimeError(
                f"GPU{index} is not strictly idle: memory={fields[2]}, util={fields[3]}, "
                f"processes={processes!r}, declarations={declarations!r}, "
                f"unexpected_declarations={unexpected_declarations!r}"
            )
        observations.append(
            {
                "index": index,
                "uuid": fields[1],
                "memory_used_mib": int(fields[2]),
                "utilization_percent": int(fields[3]),
                "compute_processes": [],
                "cuda_visible_devices_declarations": declarations,
            }
        )
    return observations


def run_logged(command: list[str], log_path: Path, *, env: dict[str, str] | None = None) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env)
    if completed.returncode != 0:
        raise RuntimeError(f"command exited {completed.returncode}: {command[:3]!r}")


def convert_coordinate(work_dir: Path, destination: Path) -> tuple[Path, str]:
    candidates = sorted(work_dir.rglob("*model_0.cif")) or sorted(work_dir.rglob("*.cif"))
    if not candidates:
        raise FileNotFoundError("Boltz produced no CIF coordinate")
    source = candidates[0]
    structure = gemmi.read_structure(str(source))
    structure.write_pdb(str(destination))
    return source, sha256_file(destination)


class Batch:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = args.root.resolve()
        self.allowed_root = args.allowed_root.resolve()
        if self.allowed_root not in self.root.parents:
            raise ValueError(f"batch root is outside {self.allowed_root}")
        self.rows = load_rows(
            args.candidates.resolve(),
            allow_all_pool_missing=args.allow_all_pool_missing,
        )
        self.targets = load_targets(args.target_manifest.resolve())
        self.lock = threading.Lock()
        self.counts = {
            "pending": len(self.rows),
            "boltz_succeeded": 0,
            "rosetta_succeeded": 0,
            "failed": 0,
        }

    def progress(self) -> None:
        with self.lock:
            write_json(
                self.root / "progress.json",
                {
                    "schema_version": "ampgent.autoresearch-rosetta-batch-progress.1",
                    "observed_at": utc_now(),
                    **self.counts,
                },
            )

    def candidate_root(self, row: dict[str, str]) -> Path:
        return self.root / "candidates" / row["branch_key"] / row["sequence_sha256"]

    def rosetta(self, row: dict[str, str], pdb: Path, candidate_root: Path) -> None:
        receipt = candidate_root / "completion_receipt.json"
        try:
            request = candidate_root / "inputs" / "rosetta_request.json"
            write_json(
                request,
                {
                    "receptor_chains": ["A"],
                    "peptide_chain": "B",
                    "nstruct": self.args.nstruct,
                    "parallel_decoys": self.args.parallel_decoys,
                    "seed": self.args.seed + int(row["proposal_rank"]),
                    "score_function": "ref2015",
                },
            )
            output = candidate_root / "results" / "rosetta_result.json"
            command = [
                str(self.args.rosetta_python),
                "-m",
                "pepagent.model_workers.rosetta_cli",
                "--request",
                str(request),
                "--input-structure",
                str(pdb),
                "--output",
                str(output),
                "--work-dir",
                str(candidate_root / "work" / "rosetta"),
            ]
            run_logged(command, candidate_root / "logs" / "rosetta.log")
            result = json.loads(output.read_text(encoding="utf-8"))
            if int(result["nstruct"]) != self.args.nstruct:
                raise ValueError("Rosetta result decoy count drifted")
            write_json(
                receipt,
                {
                    "schema_version": "ampgent.autoresearch-rosetta-candidate-completion.1",
                    "status": "succeeded",
                    "completed_at": utc_now(),
                    "candidate_id": row["candidate_id"],
                    "sequence_sha256": row["sequence_sha256"],
                    "target_key": row["branch_key"],
                    "nstruct": result["nstruct"],
                    "interface": result["interface"],
                    "primary_dG_separated_reu": result["primary_dG_separated_reu"],
                    "minimum_dG_separated_reu": result["dG_separated_reu"]["minimum"],
                    "result_sha256": sha256_file(output),
                    "md_started": False,
                },
            )
            with self.lock:
                self.counts["rosetta_succeeded"] += 1
                self.counts["pending"] -= 1
        except Exception as error:
            write_json(
                receipt,
                {
                    "schema_version": "ampgent.autoresearch-rosetta-candidate-completion.1",
                    "status": "failed",
                    "completed_at": utc_now(),
                    "candidate_id": row.get("candidate_id"),
                    "sequence_sha256": row.get("sequence_sha256"),
                    "error_category": type(error).__name__,
                    "md_started": False,
                },
            )
            with self.lock:
                self.counts["failed"] += 1
                self.counts["pending"] -= 1
        self.progress()

    def boltz(
        self, gpu_index: int, rows: list[dict[str, str]], pool: concurrent.futures.Executor
    ) -> list[concurrent.futures.Future[None]]:
        futures: list[concurrent.futures.Future[None]] = []
        for row in rows:
            candidate_root = self.candidate_root(row)
            candidate_root.mkdir(parents=True, exist_ok=False)
            for name in ("inputs", "logs", "work", "results"):
                (candidate_root / name).mkdir()
            write_json(
                candidate_root / "launch_receipt.json",
                {
                    "schema_version": "ampgent.autoresearch-rosetta-candidate-launch.1",
                    "status": "running",
                    "launched_at": utc_now(),
                    "candidate_id": row["candidate_id"],
                    "sequence": row["sequence"],
                    "sequence_sha256": row["sequence_sha256"],
                    "target_key": row["branch_key"],
                    "gpu_index": gpu_index,
                    "nstruct": self.args.nstruct,
                    "md_started": False,
                },
            )
            request = candidate_root / "inputs" / "boltz_request.json"
            write_json(
                request,
                {
                    "target_sequence": self.targets[row["branch_key"]]["sequence"],
                    "peptide_sequence": row["sequence"],
                    "seed": self.args.seed + int(row["proposal_rank"]),
                    "diffusion_samples": 1,
                    "recycling_steps": 3,
                    "sampling_steps": 200,
                    "use_msa_server": False,
                    "use_potentials": True,
                    "no_kernels": True,
                },
            )
            output = candidate_root / "results" / "boltz_result.json"
            work = candidate_root / "work" / "boltz"
            command = [
                str(self.args.gpu_python),
                "-m",
                "pepagent.model_workers.boltz2_cli",
                "--request",
                str(request),
                "--output",
                str(output),
                "--work-dir",
                str(work),
                "--cache-dir",
                str(self.args.boltz_cache),
            ]
            env = dict(os.environ)
            env["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
            try:
                run_logged(command, candidate_root / "logs" / "boltz.log", env=env)
                pdb = candidate_root / "inputs" / "boltz_model_0.pdb"
                source, pdb_sha256 = convert_coordinate(work, pdb)
                write_json(
                    candidate_root / "results" / "boltz_coordinate_receipt.json",
                    {
                        "schema_version": "ampgent.autoresearch-boltz-coordinate.1",
                        "source": str(source.relative_to(candidate_root)),
                        "source_sha256": sha256_file(source),
                        "pdb_sha256": pdb_sha256,
                        "boltz_result_sha256": sha256_file(output),
                    },
                )
                with self.lock:
                    self.counts["boltz_succeeded"] += 1
                self.progress()
                futures.append(pool.submit(self.rosetta, row, pdb, candidate_root))
            except Exception as error:
                write_json(
                    candidate_root / "completion_receipt.json",
                    {
                        "schema_version": "ampgent.autoresearch-rosetta-candidate-completion.1",
                        "status": "failed",
                        "completed_at": utc_now(),
                        "candidate_id": row["candidate_id"],
                        "sequence_sha256": row["sequence_sha256"],
                        "error_category": type(error).__name__,
                        "failed_stage": "boltz",
                        "md_started": False,
                    },
                )
                with self.lock:
                    self.counts["failed"] += 1
                    self.counts["pending"] -= 1
                self.progress()
        return futures

    def run(self) -> None:
        if self.root.exists():
            raise FileExistsError(f"exact-once batch root already exists: {self.root}")
        gpu_observations = gpu_preflight(
            self.args.gpu_indices,
            allowed_idle_declaration_pids=set(self.args.allowed_idle_declaration_pids),
        )
        for required_path in (
            self.args.gpu_python,
            self.args.rosetta_python,
            self.args.boltz_cache,
        ):
            if not required_path.exists():
                raise FileNotFoundError(f"required runtime path is missing: {required_path}")
        available = shutil.disk_usage(self.allowed_root).free
        if available < self.args.minimum_free_bytes:
            raise RuntimeError(f"insufficient free disk: {available} bytes")
        self.root.mkdir(parents=True)
        frozen_candidates = self.root / "inputs" / "candidates.csv"
        frozen_manifest = self.root / "inputs" / "target_manifest.json"
        frozen_candidates.parent.mkdir()
        shutil.copy2(self.args.candidates, frozen_candidates)
        shutil.copy2(self.args.target_manifest, frozen_manifest)
        write_json(
            self.root / "launch_receipt.json",
            {
                "schema_version": "ampgent.autoresearch-rosetta-batch-launch.1",
                "status": "running",
                "launched_at": utc_now(),
                "pid": os.getpid(),
                "host": self.args.host_label,
                "allowed_root": str(self.allowed_root),
                "gpu_python": str(self.args.gpu_python),
                "rosetta_python": str(self.args.rosetta_python),
                "boltz_cache": str(self.args.boltz_cache),
                "allowed_idle_declaration_pids": sorted(
                    set(self.args.allowed_idle_declaration_pids)
                ),
                "gpu_preflight": gpu_observations,
                "candidate_count": len(self.rows),
                "branch_counts": {
                    branch: sum(row["branch_key"] == branch for row in self.rows)
                    for branch in sorted({row["branch_key"] for row in self.rows})
                },
                "candidates_sha256": sha256_file(frozen_candidates),
                "target_manifest_sha256": sha256_file(frozen_manifest),
                "cpu_workers": self.args.cpu_workers,
                "nstruct": self.args.nstruct,
                "parallel_decoys": self.args.parallel_decoys,
                "md_started": False,
            },
        )
        self.progress()
        shards = [
            self.rows[index :: len(self.args.gpu_indices)]
            for index in range(len(self.args.gpu_indices))
        ]
        all_futures: list[concurrent.futures.Future[None]] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=self.args.cpu_workers
        ) as rosetta_pool:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=len(self.args.gpu_indices)
            ) as gpu_pool:
                producers = [
                    gpu_pool.submit(self.boltz, gpu, shard, rosetta_pool)
                    for gpu, shard in zip(self.args.gpu_indices, shards, strict=True)
                ]
                for producer in concurrent.futures.as_completed(producers):
                    all_futures.extend(producer.result())
            for future in concurrent.futures.as_completed(all_futures):
                future.result()
        status = (
            "succeeded"
            if self.counts["rosetta_succeeded"] == len(self.rows)
            else "partial"
        )
        write_json(
            self.root / "completion_receipt.json",
            {
                "schema_version": "ampgent.autoresearch-rosetta-batch-completion.1",
                "status": status,
                "completed_at": utc_now(),
                **self.counts,
                "md_started": False,
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--allowed-root", type=Path, default=ALLOWED_ROOT)
    parser.add_argument("--gpu-python", type=Path, default=GPU_PYTHON)
    parser.add_argument("--rosetta-python", type=Path, default=ROSETTA_PYTHON)
    parser.add_argument("--boltz-cache", type=Path, default=BOLTZ_CACHE)
    parser.add_argument("--host-label", default="192.168.99.2")
    parser.add_argument(
        "--allowed-idle-declaration-pids", type=int, nargs="*", default=[]
    )
    parser.add_argument(
        "--allowed-gpu-indices", type=int, nargs="+", default=[1, 2, 3]
    )
    parser.add_argument("--gpu-indices", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--cpu-workers", type=int, default=6)
    parser.add_argument("--nstruct", type=int, default=20)
    parser.add_argument("--parallel-decoys", type=int, default=1)
    parser.add_argument("--seed", type=int, default=202608310)
    parser.add_argument("--minimum-free-bytes", type=int, default=500 * 1024**3)
    parser.add_argument(
        "--allow-all-pool-missing",
        action="store_true",
        help=(
            "score every canonical target-sequence identity lacking Rosetta evidence; "
            "display and challenger gates remain annotations rather than admission gates"
        ),
    )
    args = parser.parse_args()
    if args.cpu_workers < 1 or args.cpu_workers > 6:
        raise ValueError("CPU worker count must be within 1..6")
    if args.nstruct not in {20, 200}:
        raise ValueError("Rosetta batches require 20 coarse-screen decoys (legacy 200 accepted)")
    if len(set(args.gpu_indices)) != len(args.gpu_indices):
        raise ValueError("GPU indices must be unique")
    allowed_gpu_indices = set(args.allowed_gpu_indices)
    if not set(args.gpu_indices).issubset(allowed_gpu_indices):
        raise ValueError("requested GPUs are outside the explicit allowed GPU set")
    Batch(args).run()


if __name__ == "__main__":
    main()
