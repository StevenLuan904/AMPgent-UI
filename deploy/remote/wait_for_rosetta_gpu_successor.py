from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GPU_PYTHON = Path("/sdd_data/pepagent/envs/gpu-worker-py311-v1/bin/python")
ALLOWED_STRUCTURE_ROOT = Path("/sdd_data/pepagent/ampgent/structure")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def gpu_is_strictly_idle(index: int) -> bool:
    query = subprocess.run(
        [
            "nvidia-smi",
            "-i",
            str(index),
            "--query-gpu=memory.used,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    fields = [value.strip() for value in query.split(",")]
    processes = subprocess.run(
        [
            "nvidia-smi",
            "-i",
            str(index),
            "--query-compute-apps=pid",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    declarations: list[str] = []
    expected = f"CUDA_VISIBLE_DEVICES={index}".encode()
    for environ in Path("/proc").glob("[0-9]*/environ"):
        try:
            values = environ.read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if expected in values:
            declarations.append(environ.parent.name)
    return (
        len(fields) == 2
        and int(fields[0]) <= 256
        and int(fields[1]) <= 5
        and not processes
        and not declarations
    )


def run(args: argparse.Namespace) -> None:
    predecessor_root = args.predecessor_root.resolve()
    successor_root = args.successor_root.resolve()
    if ALLOWED_STRUCTURE_ROOT.resolve() not in predecessor_root.parents:
        raise ValueError("predecessor root is outside the AMPgent structure ledger")
    if ALLOWED_STRUCTURE_ROOT.resolve() not in successor_root.parents:
        raise ValueError("successor root is outside the AMPgent structure ledger")
    if successor_root.exists():
        raise FileExistsError(f"successor exact-once root already exists: {successor_root}")
    base_receipt = {
        "schema_version": "ampgent.autoresearch-rosetta-gpu-successor-wait.1",
        "pid": os.getpid(),
        "predecessor_root": str(predecessor_root),
        "successor_root": str(successor_root),
        "successor_candidates_sha256": sha256_file(args.candidates),
        "target_manifest_sha256": sha256_file(args.target_manifest),
        "runner_sha256": sha256_file(args.runner),
        "gpu_indices": args.gpu_indices,
        "poll_seconds": args.poll_seconds,
        "md_started": False,
    }
    predecessor_count: int | None = None
    while True:
        if successor_root.exists():
            write_json(
                args.queue_receipt,
                {
                    **base_receipt,
                    "status": "failed_closed_successor_root_exists",
                    "observed_at": utc_now(),
                },
            )
            raise FileExistsError(f"successor exact-once root appeared: {successor_root}")
        predecessor_launch = predecessor_root / "launch_receipt.json"
        if predecessor_count is None:
            if not predecessor_launch.exists():
                write_json(
                    args.queue_receipt,
                    {
                        **base_receipt,
                        "status": "waiting_for_predecessor_launch",
                        "observed_at": utc_now(),
                        "predecessor_candidate_count": None,
                        "predecessor_boltz_succeeded": 0,
                        "predecessor_boltz_failed": 0,
                        "predecessor_boltz_terminal": 0,
                    },
                )
                time.sleep(args.poll_seconds)
                continue
            launch = json.loads(predecessor_launch.read_text(encoding="utf-8"))
            predecessor_count = int(launch["candidate_count"])
        boltz_count = sum(
            1
            for _ in predecessor_root.glob("candidates/*/*/results/boltz_result.json")
        )
        boltz_failed = 0
        for receipt_path in predecessor_root.glob("candidates/*/*/completion_receipt.json"):
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            boltz_failed += int(
                receipt.get("status") == "failed" and receipt.get("failed_stage") == "boltz"
            )
        boltz_terminal = boltz_count + boltz_failed
        gpu_idle = boltz_terminal == predecessor_count and all(
            gpu_is_strictly_idle(index) for index in args.gpu_indices
        )
        write_json(
            args.queue_receipt,
            {
                **base_receipt,
                "status": "dispatching" if gpu_idle else "waiting",
                "observed_at": utc_now(),
                "predecessor_candidate_count": predecessor_count,
                "predecessor_boltz_succeeded": boltz_count,
                "predecessor_boltz_failed": boltz_failed,
                "predecessor_boltz_terminal": boltz_terminal,
            },
        )
        if gpu_idle:
            command = [
                str(GPU_PYTHON),
                str(args.runner.resolve()),
                "--candidates",
                str(args.candidates.resolve()),
                "--target-manifest",
                str(args.target_manifest.resolve()),
                "--root",
                str(successor_root),
                "--gpu-indices",
                *(str(index) for index in args.gpu_indices),
                "--cpu-workers",
                str(args.cpu_workers),
                "--nstruct",
                "200",
                "--parallel-decoys",
                "1",
                "--seed",
                str(args.seed),
            ]
            os.chdir(args.runner.resolve().parent)
            os.execv(command[0], command)
        time.sleep(args.poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predecessor-root", type=Path, required=True)
    parser.add_argument("--successor-root", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--queue-receipt", type=Path, required=True)
    parser.add_argument("--gpu-indices", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--cpu-workers", type=int, default=6)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()
    if sorted(set(args.gpu_indices)) != [1, 2, 3]:
        raise ValueError("successor watcher is restricted to authorized synth GPUs 1,2,3")
    if not 1 <= args.cpu_workers <= 6:
        raise ValueError("CPU worker count must be within 1..6")
    if args.poll_seconds < 30:
        raise ValueError("poll interval must be at least 30 seconds")
    run(args)


if __name__ == "__main__":
    main()
