"""Persist the final GyrA QD selection evidence without changing candidates."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    persist_operational_call,
)
from pepagent.db.models import Candidate, Evaluation
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json

SCHEMA_VERSION = "ampgent.gyra-qd-archive-evidence.1"
TOOL_NAME = "autoresearch_qd_archive_evidence"
TOOL_VERSION = "2026.09.04-v1"
MODEL_RELEASE_KEY = "ampgent_qd_archive_v1"
MATERIALIZATION_RUN_ID = uuid.UUID("415f370f-ad2e-54d4-9e1d-b29b2be6e8eb")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _inputs(report_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    qd = _load(report_dir / "provisional_qd.json")
    readback = _load(report_dir / "pg_materialization_readback.json")
    if not readback.get("complete") or readback.get("drift") != 0:
        raise ValueError("materialization readback is incomplete or drifted")
    if readback.get("run_id") != str(MATERIALIZATION_RUN_ID):
        raise ValueError("materialization run identity drifted")
    authoritative = readback.get("authoritative_candidates") or []
    if len(authoritative) != 8 or readback.get("candidate_count") != 8:
        raise ValueError("expected exactly eight authoritative candidates")
    by_hash = {
        str(item["candidate_id"]): item
        for item in qd.get("contributions", [])
        if item.get("contribution") in {"empty_cell", "incumbent_replacement"}
    }
    selected: list[dict[str, Any]] = []
    for item in authoritative:
        candidate_id = str(uuid.UUID(str(item["candidate_id"])))
        sequence_sha256 = str(item["sequence_sha256"]).lower()
        contribution = by_hash.get(sequence_sha256)
        if contribution is None:
            raise ValueError(f"QD contribution missing for {sequence_sha256}")
        if contribution["contribution"] not in {"empty_cell", "incumbent_replacement"}:
            raise ValueError("non-contributing candidate entered final QD evidence")
        selected.append(
            {
                "candidate_id": candidate_id,
                "sequence_sha256": sequence_sha256,
                "cell_id": str(contribution["cell_id"]),
                "contribution": str(contribution["contribution"]),
                "quality": float(contribution["quality"]),
                "incumbent_candidate_id": contribution.get("incumbent_candidate_id"),
            }
        )
    selected.sort(key=lambda item: item["sequence_sha256"])
    if len({item["sequence_sha256"] for item in selected}) != 8:
        raise ValueError("final QD evidence contains duplicate sequences")
    return qd, selected


def _metric_specs(
    qd: dict[str, Any], selected: list[dict[str, Any]], qd_sha256: str
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for item in selected:
        raw = {
            "schema_version": SCHEMA_VERSION,
            "evidence_semantics": "formal_pg_new_qd_contribution_after_exact_history",
            "source_qd_sha256": qd_sha256,
            "source_archive_sha256": qd["archive_sha256"],
            "source_batch_csv_sha256": qd["batch_csv_sha256"],
            "source_sequence_sha256": item["sequence_sha256"],
            "materialization_run_id": str(MATERIALIZATION_RUN_ID),
            "cell_id": item["cell_id"],
            "contribution": item["contribution"],
            "incumbent_candidate_id": item["incumbent_candidate_id"],
        }
        common = {
            "candidate_id": uuid.UUID(item["candidate_id"]),
            "subject_run_id": MATERIALIZATION_RUN_ID,
            "evidence_role": None,
            "evidence_family": "quality_diversity",
            "model_release_key": MODEL_RELEASE_KEY,
            "applicability_status": "applicable",
            "conflict_status": "not_assessed",
            "status": "succeeded",
            "out_of_domain": False,
            "limitations_json": [
                "QD selection evidence is derived from frozen local archive inputs",
                "PG evidence binds the already materialized candidate; no Candidate mutation",
            ],
            "raw_json": raw,
        }
        specs.extend(
            [
                {
                    **common,
                    "metric_name": "qd_contribution",
                    "numeric_value": None,
                    "text_value": item["contribution"],
                    "unit": None,
                },
                {
                    **common,
                    "metric_name": "qd_cell_id",
                    "numeric_value": None,
                    "text_value": item["cell_id"],
                    "unit": None,
                },
                {
                    **common,
                    "metric_name": "qd_quality",
                    "numeric_value": item["quality"],
                    "text_value": None,
                    "unit": "dimensionless",
                },
            ]
        )
    return specs


def _record(
    qd: dict[str, Any], selected: list[dict[str, Any]], qd_sha256: str
) -> OperationalCallRecord:
    output = {
        "schema_version": SCHEMA_VERSION,
        "materialization_run_id": str(MATERIALIZATION_RUN_ID),
        "source_qd_sha256": qd_sha256,
        "authoritative_candidate_count": len(selected),
        "new_cell_count": sum(item["contribution"] == "empty_cell" for item in selected),
        "replacement_count": sum(
            item["contribution"] == "incumbent_replacement" for item in selected
        ),
        "qd_metric_count": len(selected) * 3,
        "candidate_mutations": 0,
        "historical_runs_modified": False,
    }
    return OperationalCallRecord(
        operation_key=f"gyra:generation3:qd-archive-evidence:{qd_sha256}",
        target_key="gyra",
        purpose="audit_reconciliation",
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        status="succeeded",
        input_payload={
            "schema_version": SCHEMA_VERSION,
            "materialization_run_id": str(MATERIALIZATION_RUN_ID),
            "source_qd_sha256": qd_sha256,
            "source_archive_sha256": qd["archive_sha256"],
            "authoritative_candidates": selected,
        },
        parameters={
            "append_only": True,
            "candidate_mutations": 0,
            "historical_runs_modified": False,
        },
        execution_context={
            "execution_mode": "local_postgresql_only",
            "remote_compute_used": False,
            "gpu_rosetta_md_submitted": False,
            "source_schema_version": qd["schema_version"],
        },
        output_payload=output,
        actor=TOOL_NAME,
    )


def _same_evaluation(existing: Evaluation, expected: dict[str, Any]) -> bool:
    return all(
        getattr(existing, field) == expected[field]
        for field in (
            "candidate_id",
            "subject_run_id",
            "evidence_role",
            "evidence_family",
            "model_release_key",
            "applicability_status",
            "conflict_status",
            "metric_name",
            "numeric_value",
            "text_value",
            "unit",
            "status",
            "out_of_domain",
            "limitations_json",
            "raw_json",
        )
    )


async def persist(report_dir: Path) -> dict[str, Any]:
    qd, selected = _inputs(report_dir)
    qd_sha256 = sha256_file(report_dir / "provisional_qd.json")
    record = _record(qd, selected, qd_sha256)
    specs = _metric_specs(qd, selected, qd_sha256)
    expected_by_key = {
        (spec["candidate_id"], spec["metric_name"]): spec for spec in specs
    }
    async with SessionFactory() as session, session.begin():
        run, call = await persist_operational_call(session, record)
        candidate_ids = [item["candidate_id"] for item in selected]
        candidates = list(
            await session.scalars(
                select(Candidate).where(Candidate.id.in_(candidate_ids))
            )
        )
        candidates_by_id = {str(candidate.id): candidate for candidate in candidates}
        if len(candidates_by_id) != len(candidate_ids):
            raise ValueError("authoritative candidate readback is incomplete")
        for item in selected:
            candidate = candidates_by_id[item["candidate_id"]]
            if (
                candidate.run_id != MATERIALIZATION_RUN_ID
                or candidate.sequence_sha256 != item["sequence_sha256"]
            ):
                raise ValueError("candidate identity or subject run drifted")
        existing = list(
            await session.scalars(
                select(Evaluation).where(
                    Evaluation.tool_call_id == call.id,
                    Evaluation.candidate_id.in_(candidate_ids),
                    Evaluation.metric_name.in_({spec["metric_name"] for spec in specs}),
                )
            )
        )
        existing_by_key = {
            (evaluation.candidate_id, evaluation.metric_name): evaluation
            for evaluation in existing
        }
        for key, evaluation in existing_by_key.items():
            if not _same_evaluation(evaluation, expected_by_key[key]):
                raise ValueError("QD evidence retry identity drifted")
        new_evaluations = [
            Evaluation(**spec, tool_call_id=call.id)
            for key, spec in expected_by_key.items()
            if key not in existing_by_key
        ]
        session.add_all(new_evaluations)
        await session.flush()
        repository = ExperimentRepository(session)
        event_key = sha256_json(
            {
                "event": "autoresearch.qd_archive_evidence.materialized",
                "operation_run_id": str(run.id),
                "materialization_run_id": str(MATERIALIZATION_RUN_ID),
                "source_qd_sha256": qd_sha256,
            }
        )
        await repository.append_event(
            "run",
            run.id,
            "autoresearch.qd_archive_evidence.materialized",
            TOOL_NAME,
            {
                **record.output_payload,
                "tool_call_id": str(call.id),
                "inserted_evaluation_count": len(new_evaluations),
                "event_idempotency_key": event_key,
            },
            idempotency_key=event_key,
        )
        operation_run_id = run.id
        tool_call_id = call.id

    async with SessionFactory() as session:
        readback_evaluations = list(
            await session.scalars(
                select(Evaluation).where(Evaluation.tool_call_id == tool_call_id)
            )
        )
        counts = {
            metric: sum(evaluation.metric_name == metric for evaluation in readback_evaluations)
            for metric in ("qd_contribution", "qd_cell_id", "qd_quality")
        }
        if len(readback_evaluations) != 24 or set(counts.values()) != {8}:
            raise ValueError("QD evidence readback is incomplete")
        if any(
            evaluation.subject_run_id != MATERIALIZATION_RUN_ID
            or evaluation.model_release_key != MODEL_RELEASE_KEY
            for evaluation in readback_evaluations
        ):
            raise ValueError("QD evidence binding drifted")

    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "status": "readback_verified",
        "materialization_run_id": str(MATERIALIZATION_RUN_ID),
        "evidence_operation_run_id": str(operation_run_id),
        "evidence_tool_call_id": str(tool_call_id),
        "source_qd_sha256": qd_sha256,
        "authoritative_candidate_count": len(selected),
        "qd_evaluation_count": 24,
        "qd_metric_counts": counts,
        "new_cell_count": sum(item["contribution"] == "empty_cell" for item in selected),
        "replacement_count": sum(
            item["contribution"] == "incumbent_replacement" for item in selected
        ),
        "candidate_mutations": 0,
        "historical_runs_modified": False,
        "archive_replay_evidence": True,
        "drift": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = asyncio.run(persist(args.report_dir))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
