"""Continuously run sparse-frame MM/GBSA for completed Pool-A trajectories."""

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
    p.add_argument("--runner", type=Path, required=True)
    p.add_argument("--amberhome", type=Path, required=True)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--poll-seconds", type=int, default=120)
    return p.parse_args()


def calculate(a, candidate):
    out = candidate / "analysis/mmgbsa"
    out.mkdir(parents=True, exist_ok=True)
    if (out / "mmgbsa_analysis.json").exists():
        return
    cmd = [
        a.python,
        str(a.runner),
        "--topology-pdb",
        str(candidate / "prepared_solvated.pdb"),
        "--trajectory",
        str(candidate / "production.dcd"),
        "--output-dir",
        str(out),
        "--amberhome",
        str(a.amberhome),
        "--startframe",
        "501",
        "--interval",
        "125",
    ]
    with (out / "runner.log").open("a") as log:
        done = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    if done.returncode:
        (out / "mmgbsa.failed").write_text(str(done.returncode))


def main():
    a = cli()
    active = set()
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        while True:
            active = {f for f in active if not f.done()}
            busy = {getattr(f, "candidate", None) for f in active}
            for manifest in a.results_root.glob("*/*/manifest.json"):
                c = manifest.parent
                if len(active) >= a.workers:
                    break
                if c in busy or (c / "analysis/mmgbsa/mmgbsa_analysis.json").exists():
                    continue
                f = pool.submit(calculate, a, c)
                f.candidate = c
                active.add(f)
                busy.add(c)
            time.sleep(a.poll_seconds)


if __name__ == "__main__":
    main()
