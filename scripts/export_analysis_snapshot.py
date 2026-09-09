#!/usr/bin/env python3
"""Export a deterministic, read-only AMPgent analysis snapshot from PostgreSQL.

The exporter deliberately contains no product/UI logic. It preserves the smallest
row-level facts needed by the browser-side query engine: proposal occurrences,
unique candidates, metric evidence, deterministic admission decisions, and tool
method identities. Database credentials are accepted only through an argument or
PEPAGENT_DATABASE_URL_SYNC; they are never written to the snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row


SCHEMA_VERSION = "ampgent-analysis-snapshot.1"
DIGEST_PLACEHOLDER = "0" * 64
HISTORICAL_EXACT_REPLAY = "historical_exact_replay"
HISTORICAL_PERSISTENCE_REASON = "sequence_already_materialized_in_historical_run"


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _normalise_database_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _fetch_all(cursor: psycopg.Cursor[Any], sql: str, run_id: str) -> list[dict[str, Any]]:
    cursor.execute(sql, {"run_id": run_id})
    return [dict(row) for row in cursor.fetchall()]


def _transport_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _display_candidate_rows(
    candidate_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition candidates using the read-only SQL replay classification."""

    visible = [row for row in candidate_rows if row["display_eligible"]]
    excluded = [row for row in candidate_rows if not row["display_eligible"]]
    return visible, excluded


def _occurrence_is_historical_exact_replay(
    row: dict[str, Any],
    visible_candidate_ids: set[str],
) -> bool:
    metadata = row.get("metadata_json") or {}
    candidate_id = row.get("candidate_id")
    return (
        (candidate_id is not None and candidate_id not in visible_candidate_ids)
        or metadata.get("reason") == HISTORICAL_PERSISTENCE_REASON
    )


