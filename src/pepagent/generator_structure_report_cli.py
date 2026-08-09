from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from pathlib import Path

from pepagent.generator_structure_report import (
    CANDIDATE_COLUMNS,
    build_candidate_rows,
    build_summary_rows,
    render_csv,
    sha256_bytes,
    summary_columns,
)


def _load_cohort(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_evidence(api_base: str, run_id: str) -> dict[str, object]:
    url = f"{api_base.rstrip('/')}/v1/runs/{run_id}/evidence"
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the frozen v31 structure comparison")
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--api-base", default="http://127.0.0.1:8080")
    parser.add_argument("--candidate-output", type=Path, required=True)
    parser.add_argument("--seed-summary-output", type=Path, required=True)
    parser.add_argument("--generator-summary-output", type=Path, required=True)
    args = parser.parse_args()

    candidate_rows = build_candidate_rows(
        _load_cohort(args.cohort), _load_evidence(args.api_base, args.run_id)
    )
    seed_group = ("generator_id", "generator_seed")
    generator_group = ("generator_id",)
    outputs = {
        args.candidate_output: render_csv(candidate_rows, CANDIDATE_COLUMNS),
        args.seed_summary_output: render_csv(
            build_summary_rows(candidate_rows, seed_group), summary_columns(seed_group)
        ),
        args.generator_summary_output: render_csv(
            build_summary_rows(candidate_rows, generator_group),
            summary_columns(generator_group),
        ),
    }
    manifest: dict[str, object] = {"run_id": args.run_id, "row_counts": {}, "sha256": {}}
    for path, payload in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        manifest["row_counts"][str(path)] = payload.count(b"\n") - 1  # type: ignore[index]
        manifest["sha256"][str(path)] = sha256_bytes(payload)  # type: ignore[index]
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
