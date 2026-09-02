"""Continuously refresh compact Pool-A MD candidate and target summaries."""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path


def cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True)
    parser.add_argument("--summarizer", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=int, default=300)
    return parser.parse_args()


def refresh(args: argparse.Namespace) -> int:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        args.python,
        str(args.summarizer),
        "--snapshot",
        str(args.snapshot),
        "--evidence-root",
        str(args.evidence_root),
        "--output-dir",
        str(args.output_dir),
    ]
    with (args.output_dir / "supervisor.log").open("a", encoding="utf-8") as log:
        completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    return completed.returncode


def main() -> None:
    args = cli()
    while True:
        code = refresh(args)
        (args.output_dir / "last_exit_code").write_text(str(code), encoding="utf-8")
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
