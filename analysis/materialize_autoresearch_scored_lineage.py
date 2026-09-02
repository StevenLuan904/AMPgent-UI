from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    operational_run_id,
    persist_operational_call,
)
from pepagent.db.models import Candidate, Evaluation
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json, sha256_text

SCHEMA_VERSION = "ampgent.autoresearch-scored-lineage-materialization.1"
TOOL_NAME = "autoresearch_scored_lineage_materializer"
TOOL_VERSION = "2026.09.01-v1"
DEFAULT_BATCH_SIZE = 200

NUMERIC_METRICS: dict[str, str] = {
    "amp_read_log10_mic_um": "log10(uM)",
    "guruprasad_instability_index": "dimensionless",
    "hydrophobic_moment_eisenberg": "dimensionless",
    "hydrophobic_ratio_modlamp": "fraction",
    "llamp_log10_mic_um": "log10(uM)",
    "macrel_amp_probability": "probability",
    "macrel_hemolysis_probability": "probability",
    "maximum_hydrophobic_run": "residues",
    "net_charge_ph7_4": "elementary_charge",
    "toxinpred3_hybrid_score": "score",
}
TEXT_METRICS: dict[str, str | None] = {
    "macrel_hemolysis_label": None,
    "toxinpred3_label": None,
}
FORMAL_METRICS = tuple(NUMERIC_METRICS) + tuple(TEXT_METRICS)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _require_sha256(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64:
        raise ValueError(f"{field_name} must be a SHA-256 digest")
    int(normalized, 16)
    return normalized


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() == "true"


def _finite_float(value: str, field_name: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite")
    return parsed


def _validate_rows(rows: Sequence[Mapping[str, str]]) -> tuple[str, int]:
    if not rows:
        raise ValueError("scored lineage materialization requires candidate rows")
    branches = {str(row.get("branch_key") or "").strip() for row in rows}
    generations = {int(str(row.get("generation") or "-1")) for row in rows}
    if len(branches) != 1 or "" in branches:
        raise ValueError("one materialization batch must contain one branch")
    if len(generations) != 1 or min(generations) < 1:
        raise ValueError("one materialization batch must contain one positive generation")
    identities: set[str] = set()
    sequences: set[str] = set()
    for row in rows:
        sequence = "".join(str(row.get("sequence") or "").split()).upper()
        digest = _require_sha256(str(row.get("sequence_sha256") or ""), "sequence_sha256")
        if sha256_text(sequence) != digest:
            raise ValueError("candidate sequence identity drifted")
        if digest in identities or sequence in sequences:
            raise ValueError("candidate score input contains duplicate sequences")
        identities.add(digest)
        sequences.add(sequence)
        if not _as_bool(row.get("formal_12_complete")):
            raise ValueError("candidate row is not formal-12 complete")
        for metric in NUMERIC_METRICS:
            _finite_float(str(row.get(metric) or ""), metric)
        for metric in TEXT_METRICS:
            if not str(row.get(metric) or "").strip():
                raise ValueError(f"{metric} is missing")
        if _finite_float(
            str(row["guruprasad_instability_index"]),
            "guruprasad_instability_index",
        ) > 50.0 and _as_bool(row.get("display_eligible")):
            raise ValueError("display eligibility conflicts with the <=50 instability gate")
    return next(iter(branches)), next(iter(generations))


def _challenger_by_sequence(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    rows = _read_csv(path)
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        digest = _require_sha256(str(row.get("sequence_sha256") or ""), "challenger SHA")
        if digest in result:
            raise ValueError("challenger review contains duplicate sequence identities")
        for field_name in (
            "hemopi2_classification_score",
            "calibrated_hemolysis_probability",
            "hemopi2_hc50_um",
            "missing_verified_runtimes",
        ):
            if not str(row.get(field_name) or "").strip():
                raise ValueError(f"challenger review is missing {field_name}")
        result[digest] = row
    return result


async def _existing_candidates(
    sequence_sha256s: Sequence[str], expected_run_id: uuid.UUID
) -> dict[str, Candidate]:
    async with SessionFactory() as session:
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.sequence_sha256.in_(list(sequence_sha256s)))
                .order_by(
                    (Candidate.run_id == expected_run_id).desc(),
                    Candidate.created_at,
                    Candidate.id,
                )
            )
        )
    result: dict[str, Candidate] = {}
    for candidate in candidates:
        result.setdefault(candidate.sequence_sha256, candidate)
    return result


