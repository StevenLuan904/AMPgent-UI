"""Continuously analyze completed Pool-A MD trajectories without duplicate work."""

from __future__ import annotations

import argparse
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def cli():
    p = argparse.ArgumentParser()
    p.add_argument("--results-root", type=Path, required=True)
    p.add_argument("--python", required=True)
    p.add_argument("--analyzer", type=Path, required=True)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--poll-seconds", type=int, default=120)
    return p.parse_args()


def analyze(a, candidate: Path):
    out = candidate / "analysis"
    out.mkdir(exist_ok=True)
    if (out / "interface_analysis.json").exists():
        return
    cmd = [
        a.python,
        str(a.analyzer),
        "--topology",
        str(candidate / "prepared_solvated.pdb"),
        "--trajectory",
        str(candidate / "production.dcd"),
        "--output-dir",
        str(out),
    ]
    with (out / "interface_analysis.log").open("a") as log:
        completed = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    if completed.returncode:
        (out / "interface_analysis.failed").write_text(str(completed.returncode))


def main():
    a = cli()
    active = set()
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        while True:
            done = {f for f in active if f.done()}
            active -= done
            slots = a.workers - len(active)
            if slots:
                candidates = []
                for manifest in a.results_root.glob("*/*/manifest.json"):
                    c = manifest.parent
                    if not (c / "analysis/interface_analysis.json").exists() and not any(
                        getattr(f, "candidate", None) == c for f in active
                    ):
                        candidates.append(c)
                for c in candidates[:slots]:
                    future = pool.submit(analyze, a, c)
                    future.candidate = c
                    active.add(future)
            time.sleep(a.poll_seconds)


if __name__ == "__main__":
    main()