def export_snapshot(database_url: str, run_id: str, generated_at: str) -> dict[str, Any]:
    with psycopg.connect(_normalise_database_url(database_url), row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        with connection.cursor() as cursor:
            run_rows = _fetch_all(
                cursor,
                """
                SELECT id::text, status, spec_sha256, spec_json, started_at, finished_at,
                       created_at, updated_at
                FROM experiment_runs WHERE id = %(run_id)s::uuid
                """,
                run_id,
            )
            if not run_rows:
                raise SystemExit(f"run not found: {run_id}")
            run = run_rows[0]

            tool_rows = _fetch_all(
                cursor,
                """
                SELECT id::text, tool_name, tool_version, model_uri, weights_sha256,
                       environment_sha256, random_seed, status, input_json, parameters_json,
                       output_sha256
                FROM tool_calls WHERE run_id = %(run_id)s::uuid
                ORDER BY tool_name, random_seed NULLS FIRST, id
                """,
                run_id,
            )
            occurrence_rows = _fetch_all(
                cursor,
                """
                SELECT o.id::text, o.candidate_id::text, o.sequence_sha256,
                       o.occurrence_rank, o.occurrence_kind, o.opaque_arm_label,
                       o.metadata_json, t.id::text AS tool_call_id,
                       t.input_json->>'generator_id' AS generator
                FROM candidate_occurrences o
                JOIN tool_calls t ON t.id = o.tool_call_id
                WHERE o.run_id = %(run_id)s::uuid
                ORDER BY o.created_at, o.id
                """,
                run_id,
            )
            candidate_rows = _fetch_all(
                cursor,
                """
                SELECT c.id::text, c.sequence, c.sequence_sha256, c.generation,
                       c.parent_id::text, c.status, c.proposal_rank,
                       c.generator_call_id::text, c.metadata_json, c.created_at,
                       NOT (
                           c.generation > 0
                           AND EXISTS (
                               SELECT 1
                               FROM candidates prior
                               WHERE prior.run_id <> c.run_id
                                 AND prior.sequence_sha256 = c.sequence_sha256
                                 AND (
                                     prior.created_at < c.created_at
                                     OR (prior.created_at = c.created_at AND prior.id < c.id)
                                 )
                           )
                       ) AS display_eligible
                FROM candidates c
                WHERE c.run_id = %(run_id)s::uuid
                ORDER BY c.sequence_sha256, c.id
                """,
                run_id,
            )
            evaluation_rows = _fetch_all(
                cursor,
                """
                SELECT e.candidate_id::text, e.metric_name, e.numeric_value, e.text_value,
                       e.unit, e.status, e.out_of_domain, e.limitations_json,
                       e.tool_call_id::text
                FROM evaluations e
                JOIN candidates c ON c.id = e.candidate_id
                WHERE c.run_id = %(run_id)s::uuid
                ORDER BY e.candidate_id, e.metric_name, e.tool_call_id
                """,
                run_id,
            )
            decision_rows = _fetch_all(
                cursor,
                """
                SELECT decision_type, agent_name, agent_version, status, response_sha256,
                       structured_json, created_at
                FROM agent_decisions WHERE run_id = %(run_id)s::uuid
                ORDER BY created_at, id
                """,
                run_id,
            )
            checkpoint_rows = _fetch_all(
                cursor,
                """
                SELECT stage_name, stage_order, durable_count, expected_durable_count,
                       stage_status, controller_action, reasons_json, receipt_sha256, observed_at
                FROM run_stage_checkpoints WHERE run_id = %(run_id)s::uuid
                ORDER BY stage_order, observation_no
                """,
                run_id,
            )

    candidate_record_count = len(candidate_rows)
    candidate_rows, excluded_candidate_rows = _display_candidate_rows(candidate_rows)
    visible_candidate_ids = {row["id"] for row in candidate_rows}
    occurrence_record_count = len(occurrence_rows)
    occurrence_rows = [
        row
        for row in occurrence_rows
        if not _occurrence_is_historical_exact_replay(row, visible_candidate_ids)
    ]
    evaluation_rows = [
        row for row in evaluation_rows if row["candidate_id"] in visible_candidate_ids
    ]

    origins_by_candidate: dict[str, set[str]] = defaultdict(set)
    compact_occurrences: list[dict[str, Any]] = []
    for row in occurrence_rows:
        generator = row.get("generator") or row["opaque_arm_label"]
        candidate_id = row.get("candidate_id")
        if candidate_id:
            origins_by_candidate[candidate_id].add(generator)
        metadata = row.get("metadata_json") or {}
        compact_occurrences.append(
            {
                "id": row["id"],
                "candidateId": candidate_id,
                "sequenceSha256": row["sequence_sha256"],
                "generator": generator,
                "generatorCell": row["opaque_arm_label"],
                "rank": row["occurrence_rank"],
                "kind": row["occurrence_kind"],
                "disposition": metadata.get("disposition", "unknown"),
                "displayEligible": True,
                "exclusionReason": None,
            }
        )

    decisions_by_candidate: dict[str, dict[str, Any]] = {}
    admission_policy: dict[str, Any] | None = None
    decision_methods: list[dict[str, Any]] = []
    for row in decision_rows:
        structured = row.get("structured_json") or {}
        admission = structured.get("admission") or {}
        admission_policy = structured.get("policy") or admission_policy
        for decision in admission.get("decisions", []):
            candidate_id = decision.get("candidate_id")
            if candidate_id:
                decisions_by_candidate[candidate_id] = decision
        decision_methods.append(
            {
                "decisionType": row["decision_type"],
                "agentName": row["agent_name"],
                "agentVersion": row["agent_version"],
                "status": row["status"],
                "responseSha256": row["response_sha256"],
            }
        )

    evaluations_by_candidate: dict[str, dict[str, Any]] = defaultdict(dict)
    metric_tool_ids: dict[str, set[str]] = defaultdict(set)
    for row in evaluation_rows:
        metric_tool_ids[row["metric_name"]].add(row["tool_call_id"])
        evaluations_by_candidate[row["candidate_id"]][row["metric_name"]] = {
            "value": row["numeric_value"],
            "text": row["text_value"],
            "unit": row["unit"],
            "status": row["status"],
            "outOfDomain": row["out_of_domain"],
            "limitations": row["limitations_json"] or [],
            "toolCallId": row["tool_call_id"],
        }

    compact_candidates: list[dict[str, Any]] = []
    for row in candidate_rows:
        candidate_id = row["id"]
        metadata = row.get("metadata_json") or {}
        decision = decisions_by_candidate.get(candidate_id, {})
        origins = sorted(origins_by_candidate.get(candidate_id) or {metadata.get("generator_id", "unknown")})
        compact_candidates.append(
            {
                "id": candidate_id,
                "sequence": row["sequence"],
                "sequenceSha256": row["sequence_sha256"],
                "generation": row["generation"],
                "parentId": row["parent_id"],
                "status": row["status"],
                "proposalRank": row["proposal_rank"],
                "originSet": origins,
                "cohortSha256": metadata.get("cohort_sha256"),
                "displayEligible": True,
                "exclusionReason": None,
                "admission": {
                    "status": decision.get("status", "not_evaluated"),
                    "reasons": decision.get("reasons", []),
                    "paretoFront": decision.get("pareto_front"),
                    "structureEligible": decision.get("structure_eligible", False),
                },
                "metrics": evaluations_by_candidate.get(candidate_id, {}),
            }
        )

    candidate_exclusions = [
        {
            "id": row["id"],
            "sequenceSha256": row["sequence_sha256"],
            "generation": row["generation"],
            "displayEligible": False,
            "exclusionReason": HISTORICAL_EXACT_REPLAY,
        }
        for row in excluded_candidate_rows
    ]

    tools_by_id = {row["id"]: row for row in tool_rows}
    metric_methods: dict[str, list[dict[str, Any]]] = {}
    for metric, tool_ids in sorted(metric_tool_ids.items()):
        metric_methods[metric] = [
            {
                "toolCallId": tool_id,
                "toolName": tools_by_id[tool_id]["tool_name"],
                "toolVersion": tools_by_id[tool_id]["tool_version"],
                "modelUri": tools_by_id[tool_id]["model_uri"],
                "weightsSha256": tools_by_id[tool_id]["weights_sha256"],
                "environmentSha256": tools_by_id[tool_id]["environment_sha256"],
                "outputSha256": tools_by_id[tool_id]["output_sha256"],
            }
            for tool_id in sorted(tool_ids)
        ]

    generator_cells = []
    for row in tool_rows:
        generator = (row.get("input_json") or {}).get("generator_id")
        if not generator:
            continue
        generator_cells.append(
            {
                "toolCallId": row["id"],
                "generator": generator,
                "seed": row["random_seed"],
                "status": row["status"],
                "rawProposalBudget": (row.get("parameters_json") or {}).get("raw_proposal_budget"),
                "toolName": row["tool_name"],
                "toolVersion": row["tool_version"],
                "modelUri": row["model_uri"],
                "weightsSha256": row["weights_sha256"],
                "environmentSha256": row["environment_sha256"],
                "outputSha256": row["output_sha256"],
            }
        )

    admission_counts = Counter(
        candidate["admission"]["status"] for candidate in compact_candidates
    )
    disposition_counts = Counter(item["disposition"] for item in compact_occurrences)
    metric_names = sorted(metric_methods)
    expected_evaluations = len(compact_candidates) * len(metric_names)
    observed_evaluations = len(evaluation_rows)
    out_of_domain_count = sum(bool(row["out_of_domain"]) for row in evaluation_rows)

    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "snapshotId": f"run:{run_id}:release-2026-08-28",
        "snapshotSha256": DIGEST_PLACEHOLDER,
        "generatedAt": generated_at,
        "source": "frozen_release_snapshot",
        "run": {
            "id": run["id"],
            "status": run["status"],
            "specSha256": run["spec_sha256"],
            "name": (run.get("spec_json") or {}).get("name", "Sequence-first · multitarget"),
            "startedAt": run["started_at"],
            "finishedAt": run["finished_at"],
        },
        "cohorts": sorted(
            {candidate["cohortSha256"] for candidate in compact_candidates if candidate["cohortSha256"]}
        ),
        "generatorCells": generator_cells,
        "occurrences": compact_occurrences,
        "candidates": compact_candidates,
        "candidateExclusions": candidate_exclusions,
        "displayPopulation": {
            "candidateCount": len(compact_candidates),
            "candidateRecordCount": candidate_record_count,
            "excludedCandidateCount": len(candidate_exclusions),
            "occurrenceCount": len(compact_occurrences),
            "occurrenceRecordCount": occurrence_record_count,
            "excludedOccurrenceCount": occurrence_record_count - len(compact_occurrences),
            "exclusionReason": HISTORICAL_EXACT_REPLAY,
        },
        "metricMethods": metric_methods,
        "admissionPolicy": admission_policy,
        "decisionMethods": decision_methods,
        "stageCheckpoints": checkpoint_rows,
        "summary": {
            "rawOccurrences": len(compact_occurrences),
            "uniqueCandidates": len(compact_candidates),
            "promotedOccurrences": disposition_counts.get("promoted_for_scoring", 0),
            "invalidOccurrences": len(compact_occurrences)
            - disposition_counts.get("promoted_for_scoring", 0),
            "observedEvaluations": observed_evaluations,
            "expectedEvaluations": expected_evaluations,
            "outOfDomainEvaluations": out_of_domain_count,
            "admissionCounts": dict(sorted(admission_counts.items())),
            "structureEligible": sum(
                bool(candidate["admission"]["structureEligible"])
                for candidate in compact_candidates
            ),
        },
        "coverage": {
            "observed": observed_evaluations,
            "expected": expected_evaluations,
            "missing": expected_evaluations - observed_evaluations,
            "outOfDomain": out_of_domain_count,
        },
        "warnings": [
            "Source run status is cancelled; sequence generation, scoring, and admission completed before downstream cancellation.",
            "Structure and final portfolio stages are incomplete and must not be inferred from this snapshot.",
            "This is a frozen release snapshot, not a live analytics response.",
        ],
    }
    payload["snapshotSha256"] = hashlib.sha256(_transport_json(payload).encode("utf-8")).hexdigest()
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--database-url", default=os.getenv("PEPAGENT_DATABASE_URL_SYNC"))
    parser.add_argument(
        "--generated-at",
        default=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("provide --database-url or PEPAGENT_DATABASE_URL_SYNC")

    snapshot = export_snapshot(args.database_url, args.run_id, args.generated_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_transport_json(snapshot), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "snapshotSha256": snapshot["snapshotSha256"],
                "summary": snapshot["summary"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
