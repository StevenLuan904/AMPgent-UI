from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import gemmi

from pepagent.provenance.hashing import sha256_file, sha256_json

NSTRUCT = 5


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def run_logged(command: list[str], log_path: Path, env: dict[str, str] | None = None) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=env)
    if completed.returncode:
        raise RuntimeError(f"command exited {completed.returncode}: {command[:3]!r}")


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or len({row["candidate_id"] for row in rows}) != len(rows):
        raise ValueError("frozen candidate identity is empty or duplicated")
    return sorted(rows, key=lambda row: (row["branch_key"], int(row["proposal_rank"])))


def load_targets(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return {str(item["target_key"]): "".join(item["sequence"].split()).upper() for item in payload["targets"]}


def gpu_preflight(indices: list[int]) -> list[dict[str, Any]]:
    observations = []
    for index in indices:
        query = subprocess.run(["nvidia-smi", "-i", str(index), "--query-gpu=index,uuid,memory.used,utilization.gpu", "--format=csv,noheader,nounits"], check=True, capture_output=True, text=True).stdout.strip()
        processes = subprocess.run(["nvidia-smi", "-i", str(index), "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout.strip()
        fields = [part.strip() for part in query.split(",")]
        if processes or int(fields[2]) > 256 or int(fields[3]) > 5:
            raise RuntimeError(f"GPU{index} is not idle: {query!r}; {processes!r}")
        observations.append({"index": index, "uuid": fields[1], "memory_used_mib": int(fields[2]), "utilization_percent": int(fields[3])})
    return observations


def valid_checkpoint(work: Path, index: int) -> bool:
    metric_path = work / "decoys" / f"decoy_{index:04d}.json"
    structure_path = work / "decoys" / f"decoy_{index:04d}.pdb"
    if not metric_path.is_file() or not structure_path.is_file():
        return False
    try:
        metric = json.loads(metric_path.read_text())
        return metric.get("dG_separated") is not None
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


class ResumeBatch:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.root = args.root.resolve()
        self.rows = load_rows(self.root / "inputs" / "candidates.csv")
        self.targets = load_targets(self.root / "inputs" / "target_manifest.json")
        self.lock = threading.Lock()
        self.counts = {"pending": len(self.rows), "boltz_succeeded": 0, "rosetta_succeeded": 0, "failed": 0, "reused_decoys": 0, "new_decoys_required": 0}

    def candidate(self, row: dict[str, str]) -> Path:
        return self.root / "candidates" / row["branch_key"] / row["sequence_sha256"]

    def receipt(self, row: dict[str, str]) -> Path:
        return self.candidate(row) / "protocols" / "coarse5" / "completion_receipt.json"

    def succeeded(self, row: dict[str, str]) -> bool:
        path = self.receipt(row)
        if not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text())
            return payload.get("status") == "succeeded" and int(payload.get("nstruct", 0)) == NSTRUCT
        except (OSError, ValueError, json.JSONDecodeError):
            return False

    def progress(self) -> None:
        write_json(self.root / "coarse5_progress.json", {"schema_version": "ampgent.autoresearch-rosetta-coarse5-progress.1", "observed_at": utc_now(), **self.counts})

    def seed_work(self, row: dict[str, str], request: dict[str, Any], pdb: Path) -> tuple[Path, int]:
        candidate = self.candidate(row)
        legacy = candidate / "work" / "rosetta"
        work = candidate / "work" / "rosetta_coarse5"
        work.mkdir(parents=True, exist_ok=True)
        reused = 0
        legacy_prepacked = legacy / "input.prepacked.pdb"
        if legacy_prepacked.is_file():
            link_or_copy(legacy_prepacked, work / "input.prepacked.pdb")
        for index in range(1, NSTRUCT + 1):
            if valid_checkpoint(legacy, index):
                link_or_copy(legacy / "decoys" / f"decoy_{index:04d}.pdb", work / "decoys" / f"decoy_{index:04d}.pdb")
                link_or_copy(legacy / "decoys" / f"decoy_{index:04d}.json", work / "decoys" / f"decoy_{index:04d}.json")
                reused += 1
        manifest = work / "run_manifest.json"
        if not manifest.exists():
            payload: dict[str, Any] = {"schema_version": "ampgent.rosetta-resumable-run.1", "request_sha256": sha256_json(request), "input_structure_sha256": sha256_file(pdb)}
            prepacked = work / "input.prepacked.pdb"
            if prepacked.is_file():
                payload["prepacked_input_sha256"] = sha256_file(prepacked)
            write_json(manifest, payload)
        return work, reused

    def rosetta(self, row: dict[str, str]) -> None:
        if self.succeeded(row):
            return
        candidate = self.candidate(row)
        protocol = candidate / "protocols" / "coarse5"
        receipt = self.receipt(row)
        try:
            pdb = candidate / "inputs" / "boltz_model_0.pdb"
            request_payload = {"receptor_chains": ["A"], "peptide_chain": "B", "nstruct": NSTRUCT, "parallel_decoys": 1, "seed": self.args.seed + int(row["proposal_rank"]), "score_function": "ref2015"}
            request = protocol / "inputs" / "rosetta_request.json"
            write_json(request, request_payload)
            work, reused = self.seed_work(row, request_payload, pdb)
            output = protocol / "results" / "rosetta_result.json"
            run_logged([str(self.args.rosetta_python), "-m", "pepagent.model_workers.rosetta_cli", "--request", str(request), "--input-structure", str(pdb), "--output", str(output), "--work-dir", str(work)], protocol / "logs" / "rosetta.log")
            result = json.loads(output.read_text())
            if int(result["nstruct"]) != NSTRUCT:
                raise ValueError("coarse5 result count drifted")
            write_json(receipt, {"schema_version": "ampgent.autoresearch-rosetta-candidate-completion.1", "protocol_version": "coarse5-v1", "status": "succeeded", "completed_at": utc_now(), "candidate_id": row["candidate_id"], "sequence_sha256": row["sequence_sha256"], "target_key": row["branch_key"], "nstruct": NSTRUCT, "interface": result["interface"], "primary_dG_separated_reu": result["primary_dG_separated_reu"], "minimum_dG_separated_reu": result["dG_separated_reu"]["minimum"], "primary_aggregation": "median_dG_separated_of_all_5_decoys", "result_relative_path": "results/rosetta_result.json", "result_sha256": sha256_file(output), "reused_decoy_count": reused, "new_decoy_count": NSTRUCT - reused, "md_started": False})
            with self.lock:
                self.counts["pending"] -= 1
                self.counts["rosetta_succeeded"] += 1
                self.counts["reused_decoys"] += reused
                self.counts["new_decoys_required"] += NSTRUCT - reused
        except Exception as error:
            write_json(protocol / "failure_receipt.json", {"schema_version": "ampgent.autoresearch-rosetta-coarse5-failure.1", "failed_at": utc_now(), "candidate_id": row["candidate_id"], "error_category": type(error).__name__, "error": str(error)[-1000:]})
            with self.lock:
                self.counts["pending"] -= 1
                self.counts["failed"] += 1
        self.progress()

    def ensure_boltz(self, gpu: int, row: dict[str, str]) -> None:
        candidate = self.candidate(row)
        candidate.mkdir(parents=True, exist_ok=True)
        for name in ("inputs", "logs", "work", "results"):
            (candidate / name).mkdir(exist_ok=True)
        pdb = candidate / "inputs" / "boltz_model_0.pdb"
        if pdb.is_file():
            return
        request_payload = {"target_sequence": self.targets[row["branch_key"]], "peptide_sequence": row["sequence"], "seed": self.args.seed + int(row["proposal_rank"]), "diffusion_samples": 1, "recycling_steps": 3, "sampling_steps": 200, "use_msa_server": False, "use_potentials": True, "no_kernels": True}
        request = candidate / "inputs" / "boltz_request.json"
        write_json(request, request_payload)
        output = candidate / "results" / "boltz_result.json"
        work = candidate / "work" / "boltz"
        env = dict(os.environ)
        env["CUDA_VISIBLE_DEVICES"] = str(gpu)
        run_logged([str(self.args.gpu_python), "-m", "pepagent.model_workers.boltz2_cli", "--request", str(request), "--output", str(output), "--work-dir", str(work), "--cache-dir", str(self.args.boltz_cache)], candidate / "logs" / "boltz_coarse5.log", env)
        sources = sorted(work.rglob("*model_0.cif")) or sorted(work.rglob("*.cif"))
        if not sources:
            raise FileNotFoundError("Boltz produced no CIF")
        gemmi.read_structure(str(sources[0])).write_pdb(str(pdb))
        with self.lock:
            self.counts["boltz_succeeded"] += 1
        self.progress()

    def producer(self, gpu: int, rows: list[dict[str, str]], pool: concurrent.futures.Executor) -> list[concurrent.futures.Future[None]]:
        futures = []
        for row in rows:
            if self.succeeded(row):
                continue
            try:
                self.ensure_boltz(gpu, row)
                futures.append(pool.submit(self.rosetta, row))
            except Exception as error:
                write_json(self.candidate(row) / "protocols" / "coarse5" / "failure_receipt.json", {"failed_at": utc_now(), "candidate_id": row["candidate_id"], "failed_stage": "boltz", "error_category": type(error).__name__, "error": str(error)[-1000:]})
                with self.lock:
                    self.counts["pending"] -= 1
                    self.counts["failed"] += 1
                self.progress()
        return futures

    def run(self) -> None:
        launch = self.root / "coarse5_transition" / "launch_receipt.json"
        prior_launch = None
        if launch.exists():
            prior_launch = json.loads(launch.read_text())
            prior_pid = int(prior_launch.get("pid", 0))
            if prior_pid and Path(f"/proc/{prior_pid}").exists():
                raise FileExistsError("coarse5 transition is already running")
        already = sum(self.succeeded(row) for row in self.rows)
        self.counts["pending"] -= already
        observations = gpu_preflight(self.args.gpu_indices)
        launch_payload = {"schema_version": "ampgent.autoresearch-rosetta-coarse5-launch.1", "launched_at": utc_now(), "pid": os.getpid(), "host": self.args.host_label, "root": str(self.root), "candidate_count": len(self.rows), "already_complete": already, "nstruct": NSTRUCT, "primary_aggregation": "median_dG_separated_of_all_5_decoys", "gpu_preflight": observations, "cpu_workers": self.args.cpu_workers, "files_deleted": False}
        if prior_launch is None:
            write_json(launch, launch_payload)
        else:
            attempts = self.root / "coarse5_transition" / "restart_attempts"
            write_json(attempts / f"{os.getpid()}.json", {**launch_payload, "supersedes_pid": prior_launch.get("pid")})
        self.progress()
        shards = [self.rows[index::len(self.args.gpu_indices)] for index in range(len(self.args.gpu_indices))]
        futures: list[concurrent.futures.Future[None]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.args.cpu_workers) as rosetta_pool:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(self.args.gpu_indices)) as gpu_pool:
                producers = [gpu_pool.submit(self.producer, gpu, shard, rosetta_pool) for gpu, shard in zip(self.args.gpu_indices, shards)]
                for producer in concurrent.futures.as_completed(producers):
                    futures.extend(producer.result())
            for future in concurrent.futures.as_completed(futures):
                future.result()
        write_json(self.root / "coarse5_transition" / "completion_receipt.json", {"schema_version": "ampgent.autoresearch-rosetta-coarse5-batch-completion.1", "completed_at": utc_now(), **self.counts})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--host-label", required=True)
    parser.add_argument("--gpu-python", type=Path, required=True)
    parser.add_argument("--rosetta-python", type=Path, required=True)
    parser.add_argument("--boltz-cache", type=Path, required=True)
    parser.add_argument("--gpu-indices", type=int, nargs="+", required=True)
    parser.add_argument("--cpu-workers", type=int, default=16)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    if args.cpu_workers not in range(1, 33):
        raise ValueError("cpu-workers must be 1..32")
    ResumeBatch(args).run()


if __name__ == "__main__":
    main()
