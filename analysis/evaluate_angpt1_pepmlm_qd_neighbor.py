"""Evaluate the frozen ANGPT1 PepMLM neighbor batch against its QD archive."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from pepagent.autoresearch_quality_diversity import (
    BehaviorSpacePolicy,
    QualityDiversityCandidate,
    behavior_vector,
    build_quality_diversity_archive,
    candidate_from_score_row,
)
from pepagent.provenance.hashing import sha256_file, sha256_json

SCHEMA_VERSION = "ampgent.angpt1-pepmlm-qd-neighbor.1"
TARGET_KEY = "angpt1"
SOURCE = "PepMLM-target-conditioned"
OPERATOR_ID = "angpt1-pepmlm-qd-neighbor-1aa-v1"


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _archive_branch(payload: dict[str, Any]) -> dict[str, Any]:
    branches = payload.get("branches")
    if isinstance(branches, dict) and TARGET_KEY in branches:
        return branches[TARGET_KEY]
    if TARGET_KEY == payload.get("branch_key") or "elites" in payload:
        return payload
    raise ValueError("fixed archive has no ANGPT1 branch")


def _archive_candidates(branch: dict[str, Any]) -> list[QualityDiversityCandidate]:
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
        for item in branch["elites"]
    ]


def _challenger_rows(path: Path) -> dict[str, dict[str, str]]:
    rows = _rows(path)
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        digest = row["sequence_sha256"]
        if digest in result:
            raise ValueError("challenger review contains duplicate sequence identities")
        if row.get("challenger_conflict_status") != "no_conflict":
            raise ValueError("QD input requires reviewed no-conflict challenger rows")
        result[digest] = row
    return result


def _eligible_rows(
    rows: list[dict[str, str]], challenger: dict[str, dict[str, str]]
) -> list[dict[str, str]]:
    result = [
        row
        for row in rows
        if row.get("formal_12_complete", "").casefold() == "true"
        and row.get("display_eligible", "").casefold() == "true"
        and int(row.get("activity_model_support_count_calibrated", "0")) >= 2
        and row.get("excellent_sequence_stage_calibrated", "").casefold() == "true"
        and row["sequence_sha256"] in challenger
    ]
    return result


def evaluate(
    batch_csv: Path,
    challenger_csv: Path,
    archive_json: Path,
    output_dir: Path,
) -> dict[str, Any]:
    rows = _rows(batch_csv)
    if len(rows) != 12 or len({row["sequence_sha256"] for row in rows}) != 12:
        raise ValueError("v2b QD input must contain 12 unique scored rows")
    challenger = _challenger_rows(challenger_csv)
    if len(challenger) != 12 or set(challenger) != {
        row["sequence_sha256"] for row in rows
    }:
        raise ValueError("score and challenger identities do not match exactly")

    archive_payload = json.loads(archive_json.read_text(encoding="utf-8"))
    branch = _archive_branch(archive_payload)
    policy = BehaviorSpacePolicy(**branch["policy"])
    eligible_rows = _eligible_rows(rows, challenger)
    batch = [candidate_from_score_row(row) for row in eligible_rows]
    state = build_quality_diversity_archive(_archive_candidates(branch), batch, policy)
    proposal_rows = {
        row["sequence_sha256"]: row for row in _rows(batch_csv.parent / "proposals.csv")
    }

    contributions: list[dict[str, Any]] = []
    for item in state.contributions:
        proposal = proposal_rows.get(item.candidate_id, {})
        contributions.append(
            {
                **item.model_dump(mode="json"),
                "sequence_sha256": item.candidate_id,
                "sequence": next(
                    row["sequence"]
                    for row in eligible_rows
                    if row["sequence_sha256"] == item.candidate_id
                ),
                "actual_cell_preflight": proposal.get("actual_cell_preflight"),
                "target_cell_hit_preflight": proposal.get("target_cell_hit_preflight"),
                "delta_phi": (
                    json.loads(proposal["delta_phi"])
                    if proposal.get("delta_phi")
                    else None
                ),
            }
        )

    payload: dict[str, Any] = state.model_dump(mode="json")
    payload.update(
        {
            "schema_version": SCHEMA_VERSION,
            "target_key": TARGET_KEY,
            "source": SOURCE,
            "operator_id": OPERATOR_ID,
            "batch_csv_sha256": sha256_file(batch_csv),
            "challenger_csv_sha256": sha256_file(challenger_csv),
            "archive_sha256": sha256_file(archive_json),
            "archive_covered_cell_count": len(branch["covered_cell_ids"]),
            "archive_empty_cell_count": len(branch["empty_cell_ids"]),
            "source_batch_candidate_count": len(rows),
            "formal_12_complete_count": sum(
                row["formal_12_complete"].casefold() == "true" for row in rows
            ),
            "display_eligible_count": sum(
                row["display_eligible"].casefold() == "true" for row in rows
            ),
            "calibrated_support_ge_2_count": sum(
                int(row["activity_model_support_count_calibrated"]) >= 2 for row in rows
            ),
            "excellent_calibrated_count": sum(
                row["excellent_sequence_stage_calibrated"].casefold() == "true"
                for row in rows
            ),
            "challenger_reviewed_count": len(challenger),
            "challenger_no_conflict_count": len(challenger),
            "missing_verified_runtimes": ["apex", "peptiverse"],
            "quality_eligible_candidate_count": len(eligible_rows),
            "historical_run_modified": False,
            "gpu_rosetta_md_submitted": False,
        }
    )
    payload["contribution_count"] = len(contributions)
    payload["payload_sha256"] = sha256_json(payload)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "qd_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fieldnames = [
        "candidate_id",
        "sequence_sha256",
        "sequence",
        "cell_id",
        "contribution",
        "incumbent_candidate_id",
        "quality",
        "actual_cell_preflight",
        "target_cell_hit_preflight",
        "delta_phi",
    ]
    with (output_dir / "qd_candidates.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in contributions:
            writer.writerow({key: row.get(key) for key in fieldnames})
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "target_key": TARGET_KEY,
        "source": SOURCE,
        "operator_id": OPERATOR_ID,
        "batch_candidate_count": len(rows),
        "quality_eligible_count": len(eligible_rows),
        "eligible_new_cell_count": sum(
            item.contribution == "empty_cell" for item in state.contributions
        ),
        "eligible_replacement_count": sum(
            item.contribution == "incumbent_replacement" for item in state.contributions
        ),
        "eligible_same_cell_non_elite_count": sum(
            item.contribution == "same_cell_non_elite" for item in state.contributions
        ),
        "challenger_reviewed_count": len(challenger),
        "challenger_no_conflict_count": len(challenger),
        "missing_verified_runtimes": ["apex", "peptiverse"],
        "qd_summary_sha256": sha256_file(output_dir / "qd_summary.json"),
        "qd_candidates_sha256": sha256_file(output_dir / "qd_candidates.csv"),
        "historical_run_modified": False,
        "gpu_rosetta_md_submitted": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    (output_dir / "qd_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-csv", type=Path, required=True)
    parser.add_argument("--challenger-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    evaluate(args.batch_csv, args.challenger_csv, args.archive_json, args.output_dir)


if __name__ == "__main__":
    main()