def _record(
    *,
    branch: str,
    generation: int,
    score_sha256: str,
    source_receipt_sha256: str,
    source_commit: str,
    candidate_count: int,
    accepted_count: int,
    duplicate_count: int,
    excellent_count: int,
    challenger_reviewed_count: int,
    challenger_no_conflict_count: int,
) -> OperationalCallRecord:
    now = datetime.now(UTC)
    return OperationalCallRecord(
        operation_key=(
            f"materialize-scored-lineage:{source_commit}:{score_sha256}:{branch}:"
            f"generation-{generation}"
        ),
        target_key=branch,  # type: ignore[arg-type]
        purpose="generation",
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        status="succeeded",
        input_payload={
            "candidate_scores_sha256": score_sha256,
            "source_receipt_sha256": source_receipt_sha256,
            "candidate_count": candidate_count,
            "generation": generation,
        },
        parameters={
            "formal_metric_names": list(FORMAL_METRICS),
            "instability_hard_gate": "successful finite <=50",
            "global_exact_replays": "skip_materialization_keep_operational_audit",
        },
        execution_context={
            "source_commit": source_commit,
            "execution_mode": "postgresql_transactional_evidence_import",
            "historical_runs_modified": False,
            "temporal_used": False,
            "gpu_used": False,
        },
        output_payload={
            "candidate_count": candidate_count,
            "materialized_or_reused_in_run_count": accepted_count,
            "global_exact_replay_skip_count": duplicate_count,
            "formal_evaluation_count": accepted_count * len(FORMAL_METRICS),
            "excellent_sequence_stage_count": excellent_count,
            "challenger_reviewed_count": challenger_reviewed_count,
            "challenger_no_conflict_count": challenger_no_conflict_count,
        },
        queued_at=now,
        started_at=now,
        finished_at=now,
    )


def _candidate_metadata(
    row: Mapping[str, str], challenger: Mapping[str, str] | None, score_sha256: str
) -> dict[str, Any]:
    conflict_status = (
        str(
            challenger.get("challenger_conflict_status")
            or challenger.get("conflict_status")
            or ""
        )
        if challenger
        else "not_reviewed"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source_candidate_scores_sha256": score_sha256,
        "candidate_source": row.get("source"),
        "source_raw_rank": int(row["raw_rank"]) if row.get("raw_rank") else None,
        "source_structure_file": row.get("remote_structure_file"),
        "branch_key": row["branch_key"],
        "operator_id": row.get("operator_id"),
        "action_sha256": row.get("action_sha256"),
        "action_type": row.get("action_type"),
        "parent_sequence_sha256": row.get("parent_sequence_sha256"),
        "family_key_80_80": row.get("family_key_80_80"),
        "display_eligible": _as_bool(row.get("display_eligible")),
        "activity_model_support_count": int(
            str(row.get("activity_model_support_count_calibrated") or "0")
        ),
        "excellent_sequence_stage": _as_bool(
            row.get("excellent_sequence_stage_calibrated")
        ),
        "challenger_conflict_status": conflict_status,
        "structure_status": "not_started",
        "md_status": "not_started",
    }


