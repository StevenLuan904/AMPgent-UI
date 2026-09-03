"""Evaluate a matched-source batch against a frozen PBP2a QD archive."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from pepagent.autoresearch_quality_diversity import (
    QualityDiversityCandidate,
    behavior_vector,
    build_quality_diversity_archive,
    candidate_from_score_row,
)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def wilson_interval(
    successes: int, trials: int, z: float = 1.959963984540054
) -> tuple[float, float]:
    if trials == 0:
        return (0.0, 0.0)
    proportion = successes / trials
    denominator = 1 + z * z / trials
    center = (proportion + z * z / (2 * trials)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / trials + z * z / (4 * trials * trials)
    ) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def archive_candidates(path: Path) -> tuple[dict[str, Any], list[QualityDiversityCandidate]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    archive = payload.get("branches", {}).get("pbp2a", payload)
    candidates = []
    for elite in archive["elites"]:
        behavior = elite["behavior"]
        candidates.append(
            QualityDiversityCandidate(
                candidate_id=str(elite["candidate_id"]),
                sequence=str(elite["sequence"]),
                behavior=behavior_vector(
                    str(elite["sequence"]),
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
    return archive, candidates


def _count(rows: list[dict[str, str]], predicate) -> int:
    return sum(bool(predicate(row)) for row in rows)


def _summary(rows: list[dict[str, str]], qd: dict[str, dict[str, bool]]) -> dict[str, Any]:
    proposal = len(rows)
    formal = _count(rows, lambda row: row.get("formal_12_complete") == "true")
    display = _count(rows, lambda row: row.get("display_eligible") == "true")
    support = _count(
        rows,
        lambda row: int(row.get("activity_model_support_count_calibrated") or 0) >= 2,
    )
    excellent = _count(
        rows,
        lambda row: row.get("display_eligible") == "true"
        and int(row.get("activity_model_support_count_calibrated") or 0) >= 2,
    )
    no_conflict = _count(rows, lambda row: row.get("challenger_conflict_status") == "no_conflict")
    qd_eligible = _count(rows, lambda row: row["candidate_id"] in qd)
    new_cell = sum(
        qd[row["candidate_id"]]["new_cell"]
        for row in rows
        if row["candidate_id"] in qd
    )
    replacement = sum(
        qd[row["candidate_id"]]["replacement"]
        for row in rows
        if row["candidate_id"] in qd
    )
    low, high = wilson_interval(support, proposal)
    return {
        "source_arm": rows[0]["source_arm"] if rows else "",
        "proposal_count": proposal,
        "formal12_count": formal,
        "display_count": display,
        "activity_support_ge_2_count": support,
        "excellent_count": excellent,
        "challenger_no_conflict_count": no_conflict,
        "qd_eligible_count": qd_eligible,
        "qd_new_cell_count": new_cell,
        "qd_replacement_count": replacement,
        "support_rate": support / proposal if proposal else 0.0,
        "support_wilson95_low": low,
        "support_wilson95_high": high,
        "apex_status": "runtime_unavailable",
        "peptiverse_status": "runtime_unavailable",
    }


def run(args: argparse.Namespace) -> None:
    rows = read_rows(args.calibrated_csv)
    challenger = {row["candidate_id"]: row for row in read_rows(args.challenger_csv)}
    for row in rows:
        review = challenger.get(row["candidate_id"])
        if review is None:
            raise ValueError(f"challenger coverage missing: {row['candidate_id']}")
        row["challenger_conflict_status"] = review.get("challenger_conflict_status", "")
    archive, prior = archive_candidates(args.archive_json)
    eligible_rows = [
        row
        for row in rows
        if row.get("display_eligible") == "true"
        and int(row.get("activity_model_support_count_calibrated") or 0) >= 2
        and row.get("challenger_conflict_status") == "no_conflict"
    ]
    batch = [candidate_from_score_row(row) for row in eligible_rows]
    state = build_quality_diversity_archive(prior, batch)
    qd_by_id: dict[str, dict[str, bool]] = {}
    contribution_by_id: dict[str, str] = {}
    for row, contribution in zip(eligible_rows, state.contributions, strict=True):
        qd_by_id[row["candidate_id"]] = {
            "new_cell": contribution.contribution == "empty_cell",
            "replacement": contribution.contribution == "incumbent_replacement",
        }
        contribution_by_id[row["candidate_id"]] = contribution.contribution
    qd_payload = state.model_dump(mode="json")
    qd_payload.update(
        {
            "schema_version": "ampgent.pbp2a-matched-source.qd.1",
            "archive_path": str(args.archive_json),
            "archive_sha256": hashlib.sha256(args.archive_json.read_bytes()).hexdigest(),
            "eligible_input_count": len(eligible_rows),
            "new_cell_count": sum(item["new_cell"] for item in qd_by_id.values()),
            "replacement_count": sum(item["replacement"] for item in qd_by_id.values()),
            "contribution_by_candidate_id": contribution_by_id,
            "historical_run_modified": False,
        }
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "quality_diversity.json").write_text(
        json.dumps(qd_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    task_rows = []
    for row in eligible_rows:
        contribution = contribution_by_id[row["candidate_id"]]
        if contribution not in {"empty_cell", "incumbent_replacement"}:
            continue
        task_rows.append(
            {
                "task_key": (
                    f"rosetta5:pbp2a:{row['sequence_sha256']}:"
                    f"{row['candidate_id']}"
                ),
                "target_key": "pbp2a",
                "candidate_id": row["candidate_id"],
                "sequence": row["sequence"],
                "sequence_sha256": row["sequence_sha256"],
                "source_arm": row["source_arm"],
                "qd_contribution": contribution,
                "rosetta_required": True,
                "submitted": False,
            }
        )
    if task_rows:
        with (args.output_dir / "structure_task_keys.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=list(task_rows[0]))
            writer.writeheader()
            writer.writerows(task_rows)
    summaries = [
        _summary(
            [row for row in rows if row["source_arm"] == source],
            qd_by_id,
        )
        for source in ("PepGLAD", "PepFlow")
    ]
    with (args.output_dir / "arm_summary.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    receipt = {
        "schema_version": "ampgent.pbp2a-matched-source.receipt.1",
        "proposal_count": len(rows),
        "formal12_count": sum(row.get("formal_12_complete") == "true" for row in rows),
        "display_count": sum(row.get("display_eligible") == "true" for row in rows),
        "activity_support_ge_2_count": sum(
            int(row.get("activity_model_support_count_calibrated") or 0) >= 2 for row in rows
        ),
        "excellent_count": len(eligible_rows),
        "challenger_count": len(challenger),
        "challenger_no_conflict_count": sum(
            row.get("challenger_conflict_status") == "no_conflict" for row in rows
        ),
        "qd_eligible_count": len(eligible_rows),
        "qd_new_cell_count": qd_payload["new_cell_count"],
        "qd_replacement_count": qd_payload["replacement_count"],
        "structure_task_key_count": len(task_rows),
        "arm_summary": summaries,
        "shadow_status": {"apex": "runtime_unavailable", "peptiverse": "runtime_unavailable"},
        "materialized_count": 0,
        "pg_exact_status": "preflight_only_no_candidate_write",
        "rosetta_md_submitted": False,
        "archive_sha256": qd_payload["archive_sha256"],
        "calibrated_csv_sha256": hashlib.sha256(args.calibrated_csv.read_bytes()).hexdigest(),
        "challenger_csv_sha256": hashlib.sha256(args.challenger_csv.read_bytes()).hexdigest(),
    }
    (args.output_dir / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibrated-csv", type=Path, required=True)
    parser.add_argument("--challenger-csv", type=Path, required=True)
    parser.add_argument("--archive-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
