"""Evaluate a scored PBP2a PepFlow batch against the frozen PBP2a archive."""

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


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _archive(path: Path) -> list[QualityDiversityCandidate]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    branch = payload.get("branches", {}).get("pbp2a", payload)
    result = []
    for elite in branch["elites"]:
        behavior = elite["behavior"]
        result.append(
            QualityDiversityCandidate(
                candidate_id=elite["candidate_id"],
                sequence=elite["sequence"],
                behavior=behavior_vector(
                    elite["sequence"],
                    net_charge=float(behavior["charge_density"]) * float(behavior["length"]),
                    hydrophobicity=float(behavior["hydrophobicity"]),
                    hydrophobic_moment=float(behavior["hydrophobic_moment"]),
                ),
                quality=float(elite["quality"]),
                display_eligible=True,
                activity_support_count=2,
                hemolysis_probability=0.0,
                hemolysis_label="low",
                operator_name="frozen_pbp2a_archive",
            )
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--schema-version", default="ampgent.pbp2a-pepflow-same-domain-qd.1")
    parser.add_argument("--target-key", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--operator-id", default="")
    args = parser.parse_args()
    rows = _read(args.candidate_csv)
    candidates = [candidate_from_score_row(row) for row in rows]
    state = build_quality_diversity_archive(_archive(args.archive_json), candidates)
    by_id = {item.candidate_id: item for item in state.contributions}
    output_rows = []
    for row in rows:
        contribution = by_id[row["sequence_sha256"]]
        output_rows.append(
            {
                "sequence_sha256": row["sequence_sha256"],
                "candidate_id": row.get("candidate_id", ""),
                "display_eligible": row.get("display_eligible", "false"),
                "activity_support_count_calibrated": row.get(
                    "activity_model_support_count_calibrated", "0"
                ),
                "actual_cell_id": contribution.cell_id or "",
                "contribution": contribution.contribution,
                "new_cell": str(contribution.contribution == "empty_cell").lower(),
                "replacement": str(contribution.contribution == "incumbent_replacement").lower(),
                "quality": f"{contribution.quality:.9f}",
                "incumbent_candidate_id": contribution.incumbent_candidate_id or "",
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with (args.output.parent / "qd_candidates.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)
    payload = {
        "schema_version": args.schema_version,
        "target_key": args.target_key,
        "source": args.source,
        "operator_id": args.operator_id,
        "candidate_csv_sha256": hashlib.sha256(args.candidate_csv.read_bytes()).hexdigest(),
        "archive_sha256": hashlib.sha256(args.archive_json.read_bytes()).hexdigest(),
        "candidate_count": len(rows),
        "quality_eligible_count": sum(
            item.contribution in {"empty_cell", "incumbent_replacement", "same_cell_non_elite"}
            for item in state.contributions
        ),
        "new_cell_count": sum(item.contribution == "empty_cell" for item in state.contributions),
        "replacement_count": sum(
            item.contribution == "incumbent_replacement" for item in state.contributions
        ),
        "same_cell_non_elite_count": sum(
            item.contribution == "same_cell_non_elite" for item in state.contributions
        ),
        "quality_gate_failed_count": sum(
            item.contribution == "quality_gate_failed" for item in state.contributions
        ),
        "archive_qd_score": state.archive_qd_score,
        "valid_cell_coverage": state.valid_cell_coverage,
        "maximum_cell_concentration": state.maximum_cell_concentration,
        "archive_relative_novelty": state.archive_relative_novelty,
        "historical_run_modified": False,
    }
    payload["qd_candidates_csv_sha256"] = hashlib.sha256(
        (args.output.parent / "qd_candidates.csv").read_bytes()
    ).hexdigest()
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
