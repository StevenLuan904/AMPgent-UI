from __future__ import annotations

import argparse
import asyncio
import csv
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pepagent.db.models import Candidate, Evaluation, ExperimentRun, ToolCall
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.settings import get_settings
from pepagent.workers.autoresearch_activities import _select_complete_evidence

PARENT_RUNS = {
    "acea": uuid.UUID("2e6f38f2-4730-57cc-b149-8c54eda82cd9"),
    "angpt1": uuid.UUID("2bcb662d-da67-51e3-adbe-997d3aacad89"),
    "fgf2": uuid.UUID("eb85d014-e7b3-5f6c-acab-34ac580b30e1"),
    "gyra": uuid.UUID("15ea9977-4ea1-52a6-bcf0-f6e620803d19"),
    "pbp2a": uuid.UUID("bde9b74d-84a0-50c4-9002-8ae419d937e3"),
    "vegfa": uuid.UUID("7490f36c-8f0a-5908-af1d-de1fa97f09cf"),
}
ACTIVITY_METRICS = (
    ("amp_read_log10_mic_um", "minimize"),
    ("llamp_log10_mic_um", "minimize"),
    ("macrel_amp_probability", "maximize"),
)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write empty calibrated cohort")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


async def _parent_metric_values(branch_key: str) -> dict[str, list[float]]:
    run_id = PARENT_RUNS[branch_key]
    async with SessionFactory() as session:
        run = await session.get(ExperimentRun, run_id)
        if run is None:
            raise ValueError(f"missing parent run: {run_id}")
        request = dict((run.spec_json or {}).get("workflow_request") or {})
        required_metrics = set(
            (request.get("execution_contract") or {}).get("required_sequence_metrics") or ()
        )
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == run_id)
                .order_by(Candidate.generation, Candidate.proposal_rank, Candidate.id)
            )
        )
        candidate_ids = [candidate.id for candidate in candidates]
        evaluations = list(
            await session.scalars(
                select(Evaluation).where(Evaluation.candidate_id.in_(candidate_ids))
            )
        )
        calls = {
            call.id: call
            for call in await session.scalars(
                select(ToolCall).where(
                    ToolCall.id.in_({evaluation.tool_call_id for evaluation in evaluations})
                )
            )
        }
        evidence = _select_complete_evidence(
            candidates=candidates,
            evaluations=evaluations,
            calls=calls,
            required_metrics=required_metrics,
        )
    result: dict[str, list[float]] = {}
    for metric_name, _ in ACTIVITY_METRICS:
        result[metric_name] = [
            float(candidate.metrics[metric_name].numeric_value)
            for candidate in evidence
            if candidate.metrics[metric_name].numeric_value is not None
        ]
        if not result[metric_name]:
            raise ValueError(f"parent {branch_key} lacks {metric_name}")
    return result


def _parent_metric_values_sync(branch_key: str) -> dict[str, list[float]]:
    run_id = PARENT_RUNS[branch_key]
    settings = get_settings()
    engine = create_engine(
        settings.database_url_sync,
        pool_pre_ping=True,
        connect_args={"connect_timeout": max(1, int(settings.database_connect_timeout_seconds))},
    )
    try:
        with Session(engine) as session:
            run = session.get(ExperimentRun, run_id)
            if run is None:
                raise ValueError(f"missing parent run: {run_id}")
            request = dict((run.spec_json or {}).get("workflow_request") or {})
            required_metrics = set(
                (request.get("execution_contract") or {}).get("required_sequence_metrics") or ()
            )
            candidates = list(
                session.scalars(
                    select(Candidate)
                    .where(Candidate.run_id == run_id)
                    .order_by(Candidate.generation, Candidate.proposal_rank, Candidate.id)
                )
            )
            candidate_ids = [candidate.id for candidate in candidates]
            evaluations = list(
                session.scalars(
                    select(Evaluation).where(Evaluation.candidate_id.in_(candidate_ids))
                )
            )
            calls = {
                call.id: call
                for call in session.scalars(
                    select(ToolCall).where(
                        ToolCall.id.in_({evaluation.tool_call_id for evaluation in evaluations})
                    )
                )
            }
            evidence = _select_complete_evidence(
                candidates=candidates,
                evaluations=evaluations,
                calls=calls,
                required_metrics=required_metrics,
            )
        result: dict[str, list[float]] = {}
        for metric_name, _ in ACTIVITY_METRICS:
            result[metric_name] = [
                float(candidate.metrics[metric_name].numeric_value)
                for candidate in evidence
                if candidate.metrics[metric_name].numeric_value is not None
            ]
            if not result[metric_name]:
                raise ValueError(f"parent {branch_key} lacks {metric_name}")
        return result
    finally:
        engine.dispose()


async def _parent_metric_values_with_fallback(
    branch_key: str,
) -> tuple[dict[str, list[float]], str]:
    try:
        return await _parent_metric_values(branch_key), "postgresql_asyncpg"
    except (TimeoutError, OSError) as error:
        reason = "timeout" if isinstance(error, TimeoutError) else "connection"
        return (
            await asyncio.to_thread(_parent_metric_values_sync, branch_key),
            f"postgresql_psycopg_{reason}_fallback",
        )


def _benefit_percentile(value: float, parents: list[float], direction: str) -> float:
    if direction == "minimize":
        return sum(parent >= value for parent in parents) / len(parents)
    return sum(parent <= value for parent in parents) / len(parents)


