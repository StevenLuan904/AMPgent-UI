from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from pepagent.hemopi2_v27_worker import REQUIRED_ENVIRONMENT


def _run_fresh_worker(root: Path) -> bytes:
    runtime = root / "var/validator-runtimes/hemopi2-v26-py311/Scripts/python.exe"
    worker = root / "src/pepagent/hemopi2_v27_worker.py"
    if not runtime.is_file() or not worker.is_file():
        raise FileNotFoundError("v27 runtime or worker is missing")
    environment = os.environ.copy()
    environment.update(REQUIRED_ENVIRONMENT)
    environment["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [str(runtime), str(worker)],
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"v27 smoke worker failed with exit code {result.returncode}: "
            f"{result.stderr.decode(errors='replace')}"
        )
    if result.stderr:
        raise RuntimeError("v27 smoke worker emitted stderr")
    if not result.stdout.endswith(b"\n"):
        raise RuntimeError("v27 smoke worker output is not canonical newline JSON")
    return result.stdout


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    manifest = root / "config/benchmarks/amp_designer_safety_validation_v27.yaml"
    marker = "\nexecution_status: smoke_authorized\n"
    if marker not in f"\n{manifest.read_text(encoding='utf-8')}":
        raise RuntimeError("v27 smoke is not authorized by the current execution status")
    first = _run_fresh_worker(root)
    second = _run_fresh_worker(root)
    if first != second:
        raise RuntimeError("v27 repeated fresh-process smoke output bytes differ")
    first_sha = hashlib.sha256(first).hexdigest()
    second_sha = hashlib.sha256(second).hexdigest()
    sys.stdout.buffer.write(first)
    print(f"smoke_sha256={first_sha}")
    print(f"repeat_smoke_sha256={second_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