def _evaluation_rows(
    candidate: Candidate,
    tool_call_id: uuid.UUID,
    subject_run_id: uuid.UUID,
    row: Mapping[str, str],
    score_sha256: str,
) -> list[Evaluation]:
    raw = {
        "schema_version": SCHEMA_VERSION,
        "source_candidate_scores_sha256": score_sha256,
        "source_sequence_sha256": row["sequence_sha256"],
        "import_semantics": "frozen_score_all_evidence_materialization",
    }
    evaluations: list[Evaluation] = []
    for metric, unit in NUMERIC_METRICS.items():
        evaluations.append(
            Evaluation(
                candidate_id=candidate.id,
                tool_call_id=tool_call_id,
                subject_run_id=subject_run_id,
                evidence_role="primary",
                evidence_family="score_all",
                model_release_key="ampgent_formal12_frozen",
                applicability_status="applicable",
                conflict_status="not_assessed",
                metric_name=metric,
                numeric_value=_finite_float(str(row[metric]), metric),
                text_value=None,
                unit=unit,
                status="succeeded",
                out_of_domain=(
                    metric == "guruprasad_instability_index"
                    and len(candidate.sequence) < 20
                ),
                limitations_json=(
                    ["Guruprasad was derived from proteins; OOD is audit-only"]
                    if metric == "guruprasad_instability_index"
                    else []
                ),
                raw_json=raw,
            )
        )
    for metric, unit in TEXT_METRICS.items():
        evaluations.append(
            Evaluation(
                candidate_id=candidate.id,
                tool_call_id=tool_call_id,
                subject_run_id=subject_run_id,
                evidence_role="primary",
                evidence_family="score_all",
                model_release_key="ampgent_formal12_frozen",
                applicability_status="applicable",
                conflict_status="not_assessed",
                metric_name=metric,
                numeric_value=None,
                text_value=str(row[metric]).strip(),
                unit=unit,
                status="succeeded",
                out_of_domain=False,
                limitations_json=[],
                raw_json=raw,
            )
        )
    return evaluations


def _challenger_evaluation_rows(
    candidate: Candidate,
    tool_call_id: uuid.UUID,
    subject_run_id: uuid.UUID,
    challenger: Mapping[str, str],
) -> list[Evaluation]:
    conflict_status = str(
        challenger.get("challenger_conflict_status")
        or challenger.get("conflict_status")
        or "not_assessed"
    )
    raw_json = {
        "schema_version": SCHEMA_VERSION,
        "evidence_semantics": "independent_challenger_not_primary_hard_gate",
        "source_record": dict(challenger),
    }
    hemopi2_metrics = (
        ("hemopi2_classification_score", "probability"),
        ("calibrated_hemolysis_probability", "probability"),
        ("hemopi2_hc50_um", "uM"),
    )
    evaluations = [
        Evaluation(
            candidate_id=candidate.id,
            tool_call_id=tool_call_id,
            subject_run_id=subject_run_id,
            evidence_role="challenger",
            evidence_family="hemolysis",
            model_release_key="hemopi2_v27_calibrated_v39",
            applicability_status="applicable",
            conflict_status=conflict_status,
            metric_name=metric_name,
            numeric_value=_finite_float(str(challenger[metric_name]), metric_name),
            text_value=None,
            unit=unit,
            status="succeeded",
            out_of_domain=False,
            limitations_json=["Independent challenger; not a primary hard gate"],
            raw_json=raw_json,
        )
        for metric_name, unit in hemopi2_metrics
    ]
    missing = {
        item.strip().lower()
        for item in str(challenger.get("missing_verified_runtimes") or "").split(";")
        if item.strip()
    }
    for model_key in ("apex", "peptiverse"):
        if model_key not in missing:
            continue
        evaluations.append(
            Evaluation(
                candidate_id=candidate.id,
                tool_call_id=tool_call_id,
                subject_run_id=subject_run_id,
                evidence_role="shadow",
                evidence_family="multiobjective_safety_activity",
                model_release_key=f"{model_key}_runtime_inventory_v1",
                applicability_status="runtime_unavailable",
                conflict_status="not_assessed",
                metric_name=f"{model_key}_shadow_status",
                numeric_value=None,
                text_value="runtime_unavailable",
                unit=None,
                status="unsupported",
                out_of_domain=False,
                limitations_json=[
                    "Verified runtime unavailable; no scientific pass/fail inferred"
                ],
                raw_json=raw_json,
            )
        )
    return evaluations


