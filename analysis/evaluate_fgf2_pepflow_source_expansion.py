"""Evaluate the FGF2 PepFlow batch against its frozen QD archive."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from pepagent.autoresearch_quality_diversity import (
    QualityDiversityCandidate,
    behavior_vector,
    build_quality_diversity_archive,
    candidate_from_score_row,
)
from pepagent.provenance.hashing import sha256_file, sha256_json


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _archive_candidates(payload: dict) -> list[QualityDiversityCandidate]:
    return [
        QualityDiversityCandidate(
            candidate_id=item["candidate_id"],
            sequence=item["sequence"],
            behavior=behavior_vector(
                item["sequence"],
                net_charge=item["behavior"]["charge_density"] * item["behavior"]["length"],
                hydrophobicity=item["behavior"]["hydrophobicity"],
                hydrophobic_moment=item["behavior"]["hydrophobic_moment"],
            ),
            quality=float(item["quality"]),
            display_eligible=True,
            activity_support_count=2,
            hemolysis_probability=0.0,
            hemolysis_label="low",
            operator_name="frozen_archive",
        )
        for item in payload["elites"]
    ]


def evaluate(
    batch_csv: Path,
    archive_json: Path,
    challenger_csv: Path,
    output: Path,
    calibration_reference_run_id: str,
) -> dict:
    rows = _rows(batch_csv)
    reviews = {
        row["sequence_sha256"]: row for row in _rows(challenger_csv)
    }
    eligible = [
        row
        for row in rows
        if row.get("display_eligible", "").casefold() == "true"
        and int(row.get("activity_model_support_count_calibrated", "0")) >= 2
        and reviews.get(row["sequence_sha256"], {}).get(
            "challenger_conflict_status", ""
        )
        == "no_conflict"
    ]
    batch = [candidate_from_score_row(row) for row in eligible]
    archive_payload = json.loads(archive_json.read_text(encoding="utf-8"))
    state = build_quality_diversity_archive(
        _archive_candidates(archive_payload), batch
    )
    payload = state.model_dump(mode="json")
    payload.update(
        {
            "schema_version": "ampgent.fgf2-pepflow-source-expansion-qd.1",
            "target_key": "fgf2",
            "source": "PepFlow",
            "operator_id": "fgf2-pepflow-source-expansion-1aa-v1",
            "calibration_reference_run_id": calibration_reference_run_id,
            "batch_csv_sha256": sha256_file(batch_csv),
            "archive_sha256": sha256_file(archive_json),
            "challenger_csv_sha256": sha256_file(challenger_csv),
            "source_batch_candidate_count": len(rows),
            "challenger_reviewed_count": len(reviews),
            "challenger_no_conflict_count": sum(
                row.get("challenger_conflict_status") == "no_conflict"
                for row in reviews.values()
            ),
            "historical_run_modified": False,
            "gpu_rosetta_md_submitted": False,
        }
    )
    payload["payload_sha256"] = sha256_json(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--challenger-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calibration-reference-run-id", required=True)
    args = parser.parse_args()
    evaluate(
        args.batch_csv,
        args.archive_json,
        args.challenger_csv,
        args.output,
        args.calibration_reference_run_id,
    )


if __name__ == "__main__":
    main()
