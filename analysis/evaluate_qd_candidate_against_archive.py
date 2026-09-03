"""Evaluate one scored candidate against a frozen quality-diversity archive."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from pepagent.autoresearch_quality_diversity import (
    QualityDiversityCandidate,
    behavior_vector,
    build_quality_diversity_archive,
    candidate_from_score_row,
)


def _archive_candidates(path: Path) -> list[QualityDiversityCandidate]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = []
    for elite in payload["elites"]:
        behavior = elite["behavior"]
        result.append(
            QualityDiversityCandidate(
                candidate_id=elite["candidate_id"],
                sequence=elite["sequence"],
                behavior=behavior_vector(
                    elite["sequence"],
                    net_charge=behavior["charge_density"] * behavior["length"],
                    hydrophobicity=behavior["hydrophobicity"],
                    hydrophobic_moment=behavior["hydrophobic_moment"],
                ),
                quality=float(elite["quality"]),
                display_eligible=True,
                activity_support_count=2,
                hemolysis_probability=0.0,
                hemolysis_label="low",
                operator_name="frozen_archive",
            )
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-csv", type=Path, required=True)
    parser.add_argument("--sequence-sha256", required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.candidate_csv.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    selected = [row for row in rows if row.get("sequence_sha256") == args.sequence_sha256]
    if len(selected) != 1:
        raise ValueError("candidate sequence identity is not unique in score CSV")
    candidate = candidate_from_score_row(selected[0])
    state = build_quality_diversity_archive(
        _archive_candidates(args.archive_json), [candidate]
    )
    contribution = state.contributions[0]
    payload = {
        "schema_version": "ampgent.qd-candidate-archive-assessment.1",
        "policy_id": state.policy.policy_id,
        "archive_sha256": hashlib.sha256(args.archive_json.read_bytes()).hexdigest(),
        "candidate_csv_sha256": hashlib.sha256(args.candidate_csv.read_bytes()).hexdigest(),
        "candidate_id": candidate.candidate_id,
        "sequence_sha256": args.sequence_sha256,
        "actual_cell_id": contribution.cell_id,
        "contribution": contribution.contribution,
        "quality_eligible": contribution.contribution
        in {"empty_cell", "incumbent_replacement", "same_cell_non_elite"},
        "new_cell": contribution.contribution == "empty_cell",
        "replacement": contribution.contribution == "incumbent_replacement",
        "eligible_batch_candidate_count": state.eligible_batch_candidate_count,
        "covered_cell_count": len(state.covered_cell_ids),
        "archive_qd_score": state.archive_qd_score,
        "historical_run_modified": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
