from __future__ import annotations

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_file, sha256_json

ACTIVITY_PERCENTILES = (
    "amp_read_log10_mic_um__parent_benefit_percentile",
    "llamp_log10_mic_um__parent_benefit_percentile",
    "macrel_amp_probability__parent_benefit_percentile",
)
CONFLICT_STATES = {"no_conflict", "cross_model_disagreement_retained"}


def _truth(value: object) -> bool:
    return str(value).strip().casefold() == "true"


def _gate(row: dict[str, str]) -> bool:
    return bool(
        _truth(row.get("formal_12_complete"))
        and int(row.get("formal_metric_count") or 0) == 12
        and _truth(row.get("display_eligible"))
        and _truth(row.get("excellent_sequence_stage_calibrated"))
        and int(row.get("activity_model_support_count_calibrated") or 0) >= 2
        and float(row["guruprasad_instability_index"]) <= 50.0
        and row.get("toxinpred3_label") == "Non-Toxin"
        and row.get("macrel_hemolysis_label", "").casefold() == "low"
        and row.get("historical_exact_replay", "").casefold() == "false"
        and row.get("challenger_conflict_status") in CONFLICT_STATES
        and row.get("candidate_id")
        and row.get("family_key_80_80")
    )


def _rank(row: dict[str, str]) -> tuple[object, ...]:
    percentiles = [float(row[name]) for name in ACTIVITY_PERCENTILES]
    return (
        -int(row["activity_model_support_count_calibrated"]),
        -min(percentiles),
        -(sum(percentiles) / len(percentiles)),
        float(row["calibrated_hemolysis_probability"]),
        float(row["guruprasad_instability_index"]),
        row["sequence_sha256"],
    )


def select(
    sources: list[tuple[str, str, Path]], limit_per_target: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    targets = dict.fromkeys(target for target, _, _ in sources)
    for target in targets:
        rows: list[dict[str, str]] = []
        source_summary: list[dict[str, Any]] = []
        for source_target, run_id, path in sources:
            if source_target != target:
                continue
            with path.open(encoding="utf-8-sig", newline="") as stream:
                source_rows = list(csv.DictReader(stream))
            if not source_rows or {row["branch_key"] for row in source_rows} != {target}:
                raise ValueError(f"source branch mismatch: {path}")
            for row in source_rows:
                row["_subject_run_id"] = run_id
                rows.append(row)
            source_summary.append(
                {
                    "subject_run_id": run_id,
                    "source_candidate_count": len(source_rows),
                    "source_csv_sha256": sha256_file(path),
                }
            )
        eligible = [row for row in rows if _gate(row)]
        by_family: dict[str, dict[str, str]] = {}
        for row in sorted(eligible, key=_rank):
            by_family.setdefault(row["family_key_80_80"], row)
        representatives = sorted(by_family.values(), key=_rank)
        target_selected = representatives[:limit_per_target]
        for rank, row in enumerate(target_selected, start=1):
            selected.append(
                {
                    "target_key": target,
                    "subject_run_id": row["_subject_run_id"],
                    "pool_a_pre_rosetta_rank": rank,
                    "candidate_id": row["candidate_id"],
                    "sequence": row["sequence"],
                    "sequence_sha256": row["sequence_sha256"],
                    "family_key_80_80": row["family_key_80_80"],
                    "activity_model_support_count": int(
                        row["activity_model_support_count_calibrated"]
                    ),
                    "activity_parent_benefit_min_percentile": min(
                        float(row[name]) for name in ACTIVITY_PERCENTILES
                    ),
                    "activity_parent_benefit_mean_percentile": sum(
                        float(row[name]) for name in ACTIVITY_PERCENTILES
                    )
                    / len(ACTIVITY_PERCENTILES),
                    "guruprasad_instability_index": float(
                        row["guruprasad_instability_index"]
                    ),
                    "challenger_conflict_status": row["challenger_conflict_status"],
                    "rosetta_required_nstruct": 5,
                    "rosetta_gate_dg_separated_reu_lt": -30,
                }
            )
        summary[target] = {
            "sources": source_summary,
            "source_candidate_count": len(rows),
            "strict_pre_rosetta_candidate_count": len(eligible),
            "strict_pre_rosetta_family_count": len(representatives),
            "priority_candidate_count": len(target_selected),
            "family_gap_to_50_before_rosetta": max(0, 50 - len(representatives)),
        }
    return selected, summary


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write empty priority manifest")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _source(value: str) -> tuple[str, str, Path]:
    target, run_id, path = value.split(",", 2)
    return target, run_id, Path(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", type=_source, required=True)
    parser.add_argument("--limit-per-target", type=int, default=50)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()
    rows, summary = select(args.source, args.limit_per_target)
    _write_csv(args.output_csv, rows)
    receipt = {
        "schema_version": "ampgent.pool-a-pre-rosetta-priority.1",
        "observed_at": datetime.now(UTC).isoformat(),
        "selection": (
            "strict_gate_then_one_per_80_80_family_then_lexicographic_activity_quality"
        ),
        "weighted_quality_diversity_total_used": False,
        "candidate_count": len(rows),
        "targets": summary,
        "output_csv_sha256": sha256_file(args.output_csv),
        "historical_runs_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    args.output_json.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
