"""Audit and explicitly bind AMPgent scientific run aggregation identities.

Audit/dry-run is the default.  ``--apply`` only writes the new aggregation
tables/columns and never changes scientific Candidate/Evaluation results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

CONTRACT_VERSION = "ampgent.run-aggregation-contract.1"
MIGRATION_VERSION = "0022_run_aggregation"
ADVISORY_LOCK_KEY = 4_723_661_022
PHASE_ORDER = {
    "generation": 10,
    "score_all": 20,
    "challenger": 30,
    "qd_lineage": 40,
    "boltz": 50,
    "rosetta": 60,
    "md": 70,
    "pool_s": 80,
    "unclassified": 900,
}
UUID_NAMESPACE = uuid.UUID("a8552f6f-3fab-5cd9-ac15-08e7258a33d7")


def _sha(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _uuid5(kind: str, identity: str) -> uuid.UUID:
    return uuid.uuid5(UUID_NAMESPACE, f"{CONTRACT_VERSION}:{kind}:{identity}")


def _fetch_one(connection: Connection[Any], query: str) -> dict[str, Any]:
    row = connection.execute(query).fetchone()
    return dict(row) if row else {}


def audit(connection: Connection[Any]) -> dict[str, Any]:
    """Return bounded aggregate diagnostics from a read-only transaction."""

    counts = _fetch_one(
        connection,
        """
        SELECT (SELECT count(*) FROM experiment_runs) experiment_runs,
          (SELECT count(*) FROM candidates) candidates,
          (SELECT count(*) FROM evaluations) evaluations,
          (SELECT count(*) FROM tool_calls) tool_calls,
          (SELECT count(*) FROM artifacts) artifacts,
          (SELECT count(*) FROM multitarget_structure_evidence_records) structure_records,
          (SELECT count(*) FROM lifecycle_events) lifecycle_events
        """,
    )
    anomalies = {}
    anomaly_queries = {
        "candidate_parent_cross_run": """
          SELECT count(*) n FROM candidates c JOIN candidates p ON p.id=c.parent_id
          WHERE p.run_id<>c.run_id
        """,
        "evaluation_producer_cross_run": """
          SELECT count(*) n FROM evaluations e JOIN candidates c ON c.id=e.candidate_id
          JOIN tool_calls tc ON tc.id=e.tool_call_id WHERE tc.run_id<>c.run_id
        """,
        "evaluation_subject_run_null": (
            "SELECT count(*) n FROM evaluations WHERE subject_run_id IS NULL"
        ),
        "evaluation_subject_run_mismatch": """
          SELECT count(*) n FROM evaluations e JOIN candidates c ON c.id=e.candidate_id
          WHERE e.subject_run_id IS NOT NULL AND e.subject_run_id<>c.run_id
        """,
        "failed_cancelled_candidate_rows": """
          SELECT count(*) n FROM candidates c JOIN experiment_runs r ON r.id=c.run_id
          WHERE r.status IN ('failed','cancelled')
        """,
        "failed_cancelled_evaluation_rows": """
          SELECT count(*) n FROM evaluations e JOIN candidates c ON c.id=e.candidate_id
          JOIN experiment_runs r ON r.id=c.run_id WHERE r.status IN ('failed','cancelled')
        """,
        "structure_candidate_run_mismatch": """
          SELECT count(*) n FROM multitarget_structure_evidence_records s
          JOIN candidates c ON c.id=s.candidate_id WHERE s.run_id<>c.run_id
        """,
        "tool_calls_without_artifact_edge": """
          SELECT count(*) n FROM tool_calls tc LEFT JOIN evidence_artifacts ea
          ON ea.tool_call_id=tc.id WHERE ea.tool_call_id IS NULL
        """,
        "artifacts_without_reference": """
          SELECT count(*) n FROM artifacts a LEFT JOIN evidence_artifacts ea
          ON ea.artifact_id=a.id LEFT JOIN model_release_artifacts ma ON ma.artifact_id=a.id
          WHERE ea.artifact_id IS NULL AND ma.artifact_id IS NULL
        """,
        "autoresearch_lineage_cross_run": """
          SELECT count(*) n FROM candidate_lineage_edges e
          JOIN candidates c ON c.id=e.child_candidate_id
          JOIN candidates p ON p.id=e.parent_candidate_id
          WHERE c.run_id<>p.run_id
        """,
        "metric_delta_cross_run": """
          SELECT count(*) n FROM autoresearch_metric_deltas d
          JOIN candidates c ON c.id=d.child_candidate_id
          JOIN candidates p ON p.id=d.comparator_candidate_id
          WHERE c.run_id<>p.run_id
        """,
    }
    for key, query in anomaly_queries.items():
        anomalies[key] = int(_fetch_one(connection, query)["n"])

    parent = _fetch_one(
        connection,
        """
        WITH RECURSIVE walk AS (
          SELECT id,parent_run_id,id root_run_id,0 depth,ARRAY[id] path
          FROM experiment_runs WHERE parent_run_id IS NULL
          UNION ALL
          SELECT r.id,r.parent_run_id,w.root_run_id,w.depth+1,w.path||r.id
          FROM walk w JOIN experiment_runs r ON r.parent_run_id=w.id
          WHERE NOT r.id=ANY(w.path)
        )
        SELECT count(*) FILTER (WHERE depth=0) explicit_roots,
          count(*) FILTER (WHERE depth>0) explicit_children,
          max(depth) max_depth,
          (SELECT count(*) FROM experiment_runs)-count(*) unreachable_or_cyclic
        FROM walk
        """,
    )
    invalidations = connection.execute(
        """
        SELECT event_type,count(*) n FROM lifecycle_events
        WHERE event_type ILIKE '%invalid%' OR event_type ILIKE '%supersed%'
        GROUP BY event_type ORDER BY n DESC,event_type
        """
    ).fetchall()
    active = connection.execute(
        """
        SELECT state,coalesce(wait_event_type,'none') wait_type,count(*) n
        FROM pg_stat_activity WHERE datname=current_database()
        GROUP BY state,wait_type ORDER BY n DESC
        """
    ).fetchall()
    version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    result = {
        "schema_version": "ampgent.run-aggregation-audit.1",
        "contract_version": CONTRACT_VERSION,
        "alembic_version": version,
        "counts": counts,
        "explicit_parent_forest": parent,
        "anomalies": anomalies,
        "historical_invalidation_event_types": [dict(row) for row in invalidations],
        "database_activity": [dict(row) for row in active],
        "interpretation": {
            "failed_or_cancelled_is_not_invalidation": True,
            "cross_run_producer_is_not_automatically_an_error": True,
            "sequence_duplicates_are_not_merge_keys": True,
        },
    }
    if connection.execute("SELECT to_regclass('run_lineage_edges')").fetchone()[0]:
        result["aggregation_counts"] = _fetch_one(
            connection,
            """
            SELECT (SELECT count(*) FROM scientific_roots) scientific_roots,
              (SELECT count(*) FROM scientific_run_groups) run_groups,
              (SELECT count(*) FROM experiment_runs WHERE run_group_id IS NOT NULL) grouped_runs,
              (SELECT count(*) FROM run_lineage_edges) run_lineage_edges,
              (SELECT count(*) FROM run_evidence_attachments) evidence_attachments,
              (SELECT count(*) FROM run_invalidation_events) run_invalidation_events,
              (SELECT count(*) FROM evidence_invalidation_events) evidence_invalidation_events
            """,
        )
    return result


def _load_manifest(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "run_groups": [],
            "evidence_attachments": [],
            "run_invalidations": [],
            "evidence_invalidations": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "ampgent.run-aggregation-manifest.1":
        raise ValueError("unsupported run aggregation manifest schema_version")
    return payload


def plan_parent_edges(connection: Connection[Any]) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT parent_run_id::text parent_run_id,id::text child_run_id
        FROM experiment_runs WHERE parent_run_id IS NOT NULL
        ORDER BY parent_run_id,id
        """
    ).fetchall()
    planned = []
    for row in rows:
        identity = {
            "parent_run_id": row["parent_run_id"],
            "child_run_id": row["child_run_id"],
            "relation_type": "parent_run",
            "relation_ordinal": 1,
            "binding_basis": "experiment_runs.parent_run_id",
        }
        planned.append(
            {
                **identity,
                "id": str(_uuid5("run-edge", _sha(identity))),
                "edge_sha256": _sha(identity),
            }
        )
    return planned


