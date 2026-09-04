"""Audit ANGPT1 QD contributors against exact PostgreSQL history."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, select, text

from pepagent.db.models import Candidate
from pepagent.db.session import SessionFactory


def _qd_hashes(report_dir: Path) -> list[str]:
    with (report_dir / "qd" / "qd_candidates.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    hashes = [
        str(row["sequence_sha256"]).strip().lower()
        for row in rows
        if row.get("contribution") in {"empty_cell", "incumbent_replacement"}
    ]
    if len(hashes) != 2 or len(set(hashes)) != 2:
        raise ValueError("expected exactly two unique ANGPT1 QD contributor hashes")
    return hashes


async def audit(report_dir: Path) -> dict[str, Any]:
    hashes = _qd_hashes(report_dir)
    async with SessionFactory() as session:
        await session.execute(text("SET statement_timeout = '60000ms'"))
        await session.execute(
            text("SET application_name = 'ampgent-angpt1-v2b-exact-history'")
        )
        candidate_rows = list(
            await session.execute(
                select(Candidate.id, Candidate.run_id, Candidate.sequence_sha256)
                .where(Candidate.sequence_sha256.in_(hashes))
                .order_by(Candidate.sequence_sha256, Candidate.created_at, Candidate.id)
            )
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
        event_rows = list(await session.execute(event_query, {"hashes": hashes}))
        index_row = (
            await session.execute(
                text(
                    """
                    SELECT i.indisvalid, i.indisready, i.indislive, x.indexdef
                    FROM pg_class c JOIN pg_index i ON i.indexrelid=c.oid
                    JOIN pg_indexes x ON x.indexname=c.relname
                    WHERE c.relname='ix_candidate_sequence_sha256'
                    """
                )
            )
        ).one_or_none()
        version = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()

    candidate_hits = [
        {
            "candidate_id": str(row.id),
            "run_id": str(row.run_id),
            "sequence_sha256": row.sequence_sha256,
        }
        for row in candidate_rows
    ]
    event_hits = [
        {
            "event_id": row.event_id,
            "aggregate_id": row.aggregate_id,
            "sequence_sha256": row.sequence_sha256,
        }
        for row in event_rows
    ]
    historical_hashes = {
        item["sequence_sha256"] for item in candidate_hits + event_hits
    }
    index_flags = None
    if index_row is not None:
        index_flags = {
            "valid": bool(index_row.indisvalid),
            "ready": bool(index_row.indisready),
            "live": bool(index_row.indislive),
            "definition_present": bool(index_row.indexdef),
        }
    return {
        "schema_version": "ampgent.angpt1-pepmlm-qd-neighbor-v2b.pg-exact-history.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "target_key": "ANGPT1",
        "alembic_version": version,
        "index_name": "ix_candidate_sequence_sha256",
        "index_flags": index_flags,
        "history_query_mode": "exact_hash_scan_fallback"
        if index_flags is not None and not all(index_flags.values())
        else "exact_hash_indexed",
        "candidate_hashes": hashes,
        "candidate_hit_count": len(candidate_hits),
        "candidate_hits": candidate_hits,
        "operational_score_all_hit_count": len(event_hits),
        "operational_score_all_hits": event_hits,
        "historical_hash_count": len(historical_hashes),
        "pg_new_hashes": sorted(set(hashes) - historical_hashes),
        "rejected_occurrence_count": len(set(hashes) & historical_hashes),
        "materialization_allowed": version == "0020_candidate_sequence_sha256_index",
        "remote_compute_used": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = asyncio.run(audit(args.report_dir))
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
