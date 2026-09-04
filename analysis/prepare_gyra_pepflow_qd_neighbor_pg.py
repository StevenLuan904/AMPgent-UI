"""Prepare strict GyrA QD winners and audit exact PostgreSQL history."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, select, text

from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_text


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _strict_rows(report_dir: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    scores = _csv(report_dir / "candidate_scores_calibrated.csv")
    challengers = _csv(report_dir / "challenger" / "challenger_review.csv")
    qd = json.loads((report_dir / "provisional_qd.json").read_text(encoding="utf-8"))
    qd_by_hash = {
        str(item["candidate_id"]): item
        for item in qd["contributions"]
        if item.get("contribution") in {"empty_cell", "incumbent_replacement"}
    }
    challenger_by_hash = {row["sequence_sha256"]: row for row in challengers}
    strict: list[dict[str, str]] = []
    for row in scores:
        digest = row["sequence_sha256"].lower()
        if digest not in qd_by_hash:
            continue
        if sha256_text(row["sequence"].strip().upper()) != digest:
            raise ValueError(f"sequence hash drifted: {digest}")
        challenger = challenger_by_hash.get(digest)
        if (
            row.get("formal_12_complete", "").casefold() != "true"
            or row.get("display_eligible", "").casefold() != "true"
            or int(row.get("activity_model_support_count_calibrated") or 0) < 2
            or challenger is None
            or not challenger.get("hemopi2_classification_score", "").strip()
            or not challenger.get("hemopi2_hc50_um", "").strip()
            or not challenger.get("calibrated_hemolysis_probability", "").strip()
            or challenger.get("challenger_conflict_status") != "no_conflict"
        ):
            continue
        strict.append(row)
    strict.sort(key=lambda row: row["sequence_sha256"])
    return strict, [challenger_by_hash[row["sequence_sha256"]] for row in strict]


async def _history(hashes: list[str]) -> dict[str, Any]:
    candidate_hits: list[Any] = []
    event_hits: list[Any] = []
    async with SessionFactory() as session:
        await session.execute(text("SET statement_timeout = '60000ms'"))
        await session.execute(text("SET application_name = 'ampgent-gyra-run3-exact-history'"))
        for batch_start in range(0, len(hashes), 4):
            batch = hashes[batch_start : batch_start + 4]
            candidate_hits.extend(
                (
                    await session.execute(
                        select(Candidate.id, Candidate.run_id, Candidate.sequence_sha256)
                        .where(Candidate.sequence_sha256.in_(batch))
                        .order_by(Candidate.sequence_sha256, Candidate.created_at, Candidate.id)
                    )
                ).all()
            )
            event_query = text(
                """
                SELECT DISTINCT le.id::text AS event_id,
                                le.aggregate_id::text AS aggregate_id,
                                item->>'sequence_sha256' AS sequence_sha256
                FROM lifecycle_events le
                CROSS JOIN LATERAL jsonb_array_elements(
                  CASE WHEN jsonb_typeof(le.payload_json->'output'->'candidates')='array'
                       THEN le.payload_json->'output'->'candidates'
                       ELSE '[]'::jsonb END
                ) item
                WHERE le.event_type='operational.call.succeeded'
                  AND le.payload_json->>'purpose'='score_all'
                  AND item->>'sequence_sha256' IN :hashes
                ORDER BY 3, 1
                """
            ).bindparams(bindparam("hashes", expanding=True))
            event_hits.extend(
                (await session.execute(event_query, {"hashes": batch})).all()
            )
        index_row = (
            await session.execute(
                text(
                    """
                    SELECT i.indisvalid, i.indisready, i.indislive
                    FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid
                    WHERE c.relname='ix_candidate_sequence_sha256'
                    """
                )
            )
        ).one_or_none()
        version = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
    candidate_hashes = {row.sequence_sha256 for row in candidate_hits}
    event_hashes = {row.sequence_sha256 for row in event_hits}
    historical = candidate_hashes | event_hashes
    return {
        "candidate_hits": [
            {
                "candidate_id": str(row.id),
                "run_id": str(row.run_id),
                "sequence_sha256": row.sequence_sha256,
            }
            for row in candidate_hits
        ],
        "operational_score_all_hits": [
            {
                "event_id": row.event_id,
                "aggregate_id": row.aggregate_id,
                "sequence_sha256": row.sequence_sha256,
            }
            for row in event_hits
        ],
        "candidate_hit_count": len(candidate_hits),
        "operational_score_all_hit_count": len(event_hits),
        "historical_hashes": sorted(historical),
        "alembic_version": version,
        "index_flags": None
        if index_row is None
        else {
            "valid": bool(index_row.indisvalid),
            "ready": bool(index_row.indisready),
            "live": bool(index_row.indislive),
        },
    }


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def prepare(report_dir: Path, output_dir: Path) -> dict[str, Any]:
    strict_scores, strict_challengers = _strict_rows(report_dir)
    history = asyncio.run(_history([row["sequence_sha256"] for row in strict_scores]))
    materialization_path = report_dir / "materialization_receipt.json"
    materialization = (
        json.loads(materialization_path.read_text(encoding="utf-8"))
        if materialization_path.exists()
        else None
    )
    materialized_run_id = (
        str(materialization["operational_run_id"]) if materialization else None
    )
    already_materialized = {
        row["sequence_sha256"]
        for row in history["candidate_hits"]
        if row["run_id"] == materialized_run_id
    }
    external_historical = set(history["historical_hashes"]) - already_materialized
    new_scores = [
        row
        for row in strict_scores
        if row["sequence_sha256"] not in external_historical
        and row["sequence_sha256"] not in already_materialized
    ]
    challenger_by_hash = {row["sequence_sha256"]: row for row in strict_challengers}
    new_challengers = [challenger_by_hash[row["sequence_sha256"]] for row in new_scores]
    output_dir.mkdir(parents=True, exist_ok=True)
    input_scores = new_scores
    input_challengers = new_challengers
    if not input_scores and already_materialized:
        input_scores = strict_scores
        input_challengers = strict_challengers
    if input_scores or not (output_dir / "candidate_scores.csv").exists():
        _write_csv(output_dir / "candidate_scores.csv", input_scores)
        _write_csv(output_dir / "challenger_review.csv", input_challengers)
    pre_materialization_new = (
        sorted(already_materialized)
        if already_materialized
        else [row["sequence_sha256"] for row in new_scores]
    )
    status = (
        "already_materialized"
        if already_materialized
        and len(already_materialized) == len(strict_scores)
        else "ready_for_exact_once"
        if new_scores
        else "proposal_only"
    )
    receipt = {
        "schema_version": "ampgent.gyra-pepflow-qd-neighbor.pg-exact-history.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "target_key": "GyrA",
        "generation": 3,
        "proposal_count": len(_csv(report_dir / "proposals.csv")),
        "strict_intersection_count": len(strict_scores),
        "strict_intersection_hashes": [row["sequence_sha256"] for row in strict_scores],
        "history_query_mode": "exact_hash_scan_fallback"
        if history["index_flags"] is not None and not all(history["index_flags"].values())
        else "exact_hash_indexed",
        "exact_history_status": "verified_exact_scan",
        "alembic_version": history["alembic_version"],
        "index_flags": history["index_flags"],
        "candidate_hits": history["candidate_hits"],
        "operational_score_all_hits": history["operational_score_all_hits"],
        "operational_score_all_hit_count": history["operational_score_all_hit_count"],
        "historical_hash_count": len(external_historical),
        "candidate_hit_count": history["candidate_hit_count"],
        "already_materialized_hashes": sorted(already_materialized),
        "already_materialized_count": len(already_materialized),
        "pg_new_hashes": pre_materialization_new,
        "pg_new_count": len(pre_materialization_new),
        "rejected_occurrence_count": len(
            external_historical
            & {row["sequence_sha256"] for row in strict_scores}
        ),
        "materialization_input_count": len(input_scores),
        "materialization_status": status,
        "reuse_existing_materialization": bool(already_materialized),
        "materialization_allowed": history["alembic_version"]
        == "0020_candidate_sequence_sha256_index",
        "remote_compute_used": False,
    }
    (report_dir / "pg_exact_history_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (output_dir / "strict_selection_receipt.json").write_text(
        json.dumps(
            {
                "schema_version": "ampgent.gyra-pepflow-qd-neighbor.strict-selection.1",
                "proposal_count": receipt["proposal_count"],
                "strict_intersection_count": receipt["strict_intersection_count"],
                "pg_new_count": receipt["pg_new_count"],
                "rejected_occurrence_count": receipt["rejected_occurrence_count"],
                "strict_intersection_hashes": receipt["strict_intersection_hashes"],
                "pg_new_hashes": receipt["pg_new_hashes"],
                "candidate_scores_sha256": hashlib.sha256(
                    (output_dir / "candidate_scores.csv").read_bytes()
                ).hexdigest()
                if new_scores
                else None,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(args.report_dir, args.output_dir),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
