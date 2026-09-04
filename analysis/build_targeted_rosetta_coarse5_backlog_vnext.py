"""Build a compact, read-only, target-specific coarse5 backlog inventory.

This adapter consumes the existing merged inventory and the three recently
closed, receipt-backed queues.  It deliberately queries PostgreSQL by the
authoritative ``(run_id, candidate_id)`` pair; sequence-wide history scans are
not part of this backlog operation.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.build_targeted_rosetta_coarse5_backlog import (
    TARGETS,
    integer,
    number,
    read_csv,
    read_json,
    task_key,
    truth,
)

CONTRIBUTIONS = {"empty_cell", "incumbent_replacement", "replacement"}
# A retained model disagreement is complete challenger evidence; it is not a
# primary hard-gate pass, but it must not erase an authoritative prepared task.
CONFLICT_OK = {"no_conflict"}
RETAINED_CONFLICT = "cross_model_disagreement_retained"
CSV_FIELDS = (
    "priority",
    "target_key",
    "target",
    "pool_a_count",
    "balance_gap_to_50",
    "run_id",
    "candidate_id",
    "sequence_sha256",
    "sequence",
    "source",
    "source_report",
    "source_generation",
    "qd_contribution",
    "qd_cell",
    "qd_new_cell",
    "qd_replacement",
    "qd_quality",
    "archive_qd_score",
    "activity_support",
    "formal12",
    "display",
    "challenger_status",
    "quality_eligible",
    "pg_succeeded_evaluations",
    "rosetta_task_key",
    "task_aliases",
    "nstruct",
    "existing_decoy_count",
    "remaining_decoy_count",
    "status",
    "structure_evidence_count",
    "evidence_sources",
    "requires_remote_exact_preflight",
)

ROOT = Path(__file__).resolve().parents[1]
OLD_CSV = Path(
    "reports/targeted_rosetta_coarse5_backlog_20260904/targeted_rosetta_coarse5_merged_20260904.csv"
)

SOURCE_SPECS = (
    {
        "name": "pbp2a_generation106",
        "target": "pbp2a",
        "generation": "106",
        "report": "reports/pbp2a_pepmlm_pepflow_hybrid_20260904_run2",
        "queue": "coarse5_prepared/coarse5_prepared_queue.csv",
        "score": "candidate_scores_calibrated.csv",
        "challenger": "challenger/challenger_review.csv",
        "close": "pg_materialization_close_receipt.json",
        "qd": "provisional_qd.json",
    },
    {
        "name": "gyra_generation3",
        "target": "gyra",
        "generation": "3",
        "report": "reports/gyrA_pepflow_qd_neighbor_next_20260904",
        "queue": "coarse5_prepared/coarse5_prepared_queue.csv",
        "score": "candidate_scores_calibrated.csv",
        "challenger": "challenger/challenger_review.csv",
        "close": "pg_materialization_close_receipt.json",
        "qd": "provisional_qd.json",
    },
    {
        "name": "angpt1_v2b",
        "target": "angpt1",
        "generation": "v2b",
        "report": "reports/angpt1_pepmlm_qd_neighbor_v2b_20260904",
        "queue": "resume_v2b/coarse5_prepared/coarse5_prepared_queue.csv",
        "score": "candidate_scores_calibrated.csv",
        "challenger": "challenger/challenger_review.csv",
        "close": "pg_materialization_close_receipt.json",
        "qd": "resume_v2b/qd_evidence_persistence_receipt.json",
    },
    {
        "name": "fgf2_generation5",
        "target": "fgf2",
        "generation": "5",
        "report": "reports/fgf2_pepflow_qd_neighbor_vnext_20260904_generation5_v4",
        "queue": "coarse5_prepared/coarse5_prepared_queue.csv",
        "score": "candidate_scores_calibrated.csv",
        "challenger": "challenger/challenger_review.csv",
        "close": "close_receipt.json",
        "qd": "provisional_qd.json",
        "allow_retained_challenger_conflict": "true",
    },
)


def discover_queue_specs(root: Path) -> list[dict[str, str]]:
    """Discover compact queue receipts without treating arbitrary reports as input."""
    explicit_reports = {spec["report"] for spec in SOURCE_SPECS}
    specs: list[dict[str, str]] = []
    for queue_path in sorted((root / "reports").rglob("coarse5_prepared_queue.csv")):
        relative = queue_path.relative_to(root).as_posix()
        rows = read_csv(queue_path)
        if not rows:
            continue
        current = queue_path.parent
        while current.parent.name != "reports" and current != root:
            current = current.parent
        report = current.relative_to(root).as_posix()
        if report in explicit_reports:
            continue
        eligibility_path = current / "targeted_backlog_eligibility.json"
        if eligibility_path.exists():
            eligibility = read_json(eligibility_path)
            if (
                isinstance(eligibility, dict)
                and eligibility.get("effective_status") != "authoritative"
            ):
                continue
        target = norm_target(rows[0].get("target_key"))
        if target not in TARGETS:
            continue
        score_candidates = (
            "candidate_scores_calibrated.csv",
            "candidate_scores.csv",
            "selected_candidate_scores.csv",
            "materialization_candidate_scores.csv",
            "score_all_verified/candidate_scores.csv",
            "score_all_verified_runtime/candidate_scores.csv",
            "score_all_frozen_runtime/candidate_scores.csv",
            "score_all/candidate_scores.csv",
            "materialization_input/candidate_scores.csv",
            "materialization_inputs/candidate_scores.csv",
        )
        challenger_candidates = (
            "challenger/challenger_review.csv",
            "challenger_review.csv",
            "selected_challenger_review.csv",
            "challenger_primary_eb85/challenger_review.csv",
            "materialization_input/challenger_review.csv",
            "materialization_inputs/challenger_review.csv",
        )
        qd_candidates = (
            "provisional_qd.json",
            "qd_receipt.json",
            "qd/qd_receipt.json",
            "qd_selection_receipt.json",
            "structure_queue_receipt.json",
        )
        close_candidates = (
            "pg_materialization_close_receipt.json",
            "close_receipt.json",
        )

        def pick(values: tuple[str, ...], base: Path = current) -> str:
            return next((value for value in values if (base / value).exists()), "")

        score = pick(score_candidates)
        challenger = pick(challenger_candidates)
        qd = pick(qd_candidates)
        close = pick(close_candidates)
        if not qd:
            qd = "__queue__"
        if not all((score, challenger, close)):
            continue
        close_payload = read_json(current / close)
        generation_value = (
            close_payload.get("generation") if isinstance(close_payload, dict) else None
        )
        if not isinstance(generation_value, (str, int, float)):
            generation_path = current / "generation_receipt.json"
            if generation_path.exists():
                generation_payload = read_json(generation_path)
                if isinstance(generation_payload, dict):
                    generation_value = generation_payload.get("generation")
        generation = (
            text(generation_value)
            if isinstance(generation_value, (str, int, float))
            else "discovered"
        )
        specs.append(
            {
                "name": f"discovered:{report}",
                "target": target,
                "generation": generation,
                "report": report,
                "queue": queue_path.relative_to(current).as_posix(),
                "score": score,
                "challenger": challenger,
                "close": close,
                "qd": qd,
                "queue_relative": relative,
            }
        )
    return specs


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text(value: object) -> str:
    return str(value or "").strip()


def norm_target(value: object) -> str:
    return text(value).casefold()


def qd_contribution(value: object) -> str:
    value = text(value).casefold()
    return "incumbent_replacement" if value in {"replacement", "incumbent_replacement"} else value


def qd_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    values = payload.get("contributions", payload.get("items", []))
    if isinstance(values, dict):
        values = [values]
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def index_by_hash(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        text(row.get("sequence_sha256")).casefold(): row
        for row in rows
        if text(row.get("sequence_sha256"))
    }


def score_passes(row: dict[str, str]) -> bool:
    return all(
        (
            truth(row.get("formal_12_complete")),
            integer(row.get("formal_metric_count")) == 12,
            truth(row.get("display_eligible")),
            truth(
                row.get("excellent_sequence_stage_calibrated", row.get("excellent_sequence_stage"))
            ),
            integer(
                row.get(
                    "activity_model_support_count_calibrated",
                    row.get("activity_model_support_count"),
                )
            )
            >= 2,
            number(row.get("guruprasad_instability_index"), 1e9) <= 50,
            text(row.get("toxinpred3_label")) == "Non-Toxin",
            text(row.get("macrel_hemolysis_label")).casefold() == "low",
        )
    )


def challenger_passes(row: dict[str, str], allow_retained_conflict: bool = False) -> bool:
    return bool(
        text(row.get("hemopi2_classification_label"))
        and text(row.get("validator_version"))
        and text(row.get("hemopi2_classification_score"))
        and text(row.get("hemopi2_hc50_um"))
        and (
            text(row.get("challenger_conflict_status")) in CONFLICT_OK
            or (
                allow_retained_conflict
                and text(row.get("challenger_conflict_status")) == RETAINED_CONFLICT
            )
        )
    )


def close_persistence(close: dict[str, Any]) -> dict[str, Any]:
    return close.get("persistence", {}) if isinstance(close.get("persistence"), dict) else {}


def close_is_authoritative(close: dict[str, Any], target: str, run_id: str, count: int) -> bool:
    persistence = close_persistence(close)
    if not persistence:
        postgresql = close.get("postgresql")
        if isinstance(postgresql, dict):
            return bool(
                text(postgresql.get("run_id")) == run_id
                and integer(postgresql.get("candidate_count")) == count
                and integer(postgresql.get("evaluation_count")) == count * 17
                and text(postgresql.get("status", "succeeded"))
                in {"succeeded", "readback_verified"}
                and integer(postgresql.get("subject_run_drift_count")) == 0
                and integer(postgresql.get("replay_count")) == 0
            )
        readback = close.get("pg_readback")
        if isinstance(readback, dict):
            return bool(
                text(close.get("materialization_run_id")) == run_id
                and integer(readback.get("candidate_count")) == count
                and integer(readback.get("evaluation_count")) == count * 17
                and integer(readback.get("identity_drift_count")) == 0
                and integer(readback.get("replay_count")) == 0
                and not truth(close.get("historical_runs_modified"))
            )
        materialization = close.get("materialization")
        if isinstance(materialization, dict):
            materialized_count = integer(
                materialization.get(
                    "candidate_count", materialization.get("authoritative_candidate_count")
                )
            )
            stage_counts = close.get("stage_counts", {})
            evaluation_count = integer(materialization.get("evaluation_count"))
            if not materialized_count and isinstance(stage_counts, dict):
                materialized_count = integer(stage_counts.get("materialized_candidate"))
            if not evaluation_count and isinstance(stage_counts, dict):
                evaluation_count = integer(stage_counts.get("inserted_evaluation"))
            materialized_run = text(materialization.get("run_id", close.get("run_id")))
            return bool(
                materialized_run == run_id
                and materialized_count == count
                and evaluation_count == count * 17
                and text(materialization.get("status", "succeeded"))
                in {"succeeded", "readback_verified"}
                and not truth(close.get("historical_runs_modified"))
                and integer(materialization.get("identity_drift_count")) == 0
                and integer(materialization.get("global_exact_replay_skip_count")) == 0
            )
        materialized_count = integer(
            close.get(
                "materialized_candidate_count",
                close.get("candidate_count", close.get("pg_exact_new_count")),
            )
        )
        evaluation_count = integer(close.get("evaluation_count"))
        stage_counts = close.get("stage_counts", {})
        if not materialized_count and isinstance(stage_counts, dict):
            materialized_count = integer(stage_counts.get("materialized_candidate"))
        if not evaluation_count and isinstance(stage_counts, dict):
            evaluation_count = integer(stage_counts.get("inserted_evaluation"))
        return bool(
            text(close.get("run_id", close.get("materialization_run_id"))) == run_id
            and materialized_count == count
            and evaluation_count == count * 17
            and integer(close.get("identity_drift_count")) == 0
            and integer(close.get("replay_count")) == 0
            and not truth(close.get("historical_runs_modified"))
        )
    final_qd = close.get("final_qd", {})
    if not isinstance(final_qd, dict):
        final_qd = {}
    replay = persistence.get("replay")
    replay_ok = truth(replay) or (
        isinstance(replay, dict) and text(replay.get("status")).startswith("verified")
    )
    return bool(
        norm_target(close.get("target_key", target)) == target
        and text(persistence.get("run_id")) == run_id
        and integer(persistence.get("authoritative_candidate_count")) == count
        and integer(persistence.get("evaluation_count")) == count * 17
        and truth(persistence.get("exact_binding"))
        and replay_ok
        and number(persistence.get("drift"), 1) == 0
        and truth(final_qd.get("formal_pg_new", True))
        and not truth(final_qd.get("future_priority_only"))
    )


def normalize_source_rows(
    spec: dict[str, str], root: Path
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    report = root / spec["report"]
    queue_path = report / spec["queue"]
    score_path = report / spec["score"]
    challenger_path = report / spec["challenger"]
    close_path = report / spec["close"]
    qd_path = report / spec["qd"]
    queue = read_csv(queue_path)
    scores = index_by_hash(read_csv(score_path))
    challengers = index_by_hash(read_csv(challenger_path))
    close = read_json(close_path)
    qd_payload = {} if spec["qd"] == "__queue__" else read_json(qd_path)
    qd_by_hash = {
        text(item.get("candidate_id", item.get("sequence_sha256"))).casefold(): item
        for item in qd_items(qd_payload)
    }
    run_ids = {text(row.get("run_id")) for row in queue}
    if len(run_ids) != 1:
        raise ValueError(f"source run is not singular: {spec['name']}")
    run_id = next(iter(run_ids))
    if not close_is_authoritative(close, spec["target"], run_id, len(queue)):
        raise ValueError(f"close receipt is not authoritative: {spec['name']}")
    rows: list[dict[str, Any]] = []
    diagnostics = Counter()
    for item in queue:
        sequence_sha = text(item.get("sequence_sha256")).casefold()
        score = scores.get(sequence_sha, {})
        challenger = challengers.get(sequence_sha, {})
        qd = qd_by_hash.get(sequence_sha, {})
        contribution = qd_contribution(item.get("qd_contribution", qd.get("contribution")))
        valid = contribution in CONTRIBUTIONS and score_passes(score) and challenger_passes(
            challenger,
            allow_retained_conflict=truth(spec.get("allow_retained_challenger_conflict")),
        )
        if not valid:
            diagnostics["source_candidate_gate_failed"] += 1
            continue
        candidate_id = text(item.get("candidate_id", item.get("authoritative_candidate_id")))
        aliases = {text(item.get("task_key"))}
        rows.append(
            {
                "target_key": spec["target"],
                "target": spec["target"].upper() if spec["target"] != "gyra" else "GyrA",
                "run_id": run_id,
                "candidate_id": candidate_id,
                "sequence_sha256": sequence_sha,
                "sequence": text(item.get("sequence")),
                "source": text(item.get("source", spec["name"])),
                "source_report": spec["report"],
                "source_generation": spec["generation"],
                "qd_contribution": contribution,
                "qd_cell": text(item.get("qd_cell", item.get("provisional_qd_cell"))),
                "qd_new_cell": int(contribution == "empty_cell"),
                "qd_replacement": int(contribution == "incumbent_replacement"),
                "qd_quality": number(qd.get("quality", 0)),
                "archive_qd_score": number(qd_payload.get("archive_qd_score", 0))
                if isinstance(qd_payload, dict)
                else 0,
                "activity_support": integer(
                    score.get(
                        "activity_model_support_count_calibrated",
                        score.get("activity_model_support_count"),
                    )
                ),
                "formal12": 1,
                "display": 1,
                "challenger_status": text(challenger.get("challenger_conflict_status")),
                "quality_eligible": 1,
                "status": text(item.get("status", "prepared")),
                "source_task_aliases": aliases,
            }
        )
    return rows, {
        "name": spec["name"],
        "report": spec["report"],
        "generation": spec["generation"],
        "queue_sha256": sha256(queue_path),
        "score_sha256": sha256(score_path),
        "challenger_sha256": sha256(challenger_path),
        "close_sha256": sha256(close_path),
        "qd_sha256": sha256(queue_path if spec["qd"] == "__queue__" else qd_path),
        "queue_rows": len(queue),
        "validated_rows": len(rows),
        "diagnostics": dict(sorted(diagnostics.items())),
    }


def legacy_rows(root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = root / OLD_CSV
    rows: list[dict[str, Any]] = []
    for item in read_csv(path):
        row = dict(item)
        row["target_key"] = norm_target(row.get("target_key"))
        row["source_report"] = str(OLD_CSV.parent)
        row["source_generation"] = "legacy_merged"
        if text(row.get("status")) not in {"completed", "active"}:
            row["status"] = "prepared"
        row["task_aliases"] = json.dumps([text(row.get("rosetta_task_key"))], separators=(",", ":"))
        row["requires_remote_exact_preflight"] = "true"
        row["source_task_aliases"] = {text(row.get("rosetta_task_key"))}
        for key in ("qd_new_cell", "qd_replacement", "formal12", "display", "quality_eligible"):
            row[key] = integer(row.get(key))
        for key in (
            "priority",
            "pool_a_count",
            "balance_gap_to_50",
            "activity_support",
            "pg_succeeded_evaluations",
            "nstruct",
            "existing_decoy_count",
            "remaining_decoy_count",
            "structure_evidence_count",
        ):
            row[key] = integer(row.get(key))
        for key in ("qd_quality", "archive_qd_score"):
            row[key] = number(row.get(key))
        rows.append(row)
    return rows, {"report": str(OLD_CSV.parent), "sha256": sha256(path), "rows": len(rows)}


async def pg_snapshot(
    pg_url: str, rows: list[dict[str, Any]]
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    if not rows:
        return {}, {"status": "not_needed"}
    run_ids = [text(row["run_id"]) for row in rows]
    candidate_ids = [text(row["candidate_id"]) for row in rows]
    connection = await asyncpg.connect(pg_url, timeout=5, command_timeout=25)
    try:
        await connection.execute("SET statement_timeout = '20000ms'")
        await connection.execute("SET lock_timeout = '2000ms'")
        candidates = await connection.fetch(
            """
            SELECT c.id AS candidate_id, c.run_id, c.sequence, c.sequence_sha256,
                   t.accession AS target, c.metadata_json,
                   COUNT(DISTINCT e.id) FILTER (
                       WHERE e.status='succeeded'
                   ) AS succeeded_evaluations,
                   COUNT(DISTINCT s.id) AS structure_evidence_count,
                   COUNT(DISTINCT s.id) FILTER (
                       WHERE s.evidence_kind='rosetta_decoy'
                   ) AS rosetta_decoy_count
            FROM unnest($1::uuid[], $2::uuid[]) AS wanted(run_id, candidate_id)
            JOIN candidates c ON c.run_id=wanted.run_id AND c.id=wanted.candidate_id
            JOIN experiment_runs r ON r.id=c.run_id
            JOIN targets t ON t.id=r.target_id
            LEFT JOIN evaluations e ON e.candidate_id=c.id
            LEFT JOIN multitarget_structure_evidence_records s
              ON s.run_id=c.run_id AND s.candidate_id=c.id
            GROUP BY c.id, c.run_id, c.sequence, c.sequence_sha256, t.accession, c.metadata_json
            """,
            run_ids,
            candidate_ids,
        )
        evaluation_rows = await connection.fetch(
            """
            SELECT e.subject_run_id, e.candidate_id, e.model_release_key,
                   COUNT(*) AS evaluation_count,
                   COUNT(*) FILTER (WHERE e.status='succeeded') AS succeeded_count
            FROM unnest($1::uuid[], $2::uuid[]) AS wanted(run_id, candidate_id)
            JOIN evaluations e
              ON e.subject_run_id=wanted.run_id AND e.candidate_id=wanted.candidate_id
            GROUP BY e.subject_run_id, e.candidate_id, e.model_release_key
            """,
            run_ids,
            candidate_ids,
        )
        lifecycle_rows = await connection.fetch(
            """
            SELECT aggregate_id, event_type, payload_json
            FROM lifecycle_events
            WHERE aggregate_id = ANY($1::uuid[])
              AND (event_type ILIKE '%rosetta%' OR event_type ILIKE '%structure%'
                   OR payload_json ? 'task_key')
            """,
            candidate_ids,
        )
    finally:
        await connection.close()
    evaluations: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in evaluation_rows:
        evaluations[str(item["candidate_id"])].append(dict(item))
    lifecycle: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in lifecycle_rows:
        lifecycle[str(item["aggregate_id"])].append(dict(item))
    result = {}
    for item in candidates:
        item = dict(item)
        cid = str(item["candidate_id"])
        item["evaluations"] = evaluations.get(cid, [])
        item["lifecycle_events"] = lifecycle.get(cid, [])
        result[(str(item["run_id"]), cid)] = item
    return result, {
        "status": "readback_verified",
        "query_mode": "exact_run_id+candidate_id",
        "candidate_rows": len(candidates),
        "evaluation_group_rows": len(evaluation_rows),
        "lifecycle_rows": len(lifecycle_rows),
        "sequence_history_scan": False,
    }


def classify_task(
    pg: dict[str, Any], aliases: set[str], source_prepared: bool
) -> tuple[str, int, list[str]]:
    events = pg.get("lifecycle_events", [])
    event_text = json.dumps(events, default=str).casefold()
    rosetta_decoys = integer(pg.get("rosetta_decoy_count"))
    if rosetta_decoys >= 5 or any(
        word in event_text for word in ("completed", "structure_complete")
    ):
        status = "completed"
    elif rosetta_decoys or any(word in event_text for word in ("active", "running", "started")):
        status = "active"
    elif source_prepared:
        status = "prepared"
    else:
        status = "new_ready"
    evidence_count = integer(pg.get("structure_evidence_count"))
    return status, max(evidence_count, integer(pg.get("rosetta_decoy_count"))), sorted(aliases)


def merge_rows(
    legacy: list[dict[str, Any]],
    recent: list[dict[str, Any]],
    pg_rows: dict[tuple[str, str], dict[str, Any]],
    pool_counts: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_identity: dict[tuple[str, str, str], dict[str, Any]] = {}
    exclusions = Counter()
    for incoming in [*legacy, *recent]:
        target = norm_target(incoming.get("target_key"))
        run_id = text(incoming.get("run_id"))
        cid = text(incoming.get("candidate_id"))
        if target not in TARGETS or not run_id or not cid:
            exclusions["unresolved_identity"] += 1
            continue
        identity = (target, run_id, cid)
        existing = by_identity.get(identity)
        if existing is None:
            by_identity[identity] = incoming
        else:
            existing.setdefault("source_task_aliases", set()).update(
                incoming.get("source_task_aliases", set())
            )
            exclusions["duplicate_identity_merged"] += 1
    output = []
    for row in by_identity.values():
        target = norm_target(row["target_key"])
        pg = pg_rows.get((text(row["run_id"]), text(row["candidate_id"])))
        if pg is None:
            exclusions["pg_identity_missing"] += 1
            row["status"] = (
                "prepared" if row.get("status") == "prepared_not_dispatched" else "new_ready"
            )
            row["pg_succeeded_evaluations"] = 0
            row["structure_evidence_count"] = 0
            row["existing_decoy_count"] = integer(row.get("existing_decoy_count"))
            row["evidence_sources"] = "local_receipt_only_pg_identity_unavailable"
        else:
            status, evidence_count, aliases = classify_task(
                pg,
                set(row.get("source_task_aliases", set())),
                bool(row.get("status") in {"prepared", "prepared_not_dispatched"}),
            )
            row["status"] = status
            row["pg_succeeded_evaluations"] = integer(pg.get("succeeded_evaluations"))
            row["structure_evidence_count"] = evidence_count
            row["existing_decoy_count"] = max(
                integer(row.get("existing_decoy_count")), evidence_count
            )
            row["evidence_sources"] = "postgresql_candidate_evaluation_structure_lifecycle"
            row["source_task_aliases"] = set(aliases)
        row["target_key"] = target
        row["pool_a_count"] = pool_counts.get(target, integer(row.get("pool_a_count")))
        row["balance_gap_to_50"] = max(0, 50 - integer(row["pool_a_count"]))
        row["rosetta_task_key"] = task_key(target, text(row["run_id"]), text(row["candidate_id"]))
        aliases = set(row.pop("source_task_aliases", set()))
        aliases.discard(row["rosetta_task_key"])
        row["task_aliases"] = json.dumps(sorted(aliases), separators=(",", ":"))
        row["nstruct"] = 5
        row["remaining_decoy_count"] = max(0, 5 - integer(row["existing_decoy_count"]))
        row["requires_remote_exact_preflight"] = str(
            row["status"] not in {"completed", "active"}
        ).lower()
        output.append(row)
    output.sort(
        key=lambda row: (
            -integer(row.get("balance_gap_to_50")),
            -integer(row.get("qd_new_cell")),
            -integer(row.get("qd_replacement")),
            -number(row.get("qd_quality")),
            text(row.get("target_key")),
            text(row.get("run_id")),
            text(row.get("candidate_id")),
        )
    )
    for index, row in enumerate(output, 1):
        row["priority"] = index
    return output, dict(sorted(exclusions.items()))


def write_outputs(rows: list[dict[str, Any]], receipt: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "targeted_rosetta_coarse5_backlog_vnext.csv"
    json_path = output_dir / "targeted_rosetta_coarse5_backlog_vnext.json"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in CSV_FIELDS})
    receipt["csv_sha256"] = sha256(csv_path)
    json_path.write_text(
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def build(args: argparse.Namespace) -> dict[str, Any]:
    root = args.repo_root.resolve()
    legacy, legacy_meta = legacy_rows(root)
    recent: list[dict[str, Any]] = []
    source_meta = []
    source_exclusions = Counter()
    specs = [*SOURCE_SPECS, *discover_queue_specs(root)]
    for spec in specs:
        try:
            values, meta = normalize_source_rows(spec, root)
        except ValueError:
            source_exclusions["source_close_or_identity_not_authoritative"] += 1
            source_meta.append(
                {
                    "name": spec["name"],
                    "report": spec["report"],
                    "status": "excluded",
                    "reason": "source_close_or_identity_not_authoritative",
                }
            )
            continue
        recent.extend(values)
        source_meta.append(meta)
    pool_payload = read_json(root / args.pool_counts_json)
    targets = pool_payload.get("targets", {}) if isinstance(pool_payload, dict) else {}
    pool_counts = {
        target: integer(targets.get(target, {}).get("pool_a_total_candidate_count", 0))
        for target in TARGETS
    }
    pg_status: dict[str, Any]
    try:
        pg_rows, pg_status = await pg_snapshot(args.pg_url, [*legacy, *recent])
    except Exception as exc:  # fail closed: preserve local source status, never invent PG state
        pg_rows = {}
        pg_status = {
            "status": "unavailable",
            "error_class": type(exc).__name__,
            "sequence_history_scan": False,
        }
    rows, exclusions = merge_rows(legacy, recent, pg_rows, pool_counts)
    exclusions.update(source_exclusions)
    status_counts = Counter(text(row.get("status")) for row in rows)
    target_counts = Counter(text(row.get("target_key")) for row in rows)
    existing_total = sum(integer(row.get("existing_decoy_count")) for row in rows)
    recent14_count = sum(text(row.get("source_generation")) != "legacy_merged" for row in rows)
    receipt = {
        "schema_version": "ampgent.targeted-rosetta-coarse5-backlog-vnext.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "identity": "target+run_id+authoritative_candidate_id+sequence_sha256+canonical_task_key",
        "scope": (
            "target-specific prepared/QD queues only; "
            "target-agnostic/proposal-only/failed/unresolved excluded"
        ),
        "selection": (
            "formal12+display+activity_support>=2+challenger_complete+QD(new_cell|replacement)"
        ),
        "task_key_contract": "rosetta5:<target>:<run_id>:<candidate_id>",
        "nstruct": 5,
        "dispatch_allowed": False,
        "requires_remote_exact_preflight": True,
        "remote_probe_performed": False,
        "remote_write_performed": False,
        "historical_runs_modified": False,
        "inputs": {
            "legacy": legacy_meta,
            "recent": source_meta,
            "pool_counts_json": str(args.pool_counts_json),
        },
        "postgresql": pg_status,
        "summary": {
            "old_baseline_rows": len(legacy),
            "rows": len(rows),
            "target_counts": {target: target_counts.get(target, 0) for target in TARGETS},
            "source_generation_counts": dict(
                sorted(Counter(text(row.get("source_generation")) for row in rows).items())
            ),
            "status_counts": {
                key: status_counts.get(key, 0)
                for key in ("completed", "active", "prepared", "new_ready")
            },
            "unique_identity": len(
                {(row["target_key"], row["run_id"], row["candidate_id"]) for row in rows}
            )
            == len(rows),
            "unique_sequence_sha256": len({row["sequence_sha256"] for row in rows}) == len(rows),
            "unique_canonical_task_key": len({row["rosetta_task_key"] for row in rows})
            == len(rows),
            "existing_decoys_total": existing_total,
            "remaining_decoys_total": sum(
                integer(row.get("remaining_decoy_count")) for row in rows
            ),
            "recent14": {
                "count": recent14_count,
                "denominator": len(rows),
                "coverage": round(recent14_count / len(rows), 6) if rows else 0,
            },
            "merge_deduplicated_alias_rows": exclusions.get("duplicate_identity_merged", 0),
            "pg_exclusions": {
                key: value for key, value in exclusions.items() if key in {"pg_identity_missing"}
            },
            "source_exclusions": {
                key: value for key, value in exclusions.items() if key.startswith("source_")
            },
        },
        "rows": rows,
    }
    write_outputs(rows, receipt, root / args.output_dir)
    return {"rows": len(rows), "summary": receipt["summary"], "postgresql": pg_status}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--pg-url", default=os.environ.get("PEPAGENT_DATABASE_URL_PLAIN"))
    parser.add_argument(
        "--pool-counts-json",
        type=Path,
        default=Path("reports/ampgent_pool_a_live_20260903T0605Z.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/targeted_rosetta_coarse5_backlog_20260904/vnext"),
    )
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(build(args)), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
