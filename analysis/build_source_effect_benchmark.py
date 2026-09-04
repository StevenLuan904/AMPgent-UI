"""Build a run-scoped benchmark of source-to-evidence conversion.

The input is an explicit JSON manifest of immutable compact artifacts.  No
sequence is merged across runs: records are keyed by ``run_id`` and, for
source-split runs, by the source column in that same run.  This is an audit
summary, not a winner score; all rates retain their stage denominator.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

QD_ELIGIBLE_CONTRIBUTIONS = {
    "empty_cell",
    "incumbent_replacement",
    "replacement",
    "same_cell_non_elite",
}


def _bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _resolve(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else base / path


def _wilson(successes: int, trials: int) -> list[float] | None:
    if trials == 0:
        return None
    z = 1.959963984540054
    p = successes / trials
    denominator = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def _rate(successes: int, trials: int) -> dict[str, Any]:
    return {
        "count": successes,
        "denominator": trials,
        "rate": successes / trials if trials else None,
        "ci95": _wilson(successes, trials),
    }


def _first_int(receipt: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in receipt:
            return _int(receipt[key])
    return 0


def _quality_row(row: dict[str, str]) -> float | None:
    for key in ("quality", "primary_quality", "quality_score"):
        value = _float(row.get(key))
        if value is not None:
            return value
    return None


def _delta_phi(rows: list[dict[str, str]]) -> dict[str, Any] | None:
    vectors: list[list[float]] = []
    axes: list[str] | None = None
    for row in rows:
        raw = row.get("delta_phi", "")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        vector = payload.get("acceptor_to_child")
        if isinstance(vector, list) and all(_float(item) is not None for item in vector):
            vectors.append([float(item) for item in vector])
            if axes is None and isinstance(payload.get("axes"), list):
                axes = [str(item) for item in payload["axes"]]
    if not vectors:
        return None
    return {
        "axes": axes or ["axis_0", "axis_1", "axis_2", "axis_3"],
        "mean": [
            sum(values) / len(values) for values in zip(*vectors, strict=True)
        ],
        "n": len(vectors),
    }


def _source_name(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized == "pepglad":
        return "PepGLAD"
    if normalized == "pepflow":
        return "PepFlow"
    if normalized in {"pepmlm", "pepmlm-target-conditioned"}:
        return "PepMLM"
    return value.strip() or "unknown"


def _candidate_key(row: dict[str, str]) -> str:
    return row.get("sequence_sha256") or row.get("candidate_id") or row.get("sequence", "")


def _source_groups(rows: list[dict[str, str]], split: bool) -> dict[str, list[dict[str, str]]]:
    if not split:
        return {"all": rows}
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        source = _source_name(row.get("donor_source", row.get("source", "unknown")))
        groups.setdefault(source, []).append(row)
    return dict(sorted(groups.items()))


def _challenger_counts(
    rows: list[dict[str, str]], receipt: dict[str, Any], split: bool
) -> tuple[int, int]:
    if rows:
        reviewed = len(rows)
        no_conflict = sum(
            row.get("challenger_conflict_status", "").strip().lower() == "no_conflict"
            for row in rows
        )
        return reviewed, no_conflict
    if split:
        return 0, 0
    return (
        _first_int(receipt, "challenger_reviewed_count", "challenger_reviewed"),
        _first_int(receipt, "challenger_no_conflict_count", "challenger_no_conflict"),
    )


def _qd_metrics(
    qd: dict[str, Any], rows: list[dict[str, str]], split: bool, *,
    filter_to_rows: bool = False,
) -> dict[str, Any]:
    contributions = qd.get("contributions")
    if (split or filter_to_rows) and isinstance(contributions, list):
        row_keys = {_candidate_key(row) for row in rows}
        selected = [
            item
            for item in contributions
            if str(item.get("candidate_id", "")) in row_keys
            or str(item.get("sequence_sha256", "")) in row_keys
        ]
        eligible = [
            item
            for item in selected
            if item.get("contribution") in QD_ELIGIBLE_CONTRIBUTIONS
        ]
        new_cell = sum(item.get("contribution") == "empty_cell" for item in eligible)
        replacement = sum(
            item.get("contribution") in {"incumbent_replacement", "replacement"}
            for item in eligible
        )
        cells = {str(item.get("cell_id")) for item in eligible if item.get("cell_id")}
        qualities = [
            _float(item.get("quality"))
            for item in eligible
            if _float(item.get("quality")) is not None
        ]
        return {
            "eligible": len(eligible),
            "new_cell": new_cell,
            "replacement": replacement,
            "best_quality": max(qualities) if qualities else None,
            "mean_quality": sum(qualities) / len(qualities) if qualities else None,
            "valid_cell_coverage": len(cells) / 2160,
            "archive_qd_score": None,
            "maximum_cell_concentration": (
                max(Counter(str(item.get("cell_id")) for item in eligible).values())
                / len(eligible)
                if eligible
                else None
            ),
            "archive_relative_novelty": None,
            "metric_note": (
                "source split recomputed from same-run QD contributions"
                if split
                else "materialized cohort filtered from same-run QD contributions"
            ),
        }

    eligible = _first_int(qd, "eligible_batch_candidate_count")
    new_cell = (
        sum(item.get("contribution") == "empty_cell" for item in contributions)
        if isinstance(contributions, list)
        else _first_int(qd, "new_cell_count")
    )
    replacement = _first_int(qd, "incumbent_replacement_count", "replacement_count")
    if not isinstance(contributions, list):
        receipt_contribution = str(qd.get("contribution", "")).strip().lower()
        if not new_cell:
            new_cell = int(_bool(qd.get("new_cell")))
        if not new_cell and receipt_contribution == "empty_cell":
            new_cell = 1
        if not replacement:
            replacement = int(_bool(qd.get("replacement")))
        if not replacement and receipt_contribution in {
            "incumbent_replacement",
            "replacement",
        }:
            replacement = 1
    if not replacement and isinstance(contributions, list):
        replacement = sum(
            item.get("contribution") in {"incumbent_replacement", "replacement"}
            for item in contributions
        )
    if not eligible:
        eligible = new_cell + replacement
    qualities = (
        [
            _float(item.get("quality"))
            for item in contributions
            if isinstance(item, dict) and _float(item.get("quality")) is not None
        ]
        if isinstance(contributions, list)
        else []
    )
    best_quality = _float(qd.get("best_peptide_quality"))
    mean_quality = _float(qd.get("mean_peptide_quality"))
    if best_quality is None and qualities:
        best_quality = max(qualities)
    if mean_quality is None and qualities:
        mean_quality = sum(qualities) / len(qualities)
    actual_cell = qd.get("actual_cell_id")
    if actual_cell and not eligible:
        eligible = 1
        new_cell = int(_bool(qd.get("new_cell")))
        replacement = int(_bool(qd.get("replacement")))
    return {
        "eligible": eligible,
        "new_cell": new_cell,
        "replacement": replacement,
        "best_quality": best_quality,
        "mean_quality": mean_quality,
        "valid_cell_coverage": _float(qd.get("valid_cell_coverage"))
        or (len({actual_cell}) / 2160 if actual_cell else None),
        "archive_qd_score": _float(qd.get("archive_qd_score")),
        "maximum_cell_concentration": _float(qd.get("maximum_cell_concentration"))
        or (1.0 if actual_cell else None),
        "archive_relative_novelty": _float(qd.get("archive_relative_novelty"))
        or (1.0 if _bool(qd.get("new_cell")) else None),
        "metric_note": (
            "single-candidate QD fallback uses the recorded actual cell"
            if actual_cell and not qd.get("valid_cell_coverage")
            else None
        ),
    }


def _rosetta(spec: dict[str, Any], qd_eligible: int, base: Path) -> dict[str, Any]:
    path_value = spec.get("rosetta_path")
    if not path_value:
        return {
            "evaluated_candidates": 0,
            "evaluated_decoys": 0,
            "passed_candidates": 0,
            "pending_candidates": qd_eligible,
            "status": "not_started",
        }
    receipt = _load_json(base / path_value)
    decoys = receipt.get("decoys", [])
    passed = int(bool(receipt.get("aggregation", {}).get("strict_pool_a_gate_passed")))
    return {
        "evaluated_candidates": int(bool(decoys)),
        "evaluated_decoys": len(decoys),
        "passed_candidates": passed,
        "pending_candidates": max(0, qd_eligible - int(bool(decoys))),
        "status": "passed" if passed else "evaluated_rejected",
        "median_dg_separated_reu": receipt.get("aggregation", {}).get(
            "median_dG_separated_reu"
        ),
    }


def _record(spec: dict[str, Any], base: Path) -> list[dict[str, Any]]:
    score_rows = _load_csv(_resolve(base, spec["score_path"]))
    proposal_rows = _load_csv(_resolve(base, spec.get("proposal_path")))
    challenger_rows = _load_csv(_resolve(base, spec.get("challenger_path")))
    material = _load_json(_resolve(base, spec["materialization_path"]))
    qd = _load_json(_resolve(base, spec["qd_path"]))
    split = bool(spec.get("source_split"))
    materialized_run = _first_int(
        material, "materialized_or_reused_in_run_count", "source_candidate_count"
    )
    groups = _source_groups(score_rows, split)
    materialized_score_rows = _load_csv(
        _resolve(base, spec.get("materialized_score_path"))
    )
    if spec.get("materialized_score_path") and not materialized_score_rows:
        raise ValueError(
            f"materialized_score_path has no rows: {spec['materialized_score_path']}"
        )
    materialized_groups = _source_groups(
        materialized_score_rows or score_rows, split
    )
    proposal_groups = _source_groups(proposal_rows, split) if proposal_rows else {}
    result: list[dict[str, Any]] = []
    for source, rows in groups.items():
        materialized_rows = materialized_groups.get(source, [])
        proposals = len(proposal_groups.get(source, [])) if proposal_rows else len(rows)
        if source == "all" and "proposal_count_override" in spec:
            proposals = _int(spec["proposal_count_override"])
        materialized = len(materialized_rows)
        full12 = sum(_bool(row.get("formal_12_complete")) for row in rows)
        display = sum(_bool(row.get("display_eligible")) for row in rows)
        support = sum(
            _int(
                row.get(
                    "activity_model_support_count_calibrated",
                    row.get("activity_model_support_count"),
                )
            )
            >= 2
            for row in rows
        )
        excellent = sum(
            _bool(
                row.get(
                    "excellent_sequence_stage_calibrated",
                    row.get("excellent_sequence_stage"),
                )
            )
            for row in rows
        )
        materialized_full12 = sum(
            _bool(row.get("formal_12_complete")) for row in materialized_rows
        )
        materialized_display = sum(
            _bool(row.get("display_eligible")) for row in materialized_rows
        )
        materialized_support = sum(
            _int(
                row.get(
                    "activity_model_support_count_calibrated",
                    row.get("activity_model_support_count"),
                )
            )
            >= 2
            for row in materialized_rows
        )
        materialized_excellent = sum(
            _bool(
                row.get(
                    "excellent_sequence_stage_calibrated",
                    row.get("excellent_sequence_stage"),
                )
            )
            for row in materialized_rows
        )
        challenge_rows = (
            [
                row
                for row in challenger_rows
                if _source_name(row.get("donor_source", row.get("source", ""))) == source
            ]
            if split
            else challenger_rows
        )
        challenger_reviewed, challenger_no_conflict = _challenger_counts(
            challenge_rows, material, split
        )
        materialized_challenger_rows = _load_csv(
            _resolve(base, spec.get("materialized_challenger_path"))
        )
        if not materialized_challenger_rows:
            materialized_challenger_rows = materialized_rows
        if split:
            materialized_challenger_rows = [
                row
                for row in materialized_challenger_rows
                if _source_name(row.get("donor_source", row.get("source", "")))
                == source
            ]
        materialized_challenger_reviewed, materialized_challenger_no_conflict = (
            _challenger_counts(materialized_challenger_rows, material, split)
        )
        qd_metrics = _qd_metrics(qd, rows, split)
        materialized_qd_metrics = _qd_metrics(
            qd, materialized_rows, split, filter_to_rows=True
        )
        qd_eligible = qd_metrics["eligible"]
        materialized_qd_eligible = materialized_qd_metrics["eligible"]
        rosetta = _rosetta(spec, materialized_qd_eligible, base)
        admitted = spec.get("pool_a_admitted_count")
        if admitted is None:
            admitted = qd_metrics["new_cell"] + qd_metrics["replacement"]
        identity_kind = spec.get(
            "candidate_id_kind",
            "authoritative_uuid" if not split else "run_plus_sequence_sha256_artifact_identity",
        )
        keys = [_candidate_key(row) for row in rows]
        result.append(
            {
                "label": spec["label"] if source == "all" else f"{spec['label']} / {source}",
                "source": spec["source"] if source == "all" else source,
                "source_scope": "run" if source == "all" else "run_source_split",
                "evidence_strength": spec.get(
                    "evidence_strength", "run_scoped_observational"
                ),
                "target_key": spec["target_key"],
                "run_id": spec["run_id"],
                "identity_basis": f"run_id={spec['run_id']} + sequence_sha256",
                "candidate_id_kind": identity_kind,
                "within_group_unique_sequence_sha256": len(keys) == len(set(keys)),
                "run_materialized_count": materialized_run,
                "pg_identity": {
                    "run_id": material.get("operational_run_id", spec["run_id"]),
                    "tool_call_id": material.get("tool_call_id"),
                    "materialized_candidate_count": materialized_run,
                    "inserted_evaluation_count": _first_int(
                        material, "inserted_evaluation_count", "formal_evaluation_count"
                    ),
                    "replay_count": _first_int(
                        material, "global_exact_replay_skip_count"
                    ),
                    "historical_runs_modified": bool(
                        material.get(
                            "historical_runs_modified",
                            material.get("historical_run_modified", False),
                        )
                    ),
                },
                "rates": {
                    "proposal_to_materialized": _rate(materialized, proposals),
                    "proposal_to_formal12": _rate(full12, proposals),
                    "proposal_to_display": _rate(display, proposals),
                    "proposal_to_activity_support_ge_2": _rate(support, proposals),
                    "proposal_to_challenger_reviewed": _rate(
                        challenger_reviewed, proposals
                    ),
                    "materialized_to_formal12": _rate(
                        materialized_full12, materialized
                    ),
                    "materialized_to_display": _rate(
                        materialized_display, materialized
                    ),
                    "materialized_to_activity_support_ge_2": _rate(
                        materialized_support, materialized
                    ),
                    "materialized_to_excellent": _rate(
                        materialized_excellent, materialized
                    ),
                    "materialized_to_qd_eligible": _rate(
                        materialized_qd_eligible, materialized
                    ),
                },
                "materialized_cohort": {
                    "candidate_count": materialized,
                    "formal12": materialized_full12,
                    "display": materialized_display,
                    "activity_support_ge_2": materialized_support,
                    "excellent": materialized_excellent,
                    "challenger_reviewed": materialized_challenger_reviewed,
                    "challenger_no_conflict": materialized_challenger_no_conflict,
                    "qd_eligible": materialized_qd_eligible,
                    "qd_new_cell": materialized_qd_metrics["new_cell"],
                    "qd_replacement": materialized_qd_metrics["replacement"],
                },
                "counts": {
                    "proposal": proposals,
                    "materialized": materialized,
                    "formal12": full12,
                    "display": display,
                    "activity_support_ge_2": support,
                    "excellent": excellent,
                    "challenger_reviewed": challenger_reviewed,
                    "challenger_no_conflict": challenger_no_conflict,
                    "challenger_conflict": max(0, challenger_reviewed - challenger_no_conflict),
                    "qd_eligible": qd_eligible,
                    "qd_new_cell": qd_metrics["new_cell"],
                    "qd_replacement": qd_metrics["replacement"],
                    "rosetta_evaluated_candidates": rosetta["evaluated_candidates"],
                    "rosetta_evaluated_decoys": rosetta["evaluated_decoys"],
                    "rosetta_passed_candidates": rosetta["passed_candidates"],
                    "rosetta_pending_candidates": rosetta["pending_candidates"],
                    "pool_a_admitted": _int(admitted),
                    "md_complete": _int(spec.get("md_complete_count")),
                    "md_full_evidence": _int(spec.get("md_full_evidence_count")),
                },
                "challenger": {
                    "apex": "runtime_unavailable",
                    "peptiverse": "runtime_unavailable",
                    "hemopi2_reviewed": challenger_reviewed,
                    "hemopi2_no_conflict": challenger_no_conflict,
                },
                "qd_metrics": qd_metrics,
                "rosetta": rosetta,
                "mean_delta_phi": _delta_phi(proposal_groups.get(source, rows)),
                "evidence": {
                    "score_path": spec["score_path"],
                    "materialized_score_path": spec.get("materialized_score_path"),
                    "proposal_path": spec.get("proposal_path"),
                    "materialization_path": spec["materialization_path"],
                    "qd_path": spec["qd_path"],
                    "challenger_path": spec.get("challenger_path"),
                },
            }
        )
    return result


def _next_operator(records: list[dict[str, Any]], base_dir: Path) -> dict[str, Any]:
    controlled = [
        record
        for record in records
        if record["evidence_strength"] == "matched_controlled"
        and record["counts"]["materialized"] >= 3
    ]
    comparable = [
        record
        for record in records
        if record["source_scope"] == "run" and record["counts"]["materialized"] >= 3
    ]
    if controlled:
        comparable = controlled
        selection_basis = "matched_controlled_same_target_operator"
        reason = (
            "preferred matched same-target/operator control; highest observed "
            "QD-eligible conversion within the controlled arms"
        )
    else:
        selection_basis = "run_scoped_observational"
        reason = (
            "highest observed QD-eligible conversion among comparable materialized "
            "batches; target/operator are confounded"
        )
    if not comparable:
        return {"status": "insufficient_comparable_runs"}
    chosen = max(
        comparable,
        key=lambda record: (
            record["rates"]["materialized_to_qd_eligible"]["rate"] or -1,
            record["rates"]["materialized_to_activity_support_ge_2"]["rate"] or -1,
            record["counts"]["materialized"],
        ),
    )
    qd_cells: list[str] = []
    qd_path = Path(chosen["evidence"]["qd_path"])
    if not qd_path.is_absolute():
        qd_path = base_dir / qd_path
    for item in _load_json(qd_path).get("contributions", []):
        if item.get("contribution") in QD_ELIGIBLE_CONTRIBUTIONS and item.get("cell_id"):
            qd_cells.append(str(item["cell_id"]))
    return {
        "status": "provisional_observational_choice",
        "selection_basis": selection_basis,
        "source": chosen["source"],
        "target_key": chosen["target_key"],
        "reason": reason,
        "target_qd_region": sorted(set(qd_cells)),
        "property_displacement": chosen["mean_delta_phi"],
        "operator": (
            "same-domain conservative graft; preserve display and dual-activity "
            "support, then test adjacent/same-cell QD movement"
        ),
        "guardrails": [
            "no weighted q+lambdaD ranking",
            "no hydrophobic hard gate",
            "APEX/PeptiVerse runtime_unavailable is not a pass",
            "Rosetta median dG<-30 remains required before targeted Pool A",
        ],
    }


def _coverage_matrix(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize benchmark-record coverage without inferring missing science."""
    targets = ["acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1"]
    sources = ["PepGLAD", "PepFlow", "PepMLM"]
    cells: dict[str, dict[str, Any]] = {}
    for target in targets:
        for source in sources:
            matching = [
                record
                for record in records
                if record["target_key"].casefold() == target
                and _source_name(record["source"]) == source
            ]
            cells[f"{source}:{target}"] = {
                "target_key": target,
                "source": source,
                "record_count": len(matching),
                "run_ids": [record["run_id"] for record in matching],
                "materialized_count": sum(
                    record["counts"]["materialized"] for record in matching
                ),
                "qd_eligible_count": sum(
                    record["counts"]["qd_eligible"] for record in matching
                ),
                "status": (
                    "recorded" if matching else "benchmark_not_recorded"
                ),
            }
    return {
        "sources": sources,
        "targets": targets,
        "cells": cells,
        "interpretation": (
            "benchmark_not_recorded means this explicit compact benchmark has no "
            "self-contained row; it is not evidence that the source-target pair "
            "was never run"
        ),
    }