def _validate_manifest(connection: Connection[Any], manifest: dict[str, Any]) -> None:
    run_ids: set[uuid.UUID] = set()
    candidate_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
    tool_pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
    for group in manifest.get("run_groups", []):
        root_run_id = uuid.UUID(group["root_run_id"])
        run_ids.add(root_run_id)
        member_ids = {uuid.UUID(member["run_id"]) for member in group["members"]}
        if root_run_id not in member_ids:
            raise ValueError("run group root_run_id must be an explicit member")
        for member in group["members"]:
            run_ids.add(uuid.UUID(member["run_id"]))
            phase = member.get("phase_code")
            ordinal = member.get("phase_ordinal")
            if (phase is None) != (ordinal is None):
                raise ValueError(f"phase code/ordinal must be paired for run {member['run_id']}")
            if phase is not None and PHASE_ORDER.get(phase) != ordinal:
                raise ValueError(f"invalid phase pair for run {member['run_id']}")
    for item in manifest.get("evidence_attachments", []):
        phase = item["phase_code"]
        if phase not in PHASE_ORDER or int(item["phase_order"]) != PHASE_ORDER[phase]:
            raise ValueError("attachment phase order differs from frozen contract")
        if int(item["evidence_ordinal"]) < 1:
            raise ValueError("attachment evidence_ordinal must be positive")
        subject = uuid.UUID(item["subject_run_id"])
        producer = uuid.UUID(item["producer_run_id"])
        run_ids.update((subject, producer))
        if item.get("subject_candidate_id"):
            candidate_pairs.append((uuid.UUID(item["subject_candidate_id"]), subject))
        tool_pairs.append((uuid.UUID(item["tool_call_id"]), producer))
    for item in manifest.get("run_invalidations", []):
        run_ids.add(uuid.UUID(item["run_id"]))
        if item["event_type"] not in {"invalidate", "reinstate"}:
            raise ValueError("invalid run invalidation event_type")
        if int(item["event_ordinal"]) < 1:
            raise ValueError("run invalidation event_ordinal must be positive")
    for item in manifest.get("evidence_invalidations", []):
        subject = uuid.UUID(item["subject_run_id"])
        run_ids.add(subject)
        if item["event_type"] not in {"invalidate", "reinstate"}:
            raise ValueError("invalid evidence invalidation event_type")
        if int(item["event_ordinal"]) < 1:
            raise ValueError("evidence invalidation event_ordinal must be positive")
        if item.get("subject_candidate_id"):
            candidate_pairs.append((uuid.UUID(item["subject_candidate_id"]), subject))
    if run_ids:
        found = connection.execute(
            "SELECT id FROM experiment_runs WHERE id=ANY(%s)", (list(run_ids),)
        ).fetchall()
        found_ids = {row["id"] for row in found}
        missing = sorted(str(value) for value in run_ids - found_ids)
        if missing:
            raise ValueError(f"manifest references missing runs: {missing}")
    for candidate_id, run_id in candidate_pairs:
        row = connection.execute(
            "SELECT run_id FROM candidates WHERE id=%s", (candidate_id,)
        ).fetchone()
        if row is None or row["run_id"] != run_id:
            raise ValueError(f"candidate/run mismatch: {candidate_id}")
    for tool_call_id, run_id in tool_pairs:
        row = connection.execute(
            "SELECT run_id FROM tool_calls WHERE id=%s", (tool_call_id,)
        ).fetchone()
        if row is None or row["run_id"] != run_id:
            raise ValueError(f"tool-call/producer mismatch: {tool_call_id}")


