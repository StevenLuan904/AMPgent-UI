from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path


def process_table() -> dict[int, tuple[int, str]]:
    table: dict[int, tuple[int, str]] = {}
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            status = proc.joinpath("status").read_text()
            ppid = int(next(line.split()[1] for line in status.splitlines() if line.startswith("PPid:")))
            command = proc.joinpath("cmdline").read_bytes().replace(b"\0", b" ").decode()
            table[int(proc.name)] = (ppid, command)
        except (FileNotFoundError, PermissionError, ProcessLookupError, StopIteration, UnicodeDecodeError):
            continue
    return table


def descendants(table: dict[int, tuple[int, str]], parent: int) -> set[int]:
    result: set[int] = set()
    frontier = {parent}
    while frontier:
        children = {pid for pid, (ppid, _cmd) in table.items() if ppid in frontier and pid not in result}
        result.update(children)
        frontier = children
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--expected-root", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    table = process_table()
    parent = table.get(args.parent_pid)
    if parent is None:
        raise SystemExit("parent process is not alive")
    allowed_runners = (
        "run_autoresearch_rosetta_batch",
        "resume_autoresearch_rosetta_coarse5.py",
        "runner.py",
    )
    if args.expected_root not in parent[1] or not any(name in parent[1] for name in allowed_runners):
        raise SystemExit("parent identity does not match the exact Rosetta batch")
    tree = descendants(table, args.parent_pid)
    observed = {str(pid): table[pid][1] for pid in sorted(tree | {args.parent_pid})}
    for pid in sorted(tree, reverse=True):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        os.kill(args.parent_pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.time() + 20
    while time.time() < deadline:
        alive = [pid for pid in tree | {args.parent_pid} if Path(f"/proc/{pid}").exists()]
        if not alive:
            break
        time.sleep(0.5)
    killed = []
    for pid in sorted(tree | {args.parent_pid}, reverse=True):
        if Path(f"/proc/{pid}").exists():
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except ProcessLookupError:
                pass
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps({
        "schema_version": "ampgent.exact-process-tree-stop.1",
        "stopped_at": datetime.now(timezone.utc).isoformat(),
        "parent_pid": args.parent_pid,
        "expected_root": args.expected_root,
        "observed_processes": observed,
        "sigkill_pids": killed,
        "files_deleted": False,
    }, indent=2, sort_keys=True) + "\n")
    print(args.receipt.read_text(), end="")


if __name__ == "__main__":
    main()