async def materialize(args: argparse.Namespace, *, execute: bool) -> dict[str, Any]:
    rows = _read_csv(args.candidate_scores)
    branch, generation = _validate_rows(rows)
    challengers = _challenger_by_sequence(args.challenger_review)
    score_sha256 = sha256_file(args.candidate_scores)
    source_receipt_sha256 = sha256_file(args.source_receipt)
    if len(args.source_commit) != 40 or set(args.source_commit) - set("0123456789abcdef"):
        raise ValueError("source commit must be a lowercase SHA-1")
    preliminary = _record(
        branch=branch,
        generation=generation,
        score_sha256=score_sha256,
        source_receipt_sha256=source_receipt_sha256,
        source_commit=args.source_commit,
        candidate_count=len(rows),
        accepted_count=0,
        duplicate_count=0,
        excellent_count=0,
        challenger_reviewed_count=0,
        challenger_no_conflict_count=0,
    )
    expected_run_id = operational_run_id(preliminary)
    existing = await _existing_candidates(
        [row["sequence_sha256"] for row in rows], expected_run_id
    )
    accepted_rows = [
        row
        for row in rows
        if row["sequence_sha256"] not in existing
        or existing[row["sequence_sha256"]].run_id == expected_run_id
    ]
    duplicate_rows = [row for row in rows if row not in accepted_rows]
    missing_challenger = [
        row["sequence_sha256"]
        for row in accepted_rows
        if row["sequence_sha256"] not in challengers
    ]
    if missing_challenger:
        raise ValueError(
            "materialization requires shadow/challenger evidence for every candidate"
        )
    excellent_count = sum(
        _as_bool(row.get("excellent_sequence_stage_calibrated")) for row in accepted_rows
    )
    reviewed = [row for row in accepted_rows if row["sequence_sha256"] in challengers]
    no_conflict_count = sum(
        (
            challengers[row["sequence_sha256"]].get("challenger_conflict_status")
            or challengers[row["sequence_sha256"]].get("conflict_status")
        )
        == "no_conflict"
        for row in reviewed
    )
    record = _record(
        branch=branch,
        generation=generation,
        score_sha256=score_sha256,
        source_receipt_sha256=source_receipt_sha256,
        source_commit=args.source_commit,
        candidate_count=len(rows),
        accepted_count=len(accepted_rows),
        duplicate_count=len(duplicate_rows),
        excellent_count=excellent_count,
        challenger_reviewed_count=len(reviewed),
        challenger_no_conflict_count=no_conflict_count,
    )
    summary: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "executed": execute,
        "branch_key": branch,
        "generation": generation,
        "operational_run_id": str(expected_run_id),
        "candidate_scores_sha256": score_sha256,
        "source_receipt_sha256": source_receipt_sha256,
        "source_candidate_count": len(rows),
        "materialized_or_reused_in_run_count": len(accepted_rows),
        "global_exact_replay_skip_count": len(duplicate_rows),
        "formal_evaluation_count": len(accepted_rows) * len(FORMAL_METRICS),
        "excellent_sequence_stage_count": excellent_count,
        "challenger_reviewed_count": len(reviewed),
        "challenger_no_conflict_count": no_conflict_count,
        "duplicate_sequence_sha256s": [row["sequence_sha256"] for row in duplicate_rows],
        "historical_runs_modified": False,
    }
    if not execute:
        summary["receipt_payload_sha256"] = sha256_json(summary)
        return summary

    batch_size = int(args.batch_size)
    if batch_size < 1:
        raise ValueError("batch size must be positive")
    running_record = record.model_copy(
        update={"status": "running", "output_payload": None, "finished_at": None}
    )
    async with SessionFactory() as session, session.begin():
        await persist_operational_call(session, running_record)

    inserted_evaluation_count = 0
    for offset in range(0, len(accepted_rows), batch_size):
        batch = accepted_rows[offset : offset + batch_size]
        async with SessionFactory() as session, session.begin():
            run, call = await persist_operational_call(session, running_record)
            repository = ExperimentRepository(session)
            parent_hashes = sorted(
                {
                    str(row.get("parent_sequence_sha256") or "")
                    for row in batch
                    if row.get("parent_sequence_sha256")
                }
            )
            parent_candidates = list(
                await session.scalars(
                    select(Candidate)
                    .where(Candidate.sequence_sha256.in_(parent_hashes))
                    .order_by(Candidate.created_at, Candidate.id)
                )
            )
            parents: dict[str, Candidate] = {}
            for parent in parent_candidates:
                parents.setdefault(parent.sequence_sha256, parent)
            materialized: list[tuple[Candidate, Mapping[str, str]]] = []
            for batch_index, row in enumerate(batch):
                proposal_rank = offset + batch_index + 1
                digest = row["sequence_sha256"]
                current = existing.get(digest)
                parent = parents.get(str(row.get("parent_sequence_sha256") or ""))
                metadata = _candidate_metadata(
                    row, challengers.get(digest), score_sha256
                )
                if current is None:
                    current = await repository.add_candidate(
                        run.id,
                        row["sequence"],
                        generation=generation,
                        proposal_rank=proposal_rank,
                        generator_call_id=call.id,
                        parent_id=parent.id if parent else None,
                        metadata=metadata,
                        actor=TOOL_NAME,
                    )
                elif (
                    current.run_id != run.id
                    or current.sequence_sha256 != digest
                    or current.generation != generation
                    or current.metadata_json.get("source_candidate_scores_sha256")
                    != score_sha256
                ):
                    raise ValueError("materialized candidate retry identity drifted")
                materialized.append((current, row))
            candidate_ids = [candidate.id for candidate, _ in materialized]
            existing_evaluations = set(
                (
                    await session.execute(
                        select(Evaluation.candidate_id, Evaluation.metric_name).where(
                            Evaluation.candidate_id.in_(candidate_ids),
                            Evaluation.tool_call_id == call.id,
                        )
                    )
                ).all()
            )
            new_evaluations: list[Evaluation] = []
            for candidate, row in materialized:
                for evaluation in _evaluation_rows(
                    candidate, call.id, run.id, row, score_sha256
                ):
                    if (candidate.id, evaluation.metric_name) not in existing_evaluations:
                        new_evaluations.append(evaluation)
                challenger = challengers.get(candidate.sequence_sha256)
                if challenger is None:
                    raise ValueError(
                        "materialized candidate is missing challenger evidence"
                    )
                for evaluation in _challenger_evaluation_rows(
                    candidate, call.id, run.id, challenger
                ):
                    if (candidate.id, evaluation.metric_name) not in existing_evaluations:
                        new_evaluations.append(evaluation)
            session.add_all(new_evaluations)
            await session.flush()
            inserted_evaluation_count += len(new_evaluations)

    async with SessionFactory() as session, session.begin():
        run, call = await persist_operational_call(session, record)
        repository = ExperimentRepository(session)
        event_key = sha256_json(
            {
                "event": "autoresearch.scored_lineage.materialized",
                "run_id": str(run.id),
                "candidate_scores_sha256": score_sha256,
            }
        )
        await repository.append_event(
            "run",
            run.id,
            "autoresearch.scored_lineage.materialized",
            TOOL_NAME,
            {
                **summary,
                "tool_call_id": str(call.id),
                "inserted_evaluation_count": inserted_evaluation_count,
                "event_idempotency_key": event_key,
            },
            idempotency_key=event_key,
        )
        summary["tool_call_id"] = str(call.id)
        summary["inserted_evaluation_count"] = inserted_evaluation_count
        summary["batch_size"] = batch_size
    summary["receipt_payload_sha256"] = sha256_json(summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-scores", type=Path, required=True)
    parser.add_argument("--challenger-review", type=Path, required=True)
    parser.add_argument("--source-receipt", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run-output", type=Path)
    mode.add_argument("--execute-output", type=Path)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()
    output = args.execute_output or args.dry_run_output
    assert output is not None
    if output.exists():
        raise FileExistsError("materialization receipt output is append-only")
    payload = asyncio.run(materialize(args, execute=args.execute_output is not None))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