def apply(connection: Connection[Any], manifest: dict[str, Any]) -> dict[str, int]:
    version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    if version != MIGRATION_VERSION:
        raise RuntimeError(f"expected Alembic {MIGRATION_VERSION}, observed {version}")
    connection.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
    connection.execute("SET LOCAL lock_timeout='2s'")
    connection.execute("SET LOCAL statement_timeout='60s'")
    connection.execute("SET LOCAL idle_in_transaction_session_timeout='15s'")
    _validate_manifest(connection, manifest)
    counts = {
        "lineage_edges": 0,
        "scientific_roots": 0,
        "run_groups": 0,
        "members": 0,
        "attachments": 0,
        "run_invalidations": 0,
        "evidence_invalidations": 0,
    }

    for edge in plan_parent_edges(connection):
        result = connection.execute(
            """
            INSERT INTO run_lineage_edges
              (id,parent_run_id,child_run_id,relation_type,relation_ordinal,binding_basis,
               edge_sha256,metadata_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb)
            ON CONFLICT (edge_sha256) DO NOTHING
            """,
            (
                edge["id"],
                edge["parent_run_id"],
                edge["child_run_id"],
                edge["relation_type"],
                edge["relation_ordinal"],
                edge["binding_basis"],
                edge["edge_sha256"],
            ),
        )
        counts["lineage_edges"] += result.rowcount

    for group in manifest.get("run_groups", []):
        root_run_id = uuid.UUID(group["root_run_id"])
        root_key = group["scientific_root_key"]
        root_id = _uuid5("scientific-root", root_key)
        result = connection.execute(
            """INSERT INTO scientific_roots
            (id,root_key,root_run_id,contract_version,metadata_json)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT (root_key) DO NOTHING""",
            (
                root_id,
                root_key,
                root_run_id,
                CONTRACT_VERSION,
                Jsonb(group.get("metadata", {})),
            ),
        )
        counts["scientific_roots"] += result.rowcount
        group_key = group["group_key"]
        group_id = _uuid5("run-group", group_key)
        result = connection.execute(
            """INSERT INTO scientific_run_groups
            (id,group_key,scientific_root_id,root_run_id,contract_version,metadata_json)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (group_key) DO NOTHING""",
            (
                group_id,
                group_key,
                root_id,
                root_run_id,
                CONTRACT_VERSION,
                Jsonb(group.get("metadata", {})),
            ),
        )
        counts["run_groups"] += result.rowcount
        for member in group["members"]:
            result = connection.execute(
                """
                UPDATE experiment_runs SET run_group_id=%s,root_run_id=%s,phase_code=%s,
                  phase_ordinal=%s,aggregation_basis='explicit_manifest'
                WHERE id=%s AND (run_group_id IS NULL OR run_group_id=%s)
                """,
                (
                    group_id,
                    root_run_id,
                    member.get("phase_code"),
                    member.get("phase_ordinal"),
                    member["run_id"],
                    group_id,
                ),
            )
            if result.rowcount != 1:
                raise ValueError(f"run already belongs to another group: {member['run_id']}")
            counts["members"] += 1

    for item in manifest.get("evidence_attachments", []):
        identity = {
            key: item.get(key)
            for key in (
                "subject_run_id",
                "subject_candidate_id",
                "producer_run_id",
                "tool_call_id",
                "phase_code",
                "phase_order",
                "evidence_ordinal",
                "evidence_role",
                "binding_basis",
            )
        }
        digest = _sha(identity)
        result = connection.execute(
            """
            INSERT INTO run_evidence_attachments
              (id,subject_run_id,subject_candidate_id,producer_run_id,tool_call_id,
               phase_code,phase_order,evidence_ordinal,evidence_role,binding_basis,
               attachment_sha256,metadata_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (attachment_sha256) DO NOTHING
            """,
            (
                _uuid5("evidence-attachment", digest),
                item["subject_run_id"],
                item.get("subject_candidate_id"),
                item["producer_run_id"],
                item["tool_call_id"],
                item["phase_code"],
                item["phase_order"],
                item["evidence_ordinal"],
                item["evidence_role"],
                item["binding_basis"],
                digest,
                Jsonb(item.get("metadata", {})),
            ),
        )
        counts["attachments"] += result.rowcount
    for item in manifest.get("run_invalidations", []):
        identity = {
            key: item.get(key)
            for key in ("run_id", "event_ordinal", "event_type", "reason", "decision_id")
        }
        digest = _sha(identity)
        result = connection.execute(
            """
            INSERT INTO run_invalidation_events
              (id,run_id,event_ordinal,event_type,reason,decision_id,event_sha256,metadata_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (event_sha256) DO NOTHING
            """,
            (
                _uuid5("run-invalidation", digest),
                item["run_id"],
                item["event_ordinal"],
                item["event_type"],
                item["reason"],
                item.get("decision_id"),
                digest,
                Jsonb(item.get("metadata", {})),
            ),
        )
        counts["run_invalidations"] += result.rowcount
    for item in manifest.get("evidence_invalidations", []):
        identity = {
            key: item.get(key)
            for key in (
                "subject_run_id",
                "subject_candidate_id",
                "record_kind",
                "record_id",
                "event_ordinal",
                "event_type",
                "reason",
                "decision_id",
            )
        }
        digest = _sha(identity)
        result = connection.execute(
            """
            INSERT INTO evidence_invalidation_events
              (id,subject_run_id,subject_candidate_id,record_kind,record_id,event_ordinal,
               event_type,reason,decision_id,event_sha256,metadata_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (event_sha256) DO NOTHING
            """,
            (
                _uuid5("evidence-invalidation", digest),
                item["subject_run_id"],
                item.get("subject_candidate_id"),
                item["record_kind"],
                item["record_id"],
                item["event_ordinal"],
                item["event_type"],
                item["reason"],
                item.get("decision_id"),
                digest,
                Jsonb(item.get("metadata", {})),
            ),
        )
        counts["evidence_invalidations"] += result.rowcount
    return counts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("PEPAGENT_DATABASE_URL_PLAIN"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--backup-receipt", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.database_url:
        raise SystemExit("set PEPAGENT_DATABASE_URL_PLAIN or pass --database-url")
    manifest = _load_manifest(args.manifest)
    if args.apply:
        if args.backup_receipt is None:
            raise SystemExit("--apply requires --backup-receipt")
        backup = json.loads(args.backup_receipt.read_text(encoding="utf-8"))
        if not (
            backup.get("restore", {}).get("restore_verified") is True
            or backup.get("backup_verified") is True
        ):
            raise SystemExit("backup receipt does not prove a verified backup/restore")
    with psycopg.connect(args.database_url, row_factory=dict_row, connect_timeout=10) as connection:
        if args.apply:
            with connection.transaction():
                applied = apply(connection, manifest)
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL statement_timeout='60s'")
                connection.execute("SET LOCAL idle_in_transaction_session_timeout='15s'")
                result = {"mode": "apply", "applied": applied, "audit": audit(connection)}
        else:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL statement_timeout='60s'")
                connection.execute("SET LOCAL idle_in_transaction_session_timeout='15s'")
                result = {
                    "mode": "dry_run",
                    "planned_parent_lineage_edges": len(plan_parent_edges(connection)),
                    "manifest_run_groups": len(manifest.get("run_groups", [])),
                    "manifest_evidence_attachments": len(manifest.get("evidence_attachments", [])),
                    "audit": audit(connection),
                }
    rendered = json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
