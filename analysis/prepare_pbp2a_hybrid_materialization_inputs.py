"""Select only the receipt-backed PBP2a QD winners for materialization."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(report_dir: Path, output_dir: Path) -> dict[str, Any]:
    scores_path = report_dir / "candidate_scores_calibrated.csv"
    challenger_path = report_dir / "challenger" / "challenger_review.csv"
    qd_path = report_dir / "qd_candidates.csv"
    score_rows = _read_csv(scores_path)
    challenger_rows = _read_csv(challenger_path)
    qd_rows = _read_csv(qd_path)
    winner_hashes = [
        row["sequence_sha256"]
        for row in qd_rows
        if row.get("contribution") == "empty_cell" and row.get("new_cell") == "true"
    ]
    if len(winner_hashes) != 4 or len(set(winner_hashes)) != 4:
        raise ValueError("expected four QD new-cell winners")
    score_by_hash = {row["sequence_sha256"]: row for row in score_rows}
    challenger_by_hash = {row["sequence_sha256"]: row for row in challenger_rows}
    selected_scores = [score_by_hash[digest] for digest in winner_hashes]
    selected_challengers = [challenger_by_hash[digest] for digest in winner_hashes]
    if any(
        not str(row.get("formal_12_complete")).casefold() == "true"
        or str(row.get("display_eligible")).casefold() != "true"
        or int(row.get("activity_model_support_count_calibrated") or 0) < 2
        for row in selected_scores
    ):
        raise ValueError("selected row failed formal/display/support gate")
    if any(
        row.get("challenger_conflict_status", row.get("conflict_status")) != "no_conflict"
        for row in selected_challengers
    ):
        raise ValueError("selected row failed HemoPI2 no-conflict gate")
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_score_path = output_dir / "candidate_scores.csv"
    with selected_score_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=score_rows[0].keys())
        writer.writeheader()
        writer.writerows(selected_scores)
    selected_challenger_path = output_dir / "challenger_review.csv"
    with selected_challenger_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=challenger_rows[0].keys())
        writer.writeheader()
        writer.writerows(selected_challengers)
    receipt = {
        "schema_version": "ampgent.pbp2a-pepflow-hybrid.materialization-inputs.1",
        "source_report": report_dir.as_posix(),
        "selection_rule": (
            "QD new_cell + formal12 + display + calibrated_support>=2 + "
            "HemoPI2 no_conflict"
        ),
        "selected_count": len(winner_hashes),
        "selected_sequence_sha256": winner_hashes,
        "candidate_scores_sha256": _sha256(selected_score_path),
        "challenger_review_sha256": _sha256(selected_challenger_path),
        "pg_history_gate": "exact_history_receipt_zero_hits",
        "materialization_status": "ready_for_exact_once",
    }
    (output_dir / "selection_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.report_dir, args.output_dir), separators=(",", ":")))


if __name__ == "__main__":
    main()
