"""Split matched-source evidence into explicit arm materialization inputs."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise ValueError(f"empty arm input: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(scores: Path, challenger: Path, output_dir: Path) -> None:
    score_rows = _rows(scores)
    challenger_rows = _rows(challenger)
    challenger_by_sha = {row["sequence_sha256"]: row for row in challenger_rows}
    if len(challenger_by_sha) != len(challenger_rows):
        raise ValueError("challenger sequence identities are duplicated")
    for row in score_rows:
        if row["sequence_sha256"] not in challenger_by_sha:
            raise ValueError(f"challenger coverage missing: {row['sequence_sha256']}")
    for source in ("PepGLAD", "PepFlow"):
        arm_rows = [dict(row, source=source) for row in score_rows if row["source_arm"] == source]
        arm_challenger = [
            challenger_by_sha[row["sequence_sha256"]]
            for row in arm_rows
        ]
        _write(output_dir / f"{source.lower()}_candidate_scores.csv", arm_rows)
        _write(output_dir / f"{source.lower()}_challenger.csv", arm_challenger)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--challenger", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    run(args.scores, args.challenger, args.output_dir)


if __name__ == "__main__":
    main()
