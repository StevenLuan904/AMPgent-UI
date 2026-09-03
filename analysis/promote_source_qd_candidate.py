"""Promote one immutable source-stage candidate into a compact materialization input.

This is deliberately a read-only promotion step: it reuses an already complete
formal-12/challenger result and a frozen QD assessment, and never recomputes a
model stage or writes PostgreSQL.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def _support(row: dict[str, str]) -> int:
    for key in (
        "activity_support_calibrated",
        "activity_model_support_count_calibrated",
        "activity_model_support_count",
    ):
        if row.get(key):
            return int(float(row[key]))
    return 0


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if path.exists():
        raise FileExistsError(f"append-only output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def promote(
    *,
    score_csv: Path,
    challenger_csv: Path,
    qd_receipt: Path,
    output_dir: Path,
    sequence_sha256: str,
    source_stage: str,
) -> dict[str, Any]:
    digest = sequence_sha256.strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("sequence_sha256 must be a lowercase SHA-256 digest")
    score_rows = [
        row for row in _read_csv(score_csv) if row.get("sequence_sha256") == digest
    ]
    if len(score_rows) != 1:
        raise ValueError("source score stage must contain exactly one selected identity")
    row = score_rows[0]
    sequence = "".join(row.get("sequence", "").split()).upper()
    if _sha256_text(sequence) != digest:
        raise ValueError("source sequence identity drifted")
    if not _true(row.get("formal_12_complete")):
        raise ValueError("selected source row is not formal-12 complete")
    if not _true(row.get("display_eligible")):
        raise ValueError("selected source row is not display eligible")
    if _support(row) < 2:
        raise ValueError("selected source row lacks calibrated activity support")
    if row.get("toxinpred3_label") != "Non-Toxin":
        raise ValueError("selected source row fails ToxinPred3 gate")
    if row.get("macrel_hemolysis_label") != "low":
        raise ValueError("selected source row fails Macrel gate")
    try:
        instability = float(row["guruprasad_instability_index"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("selected source row has invalid Guruprasad value") from exc
    if not math.isfinite(instability) or instability > 50:
        raise ValueError("selected source row fails Guruprasad gate")

    challenger_rows = [
        item
        for item in _read_csv(challenger_csv)
        if item.get("sequence_sha256") == digest
    ]
    if len(challenger_rows) != 1:
        raise ValueError("challenger stage must contain exactly one selected identity")
    challenger = challenger_rows[0]
    conflict = (
        challenger.get("challenger_conflict_status")
        or challenger.get("conflict_status")
        or ""
    )
    if conflict != "no_conflict":
        raise ValueError("selected source row has a challenger conflict")

    qd = json.loads(qd_receipt.read_text(encoding="utf-8"))
    if qd.get("sequence_sha256") != digest:
        raise ValueError("QD receipt identity drifted")
    if not qd.get("quality_eligible") or qd.get("contribution") not in {
        "empty_cell",
        "incumbent_replacement",
    }:
        raise ValueError("selected source row is not a valid QD contribution")

    output_dir.mkdir(parents=True, exist_ok=False)
    output_score = output_dir / "candidate_scores.csv"
    output_challenger = output_dir / "challenger_review.csv"
    _write_csv(output_score, [row])
    _write_csv(output_challenger, [challenger])
    receipt = {
        "schema_version": "ampgent.source-qd-candidate-promotion.1",
        "promotion_status": "ready_for_exact_pg_materialization",
        "target_key": row.get("branch_key", ""),
        "source": "PepMLM",
        "source_stage": source_stage,
        "sequence": sequence,
        "sequence_sha256": digest,
        "source_proposal_id": row.get("candidate_id", ""),
        "formal12_complete": True,
        "display_eligible": True,
        "activity_support_calibrated": _support(row),
        "toxinpred3_label": row["toxinpred3_label"],
        "macrel_hemolysis_label": row["macrel_hemolysis_label"],
        "guruprasad_instability_index": instability,
        "challenger": {"reviewed": True, "conflict_status": conflict},
        "qd": {
            "actual_cell_id": qd["actual_cell_id"],
            "contribution": qd["contribution"],
            "new_cell": bool(qd.get("new_cell")),
            "replacement": bool(qd.get("replacement")),
            "archive_sha256": qd.get("archive_sha256"),
        },
        "pg_exact_preflight": "required_before_materialization",
        "upstream_hashes": {
            "score_csv_sha256": _sha256_file(score_csv),
            "challenger_csv_sha256": _sha256_file(challenger_csv),
            "qd_receipt_sha256": _sha256_file(qd_receipt),
        },
        "model_stage_recomputed": False,
        "gpu_rosetta_md_submitted": False,
    }
    receipt_path = output_dir / "source_receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--score-csv", type=Path, required=True)
    parser.add_argument("--challenger-csv", type=Path, required=True)
    parser.add_argument("--qd-receipt", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sequence-sha256", required=True)
    parser.add_argument("--source-stage", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            promote(
                score_csv=args.score_csv,
                challenger_csv=args.challenger_csv,
                qd_receipt=args.qd_receipt,
                output_dir=args.output_dir,
                sequence_sha256=args.sequence_sha256,
                source_stage=args.source_stage,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
