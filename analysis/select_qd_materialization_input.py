"""Select only quality-diversity contributors for lineage materialization."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from pepagent.provenance.hashing import sha256_file, sha256_json


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def select(
    scores: Path, challenger: Path, qd: Path, output_dir: Path
) -> dict[str, object]:
    score_rows = _read(scores)
    challenger_rows = _read(challenger)
    payload = json.loads(qd.read_text(encoding="utf-8"))
    selected_ids = {
        str(item["candidate_id"])
        for item in payload.get("contributions", [])
        if item.get("contribution") in {"empty_cell", "incumbent_replacement"}
    }
    selected_scores = [
        row for row in score_rows if row.get("sequence_sha256") in selected_ids
    ]
    selected_challenger = [
        row for row in challenger_rows if row.get("sequence_sha256") in selected_ids
    ]
    if len(selected_scores) != len(selected_ids):
        raise ValueError("QD contributor is missing from calibrated score rows")
    if len(selected_challenger) != len(selected_ids):
        raise ValueError("QD contributor is missing challenger evidence")
    output_dir.mkdir(parents=True, exist_ok=False)
    score_path = output_dir / "candidate_scores.csv"
    challenger_path = output_dir / "challenger_review.csv"
    for path, rows in (
        (score_path, selected_scores),
        (challenger_path, selected_challenger),
    ):
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    receipt = {
        "schema_version": "ampgent.qd-materialization-input.1",
        "qd_path": str(qd),
        "qd_sha256": sha256_file(qd),
        "selected_candidate_count": len(selected_ids),
        "selected_sequence_sha256s": sorted(selected_ids),
        "candidate_scores_sha256": sha256_file(score_path),
        "challenger_review_sha256": sha256_file(challenger_path),
        "selection_rule": ["empty_cell", "incumbent_replacement"],
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "selection_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--challenger", type=Path, required=True)
    parser.add_argument("--qd", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    select(args.scores, args.challenger, args.qd, args.output_dir)


if __name__ == "__main__":
    main()