def _load_frozen_percentile_witnesses(
    branch_key: str,
    paths: list[Path],
) -> tuple[dict[str, list[tuple[float, float]]], list[str]]:
    witnesses = {metric_name: [] for metric_name, _ in ACTIVITY_METRICS}
    source_hashes: list[str] = []
    expected_run_id = str(PARENT_RUNS[branch_key])
    for path in paths:
        receipt_candidates = (
            path.parent / "calibration_receipt.json",
            path.parent / "activity_calibration_receipt.json",
            path.parent / "activity_support_receipt.json",
        )
        receipt_path = next(
            (candidate for candidate in receipt_candidates if candidate.exists()), None
        )
        if receipt_path is None:
            raise ValueError(f"fallback calibration witness lacks a receipt: {path}")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        if receipt.get("parent_run_ids", {}).get(branch_key) != expected_run_id:
            raise ValueError("fallback calibration witness parent run identity drifted")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        branch_rows = [row for row in rows if row["branch_key"] == branch_key]
        if not branch_rows:
            raise ValueError(f"fallback calibration witness lacks branch {branch_key}")
        for row in branch_rows:
            if row.get("activity_support_semantics") != (
                "at_or_above_parent_run_top_quartile_per_independent_model"
            ):
                raise ValueError("fallback calibration witness semantics drifted")
            for metric_name, _ in ACTIVITY_METRICS:
                witnesses[metric_name].append(
                    (
                        float(row[metric_name]),
                        float(row[f"{metric_name}__parent_benefit_percentile"]),
                    )
                )
        source_hashes.extend((sha256_file(path), sha256_file(receipt_path)))
    if any(not values for values in witnesses.values()):
        raise ValueError("fallback calibration witnesses are incomplete")
    return witnesses, source_hashes


def _benefit_percentile_lower_bound(
    value: float,
    witnesses: list[tuple[float, float]],
    direction: str,
) -> float:
    if direction == "minimize":
        bounds = [percentile for witness, percentile in witnesses if witness >= value]
    else:
        bounds = [percentile for witness, percentile in witnesses if witness <= value]
    return max(bounds, default=0.0)


async def _run(args: argparse.Namespace) -> None:
    with args.input_csv.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    branches = sorted({row["branch_key"] for row in rows})
    unknown = set(branches) - set(PARENT_RUNS)
    if unknown:
        raise ValueError(f"unknown branches: {sorted(unknown)}")
    reference: dict[str, dict[str, list[float]]] = {}
    fallback_witnesses: dict[str, dict[str, list[tuple[float, float]]]] = {}
    reference_sources: dict[str, str] = {}
    fallback_source_hashes: dict[str, list[str]] = {}
    for branch in branches:
        try:
            (
                reference[branch],
                reference_sources[branch],
            ) = await _parent_metric_values_with_fallback(branch)
        except SQLAlchemyError:
            if not args.fallback_calibrated_csv:
                raise
            fallback_witnesses[branch], fallback_source_hashes[branch] = (
                _load_frozen_percentile_witnesses(branch, args.fallback_calibrated_csv)
            )
            reference_sources[branch] = "frozen_calibrated_witness_monotonic_lower_bound"
    calibrated: list[dict[str, Any]] = []
    for row in rows:
        branch = row["branch_key"]
        if branch in fallback_witnesses:
            percentiles = {
                metric_name: _benefit_percentile_lower_bound(
                    float(row[metric_name]),
                    fallback_witnesses[branch][metric_name],
                    direction,
                )
                for metric_name, direction in ACTIVITY_METRICS
            }
            percentile_semantics = "monotonic_lower_bound_from_frozen_parent_percentile_witnesses"
        else:
            percentiles = {
                metric_name: _benefit_percentile(
                    float(row[metric_name]), reference[branch][metric_name], direction
                )
                for metric_name, direction in ACTIVITY_METRICS
            }
            percentile_semantics = "exact_parent_empirical_cdf"
        support = sum(value >= 0.75 for value in percentiles.values())
        display_eligible = str(row.get("display_eligible", "")).lower() == "true"
        calibrated.append(
            {
                **row,
                **{
                    f"{metric_name}__parent_benefit_percentile": f"{value:.6f}"
                    for metric_name, value in percentiles.items()
                },
                "activity_model_support_count_calibrated": support,
                "excellent_sequence_stage_calibrated": str(
                    display_eligible and support >= 2
                ).lower(),
                "activity_support_semantics": (
                    "at_or_above_parent_run_top_quartile_per_independent_model"
                ),
                "activity_support_percentile_semantics": percentile_semantics,
            }
        )
    calibrated.sort(
        key=lambda row: (
            row["branch_key"],
            row["excellent_sequence_stage_calibrated"] != "true",
            -int(row["activity_model_support_count_calibrated"]),
            row["sequence"],
        )
    )
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output_csv, calibrated)
    receipt = {
        "schema_version": "ampgent.autoresearch-activity-support-calibration.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "source_csv_sha256": sha256_file(args.input_csv),
        "parent_run_ids": {branch: str(PARENT_RUNS[branch]) for branch in branches},
        "reference_sources": reference_sources,
        "fallback_source_sha256s": fallback_source_hashes,
        "candidate_count": len(calibrated),
        "support_ge_2_count": sum(
            int(row["activity_model_support_count_calibrated"]) >= 2 for row in calibrated
        ),
        "excellent_sequence_stage_count": sum(
            row["excellent_sequence_stage_calibrated"] == "true" for row in calibrated
        ),
        "output_csv_sha256": sha256_file(args.output_csv),
        "historical_run_modified": False,
    }
    receipt["receipt_payload_sha256"] = sha256_json(receipt)
    args.output_json.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--fallback-calibrated-csv", type=Path, action="append", default=[])
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