def build_benchmark(config: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    records = [record for spec in config["specs"] for record in _record(spec, base_dir)]
    return {
        "schema_version": "ampgent.source-effect-benchmark.1",
        "benchmark_id": config.get("benchmark_id", "ampgent_source_effect_20260904"),
        "observed_at_utc": config.get("observed_at_utc") or datetime.now(UTC).isoformat(),
        "md_observation": config.get("md_observation"),
        "identity_policy": (
            "never merge sequences across run_id; source splits remain inside one run"
        ),
        "denominator_policy": (
            "proposal, materialized, and downstream rates retain explicit stage "
            "denominators"
        ),
        "excluded_scopes": config.get("excluded_scopes", []),
        "records": records,
        "coverage_matrix": _coverage_matrix(records),
        "next_operator": _next_operator(records, base_dir),
        "weighted_total_used": False,
        "shadow_runtime_policy": "runtime_unavailable is structured coverage, never a pass",
    }


CSV_FIELDS = [
    "label", "source", "source_scope", "evidence_strength", "target_key", "run_id",
    "candidate_id_kind",
    "proposal", "materialized", "formal12", "display", "activity_support_ge_2",
    "excellent", "challenger_reviewed", "challenger_no_conflict", "challenger_conflict",
    "qd_eligible", "qd_new_cell", "qd_replacement", "rosetta_evaluated_candidates",
    "rosetta_evaluated_decoys", "rosetta_passed_candidates", "rosetta_pending_candidates",
    "pool_a_admitted",
    "md_complete", "md_full_evidence", "best_quality", "mean_quality",
    "valid_cell_coverage", "archive_qd_score", "maximum_cell_concentration",
    "archive_relative_novelty", "support_rate", "qd_rate", "sequence_identity_unique",
]


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in records:
            counts = record["counts"]
            qd = record["qd_metrics"]
            writer.writerow(
                {
                    "label": record["label"], "source": record["source"],
                    "source_scope": record["source_scope"],
                    "evidence_strength": record["evidence_strength"],
                    "target_key": record["target_key"],
                    "run_id": record["run_id"], "candidate_id_kind": record["candidate_id_kind"],
                    **counts, "best_quality": qd["best_quality"],
                    "mean_quality": qd["mean_quality"],
                    "valid_cell_coverage": qd["valid_cell_coverage"],
                    "archive_qd_score": qd["archive_qd_score"],
                    "maximum_cell_concentration": qd["maximum_cell_concentration"],
                    "archive_relative_novelty": qd["archive_relative_novelty"],
                    "support_rate": record["rates"][
                        "materialized_to_activity_support_ge_2"
                    ]["rate"],
                    "qd_rate": record["rates"]["materialized_to_qd_eligible"]["rate"],
                    "sequence_identity_unique": record["within_group_unique_sequence_sha256"],
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--observed-at-utc")
    args = parser.parse_args()
    config = _load_json(args.config)
    if args.observed_at_utc:
        config["observed_at_utc"] = args.observed_at_utc
    result = build_benchmark(config, args.config.resolve().parents[2])
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    write_csv(result["records"], args.output_csv)


if __name__ == "__main__":
    main()
