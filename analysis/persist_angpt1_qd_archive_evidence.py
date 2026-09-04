"""Persist exact ANGPT1 QD contribution evidence as an append-only audit run."""

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
from pepagent.db.models import Candidate, Evaluation, LifecycleEvent, ToolCall
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json

SCHEMA_VERSION = "ampgent.angpt1-qd-archive-evidence.1"
TOOL_NAME = "autoresearch_qd_archive_evidence"
TOOL_VERSION = "2026.09.04-v1"
MODEL_RELEASE_KEY = "ampgent_qd_archive_v1"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _inputs(report_dir: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    history = _json(report_dir / "pg_exact_history_receipt.json")
    materialization = _json(
        report_dir / "resume_v2b" / "materialization_receipt.json"
    )
    qd = _json(report_dir / "qd" / "qd_summary.json")
    if history.get("rejected_occurrence_count") != 0:
        raise ValueError("QD history contains a historical occurrence")
    if len(history.get("pg_new_hashes") or []) != 2:
        raise ValueError("exact history is not PG-new for both contributors")
    if int(materialization.get("materialized_or_reused_in_run_count", 0)) != 2:
        raise ValueError("materialization receipt does not contain two candidates")
    contributions = [
        item
        for item in qd.get("contributions", [])
        if item.get("contribution") in {"empty_cell", "incumbent_replacement"}
    ]
    if len(contributions) != 2:
        raise ValueError("expected exactly two QD contribution rows")
    if {str(item["candidate_id"]) for item in contributions} != set(
        history["pg_new_hashes"]
    ):
        raise ValueError("history and QD contributor identities disagree")
    return history, materialization, sorted(contributions, key=lambda item: item["candidate_id"])


def _record(
    qd: dict[str, Any],
    materialization: dict[str, Any],
    selected: list[dict[str, Any]],
    qd_sha256: str,
) -> OperationalCallRecord:
    output = {
        "schema_version": SCHEMA_VERSION,
        "materialization_run_id": materialization["operational_run_id"],
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
        operation_key=(
            f"angpt1:qd-archive-evidence:{materialization['operational_run_id']}:{qd_sha256}"
        ),
        target_key="angpt1",
        purpose="audit_reconciliation",
        tool_name=TOOL_NAME,
        tool_version=TOOL_VERSION,
        status="succeeded",
        input_payload={
            "schema_version": SCHEMA_VERSION,
            "materialization_run_id": materialization["operational_run_id"],
            "source_qd_sha256": qd_sha256,
            "source_archive_sha256": qd["archive_sha256"],
            "contributors": selected,
        },
        parameters={"append_only": True, "candidate_mutations": 0},
        execution_context={
            "execution_mode": "local_postgresql_only",
            "remote_compute_used": False,
            "gpu_rosetta_md_submitted": False,
        },
        output_payload=output,
        actor=TOOL_NAME,
    )


def _specs(
    qd: dict[str, Any],
    materialization: dict[str, Any],
    selected: list[dict[str, Any]],
    qd_sha256: str,
) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for item in selected:
        raw = {
            "schema_version": SCHEMA_VERSION,
            "evidence_semantics": "formal_pg_new_qd_contribution_after_exact_history",
            "source_qd_sha256": qd_sha256,
            "source_archive_sha256": qd["archive_sha256"],
            "source_batch_csv_sha256": qd["batch_csv_sha256"],
            "source_sequence_sha256": item["candidate_id"],
            "materialization_run_id": materialization["operational_run_id"],
            "cell_id": item["cell_id"],
            "contribution": item["contribution"],
            "incumbent_candidate_id": item.get("incumbent_candidate_id"),
        }
        common = {
            "subject_run_id": uuid.UUID(materialization["operational_run_id"]),
            "evidence_role": None,
            "evidence_family": "quality_diversity",
            "model_release_key": MODEL_RELEASE_KEY,
            "applicability_status": "applicable",
            "conflict_status": "not_assessed",
            "status": "succeeded",
            "out_of_domain": False,
            "limitations_json": [
                "QD selection is derived from the frozen local archive",
                "Evidence binds an existing PG-new candidate; no Candidate mutation",
            ],
            "raw_json": raw,
        }
        for metric_name, numeric_value, text_value, unit in (
            ("qd_contribution", None, item["contribution"], None),
            ("qd_cell_id", None, item["cell_id"], None),
            ("qd_quality", float(item["quality"]), None, "dimensionless"),
        ):
            specs.append(
                {
                    **common,
                    "candidate_id": uuid.UUID(item["candidate_uuid"]),
                    "metric_name": metric_name,
                    "numeric_value": numeric_value,
                    "text_value": text_value,
                    "unit": unit,
                }
            )
    return specs


async def persist(report_dir: Path) -> dict[str, Any]:
    history, materialization, selected = _inputs(report_dir)
    qd = _json(report_dir / "qd" / "qd_summary.json")
    qd_sha256 = sha256_file(report_dir / "qd" / "qd_summary.json")
    run_id = uuid.UUID(materialization["operational_run_id"])
    qd_hashes = [str(item["candidate_id"]) for item in selected]
    record = _record(qd, materialization, selected, qd_sha256)
    async with SessionFactory() as session, session.begin():
        evidence_run, evidence_call = await persist_operational_call(session, record)
        candidates = list(
            await session.scalars(
                select(Candidate).where(
                    Candidate.run_id == run_id,
                    Candidate.sequence_sha256.in_(qd_hashes),
                )
            )
        )
        candidates_by_hash = {candidate.sequence_sha256: candidate for candidate in candidates}
        if set(candidates_by_hash) != set(qd_hashes):
            raise ValueError("authoritative PG candidate readback is incomplete")
        selected_with_uuid = [
            dict(item, candidate_uuid=str(candidates_by_hash[item["candidate_id"]].id))
            for item in selected
        ]
        specs = _specs(qd, materialization, selected_with_uuid, qd_sha256)
        expected = {(spec["candidate_id"], spec["metric_name"]): spec for spec in specs}
        existing = list(
            await session.scalars(
                select(Evaluation).where(
                    Evaluation.tool_call_id == evidence_call.id,
                    Evaluation.candidate_id.in_([candidate.id for candidate in candidates]),
                    Evaluation.metric_name.in_([spec["metric_name"] for spec in specs]),
                )
            )
        )
        existing_by_key = {
            (evaluation.candidate_id, evaluation.metric_name): evaluation
            for evaluation in existing
        }
        for key, evaluation in existing_by_key.items():
            spec = expected[key]
            if any(getattr(evaluation, field) != spec[field] for field in (
                "candidate_id", "subject_run_id", "evidence_role", "evidence_family",
                "model_release_key", "applicability_status", "conflict_status", "metric_name",
                "numeric_value", "text_value", "unit", "status", "out_of_domain",
                "limitations_json", "raw_json",
            )):
                raise ValueError("QD evidence retry identity drifted")
        new_evaluations = [
            Evaluation(**spec, tool_call_id=evidence_call.id)
            for key, spec in expected.items()
            if key not in existing_by_key
        ]
        session.add_all(new_evaluations)
        await session.flush()
        event_key = sha256_json(
            {
                "event": "autoresearch.qd_archive_evidence.materialized",
                "run_id": str(evidence_run.id),
                "source_qd_sha256": qd_sha256,
            }
        )
        await ExperimentRepository(session).append_event(
            "run",
            evidence_run.id,
            "autoresearch.qd_archive_evidence.materialized",
            TOOL_NAME,
            {
                **record.output_payload,
                "tool_call_id": str(evidence_call.id),
                "inserted_evaluation_count": len(new_evaluations),
                "event_idempotency_key": event_key,
            },
            idempotency_key=event_key,
        )
        evidence_run_id, evidence_tool_call_id = evidence_run.id, evidence_call.id

    async with SessionFactory() as session:
        evaluations = list(
            await session.scalars(
                select(Evaluation).where(Evaluation.tool_call_id == evidence_tool_call_id)
            )
        )
        call = await session.get(ToolCall, evidence_tool_call_id)
        events = list(
            await session.scalars(
                select(LifecycleEvent).where(
                    LifecycleEvent.aggregate_type == "run",
                    LifecycleEvent.aggregate_id == evidence_run_id,
                    LifecycleEvent.event_type == "autoresearch.qd_archive_evidence.materialized",
                )
            )
        )
    metric_names = ("qd_contribution", "qd_cell_id", "qd_quality")
    counts = {
        metric: sum(e.metric_name == metric for e in evaluations)
        for metric in metric_names
    }
    if (
        call is None
        or call.status != "succeeded"
        or len(evaluations) != 6
        or set(counts.values()) != {2}
        or len(events) != 1
    ):
        raise ValueError("QD evidence readback is incomplete")
    if any(
        e.subject_run_id != run_id or e.model_release_key != MODEL_RELEASE_KEY
        for e in evaluations
    ):
        raise ValueError("QD evidence subject binding drifted")
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "status": "readback_verified",
        "materialization_run_id": str(run_id),
        "evidence_operation_run_id": str(evidence_run_id),
        "evidence_tool_call_id": str(evidence_tool_call_id),
        "source_qd_sha256": qd_sha256,
        "authoritative_candidate_count": 2,
        "qd_evaluation_count": 6,
        "qd_metric_counts": counts,
        "new_cell_count": sum(item["contribution"] == "empty_cell" for item in selected),
        "replacement_count": sum(
            item["contribution"] == "incumbent_replacement" for item in selected
        ),
        "candidate_mutations": 0,
        "historical_runs_modified": False,
        "archive_replay_evidence": True,
        "drift": 0,
        "history_receipt_sha256": sha256_json(history),
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
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
