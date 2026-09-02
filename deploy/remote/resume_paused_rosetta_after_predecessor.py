from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ALLOWED_ROOT = Path("/data1/huangyueshan/pepagent/data/run-cache")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def validate_root(path: Path) -> Path:
    resolved = path.resolve()
    if ALLOWED_ROOT.resolve() not in resolved.parents:
        raise ValueError(f"root is outside the AMPgent run cache: {resolved}")
    return resolved


def process_identity(pid: int) -> tuple[str, str] | None:
    process_root = Path("/proc") / str(pid)
    try:
        stat = (process_root / "stat").read_text(encoding="utf-8")
        state = stat[stat.rfind(") ") + 2]
        command = (process_root / "cmdline").read_bytes().replace(b"\0", b" ").decode()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return state, command.strip()


def process_uses_root(command: str, root: Path) -> bool:
    return str(root) in command


def processes_using_root(root: Path, *, ignored_pids: set[int]) -> list[int]:
    matches: list[int] = []
    for entry in Path("/proc").glob("[0-9]*"):
        pid = int(entry.name)
        if pid in ignored_pids:
            continue
        identity = process_identity(pid)
        if identity is not None and process_uses_root(identity[1], root):
            matches.append(pid)
    return sorted(matches)


def gpu_is_idle(index: int, *, ignored_declaration_pids: set[int]) -> bool:
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
    memory_used, utilization = (int(value.strip()) for value in query.split(","))
    compute_pids = subprocess.run(
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
    expected = f"CUDA_VISIBLE_DEVICES={index}".encode()
    declarations: list[int] = []
    for environ in Path("/proc").glob("[0-9]*/environ"):
        try:
            declared = expected in environ.read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        pid = int(environ.parent.name)
        if declared and pid not in ignored_declaration_pids:
            declarations.append(pid)
    return memory_used <= 256 and utilization <= 5 and not compute_pids and not declarations


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run(args: argparse.Namespace) -> None:
    predecessor_root = validate_root(args.predecessor_root)
    successor_root = validate_root(args.successor_root)
    receipt = args.receipt.resolve()
    if successor_root not in receipt.parents:
        raise ValueError("receipt must stay inside the resumed batch root")
    base = {
        "schema_version": "ampgent.rosetta-balanced-resume-watcher.1",
        "watcher_pid": os.getpid(),
        "predecessor_pid": args.predecessor_pid,
        "predecessor_root": str(predecessor_root),
        "successor_pid": args.successor_pid,
        "successor_root": str(successor_root),
        "predecessor_observer_pid": args.predecessor_observer_pid,
        "gpu_indices": args.gpu_indices,
        "source_revision": args.source_revision,
        "action": "resume_existing_checkpoint_only",
        "md_started": False,
    }
    while True:
        successor = process_identity(args.successor_pid)
        if successor is None or not process_uses_root(successor[1], successor_root):
            write_receipt(receipt, {**base, "status": "failed_closed_successor_identity"})
            raise RuntimeError("successor PID identity changed or disappeared")
        if successor[0] not in {"T", "t"}:
            write_receipt(receipt, {**base, "status": "already_resumed", "observed_at": utc_now()})
            return

        predecessor = process_identity(args.predecessor_pid)
        if predecessor is not None and not process_uses_root(predecessor[1], predecessor_root):
            write_receipt(receipt, {**base, "status": "failed_closed_predecessor_identity"})
            raise RuntimeError("predecessor PID identity changed")
        observer = process_identity(args.predecessor_observer_pid)
        if observer is not None and (
            not process_uses_root(observer[1], predecessor_root)
            or "run_rosetta_receipt_ingester.py" not in observer[1]
        ):
            write_receipt(receipt, {**base, "status": "failed_closed_observer_identity"})
            raise RuntimeError("predecessor observer PID identity changed")
        remaining = processes_using_root(
            predecessor_root,
            ignored_pids={
                os.getpid(),
                args.predecessor_pid,
                args.predecessor_observer_pid,
            },
        )
        predecessor_terminal = predecessor is None and not remaining
        gpu_idle = predecessor_terminal and all(
            gpu_is_idle(index, ignored_declaration_pids={args.successor_pid})
            for index in args.gpu_indices
        )
        write_receipt(
            receipt,
            {
                **base,
                "status": "resuming" if gpu_idle else "waiting",
                "observed_at": utc_now(),
                "predecessor_terminal": predecessor_terminal,
                "remaining_predecessor_processes": remaining,
                "gpu_idle": gpu_idle,
            },
        )
        if gpu_idle:
            os.kill(args.successor_pid, signal.SIGCONT)
            time.sleep(1)
            resumed = process_identity(args.successor_pid)
            resumed_ok = (
                resumed is not None
                and process_uses_root(resumed[1], successor_root)
                and resumed[0] not in {"T", "t"}
            )
            write_receipt(
                receipt,
                {
                    **base,
                    "status": "resumed" if resumed_ok else "failed_closed_resume_verify",
                    "observed_at": utc_now(),
                    "predecessor_terminal": True,
                    "remaining_predecessor_processes": [],
                    "gpu_idle_before_resume": True,
                },
            )
            if not resumed_ok:
                raise RuntimeError("SIGCONT verification failed")
            return
        time.sleep(args.poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predecessor-pid", type=int, required=True)
    parser.add_argument("--predecessor-root", type=Path, required=True)
    parser.add_argument("--predecessor-observer-pid", type=int, required=True)
    parser.add_argument("--successor-pid", type=int, required=True)
    parser.add_argument("--successor-root", type=Path, required=True)
    parser.add_argument("--gpu-indices", type=int, nargs="+", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()
    if sorted(set(args.gpu_indices)) != [0, 1]:
        raise ValueError("this watcher is restricted to .19 GPU0/GPU1")
    if args.poll_seconds < 30:
        raise ValueError("poll interval must be at least 30 seconds")
    run(args)


if __name__ == "__main__":
    main()
