from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from pepagent.autoresearch_quality_diversity import (
    build_quality_diversity_archive,
    candidate_from_score_row,
)
from pepagent.provenance.hashing import sha256_file, sha256_json


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-csv", type=Path, required=True)
    parser.add_argument("--prior-csv", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    batch = [candidate_from_score_row(row) for row in _rows(args.batch_csv)]
    prior = [
        candidate_from_score_row(row)
        for path in args.prior_csv
        for row in _rows(path)
    ]
    state = build_quality_diversity_archive(prior, batch)
    payload = state.model_dump(mode="json")
    payload["batch_csv_sha256"] = sha256_file(args.batch_csv)
    payload["prior_csv_sha256s"] = [sha256_file(path) for path in args.prior_csv]
    payload["historical_run_modified"] = False
    payload["payload_sha256"] = sha256_json(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
