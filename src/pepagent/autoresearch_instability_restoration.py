from __future__ import annotations

import json
import math
import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    persist_operational_call,
)
from pepagent.db.models import (
    Artifact,
    EvidenceArtifact,
    EvidenceArtifactLocation,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.storage.object_store import ContentAddressedObjectStore, StoredObject

RESTORATION_SCHEMA = "ampgent.autoresearch-instability-score-restoration.1"
RESTORATION_POLICY = "guruprasad-successful-finite-score-lt50-ood-audit-only"
RESTORATION_ACTOR = "autoresearch-instability-score-restoration-v1"
RESTORATION_TOOL = "autoresearch-instability-ood-gate-restoration"
RESTORATION_TOOL_VERSION = "1.0.0"
FORMAL_METRIC_NAMES = (
    "amp_read_log10_mic_um",
    "guruprasad_instability_index",
    "hydrophobic_moment_eisenberg",
    "hydrophobic_ratio_modlamp",
    "llamp_log10_mic_um",
    "macrel_amp_probability",
    "macrel_hemolysis_label",
    "macrel_hemolysis_probability",
    "maximum_hydrophobic_run",
    "net_charge_ph7_4",
    "toxinpred3_hybrid_score",
    "toxinpred3_label",
)
TARGET_KEYS = ("acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1")
GOLD_ARCHIVES = frozenset(
    {"activity_consensus", "activity_safety_balance", "stability_degradation"}
)


def _iso_utc(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _postgres_timestamp_cutoff(value: datetime) -> datetime:
    """Return the UTC-naive value required by the legacy timestamp columns."""

    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).replace(tzinfo=None)


def canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def validate_restoration_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != RESTORATION_SCHEMA:
        raise ValueError("instability restoration manifest schema differs")
    if manifest.get("policy") != RESTORATION_POLICY:
        raise ValueError("instability restoration policy differs")
    if manifest.get("archive_membership_rewrite_count") != 0:
        raise ValueError("restoration must not rewrite historical archive membership")
    rows = manifest.get("restored_candidates")
    if not isinstance(rows, list):
        raise ValueError("restoration manifest candidates must be a list")
    identities: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("restoration candidate must be an object")
        identity = (str(row.get("run_id", "")), str(row.get("candidate_id", "")))
        if not all(identity) or identity in identities:
            raise ValueError("restoration candidate identities must be unique")
        identities.add(identity)
        if row.get("display_hard_gate_pass") is not True:
            raise ValueError("restoration candidate does not pass the display hard gate")
        if row.get("instability_score_qualified") is not True:
            raise ValueError("restoration candidate is not score-qualified")
        if row.get("guruprasad_instability_ood_audit") is not True:
            raise ValueError("restoration candidate was not excluded by the old OOD rule")
        score = row.get("guruprasad_instability_index")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("restoration candidate lacks a numeric instability score")
        if not math.isfinite(float(score)) or float(score) >= 50.0:
            raise ValueError("restoration candidate fails the score-only instability gate")
        evaluation_ids = row.get("evaluation_ids")
        if not isinstance(evaluation_ids, Mapping) or set(evaluation_ids) != set(
            FORMAL_METRIC_NAMES
        ):
            raise ValueError("restoration candidate lacks its exact formal-12 evidence")
    summary = manifest.get("summary")
    if not isinstance(summary, Mapping):
        raise ValueError("restoration manifest lacks a summary")
    if int(summary.get("candidate_count", -1)) != len(rows):
        raise ValueError("restoration manifest candidate count differs")


async def _formal_runs(
    session: AsyncSession, cutoff: datetime
) -> list[dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT id, status, spec_json->>'branch_key' AS target_key,
                   spec_json->>'release_sha256' AS release_sha256,
                   created_at
            FROM experiment_runs
            WHERE spec_json->>'run_kind'='autoresearch_closed_loop_formal_branch'
              AND created_at <= :cutoff
            ORDER BY created_at, id
            """
        ),
        {"cutoff": _postgres_timestamp_cutoff(cutoff)},
    )
    rows = [dict(row) for row in result.mappings()]
    if any(row["target_key"] not in TARGET_KEYS for row in rows):
        raise ValueError("formal AutoResearch history contains an unknown target")
    return rows


async def _restored_candidates(
    session: AsyncSession, cutoff: datetime
) -> list[dict[str, Any]]:
    required = list(FORMAL_METRIC_NAMES)
    result = await session.execute(
        text(
            """
            WITH formal_runs AS MATERIALIZED (
              SELECT id, status, spec_json->>'branch_key' AS target_key,
                     spec_json->>'release_sha256' AS release_sha256
              FROM experiment_runs
              WHERE spec_json->>'run_kind'='autoresearch_closed_loop_formal_branch'
                AND created_at <= :cutoff
            ), ranked AS MATERIALIZED (
              SELECT r.id AS run_id, r.status AS run_status, r.target_key,
                     r.release_sha256, c.id AS candidate_id, c.sequence,
                     c.sequence_sha256, c.generation, e.id AS evaluation_id,
                     e.metric_name, e.numeric_value, e.text_value,
                     e.status AS evaluation_status, e.out_of_domain,
                     row_number() OVER (
                       PARTITION BY c.id, e.metric_name
                       ORDER BY e.created_at DESC, e.id DESC
                     ) AS rank
              FROM formal_runs r
              JOIN candidates c ON c.run_id=r.id AND c.created_at <= :cutoff
              JOIN evaluations e ON e.candidate_id=c.id AND e.created_at <= :cutoff
              WHERE e.metric_name = ANY(:required_metrics)
            ), projected AS (
              SELECT run_id, run_status, target_key, release_sha256, candidate_id,
                     sequence, sequence_sha256, generation,
                     count(*) FILTER (WHERE evaluation_status='succeeded') AS metric_count,
                     jsonb_object_agg(metric_name, evaluation_id::text) AS evaluation_ids,
                     max(numeric_value) FILTER (
                       WHERE metric_name='guruprasad_instability_index'
                     ) AS instability,
                     bool_or(out_of_domain) FILTER (
                       WHERE metric_name='guruprasad_instability_index'
                     ) AS instability_ood,
                     max(text_value) FILTER (
                       WHERE metric_name='toxinpred3_label'
                     ) AS toxin_label,
                     max(text_value) FILTER (
                       WHERE metric_name='macrel_hemolysis_label'
                     ) AS hemolysis_label
              FROM ranked
              WHERE rank=1
              GROUP BY run_id, run_status, target_key, release_sha256,
                       candidate_id, sequence, sequence_sha256, generation
            )
            SELECT * FROM projected
            WHERE metric_count=:metric_count
              AND instability < 50.0
              AND instability_ood IS TRUE
              AND lower(replace(coalesce(toxin_label,''),'_','-'))
                    IN ('non-toxin','nontoxin','non-toxic')
              AND lower(coalesce(hemolysis_label,''))='low'
            ORDER BY run_id, candidate_id
            """
        ),
        {
            "cutoff": _postgres_timestamp_cutoff(cutoff),
            "required_metrics": required,
            "metric_count": len(FORMAL_METRIC_NAMES),
        },
    )
    return [dict(row) for row in result.mappings()]


async def _occurrences(
    session: AsyncSession, cutoff: datetime
) -> dict[tuple[str, str], tuple[str, ...]]:
    result = await session.execute(
        text(
            """
            SELECT o.run_id, o.sequence_sha256, o.id
            FROM candidate_occurrences o
            JOIN experiment_runs r ON r.id=o.run_id
            WHERE r.spec_json->>'run_kind'='autoresearch_closed_loop_formal_branch'
              AND r.created_at <= :cutoff AND o.created_at <= :cutoff
            ORDER BY o.run_id, o.sequence_sha256, o.created_at, o.id
            """
        ),
        {"cutoff": _postgres_timestamp_cutoff(cutoff)},
    )
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in result.mappings():
        grouped[(str(row["run_id"]), str(row["sequence_sha256"]))].append(str(row["id"]))
    return {key: tuple(values) for key, values in grouped.items()}


async def _archive_memberships(
    session: AsyncSession, cutoff: datetime
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result = await session.execute(
        text(
            """
            SELECT v.run_id, v.iteration_no, v.archive_name, v.id AS archive_version_id,
                   m.candidate_id, m.change_kind, m.member_ordinal
            FROM autoresearch_archive_versions v
            JOIN experiment_runs r ON r.id=v.run_id
            JOIN autoresearch_archive_memberships m
              ON m.archive_version_id=v.id AND m.is_active
            WHERE r.spec_json->>'run_kind'='autoresearch_closed_loop_formal_branch'
              AND r.created_at <= :cutoff
              AND v.created_at <= :cutoff AND m.created_at <= :cutoff
            ORDER BY v.run_id, m.candidate_id, v.iteration_no,
                     v.archive_name, v.id
            """
        ),
        {"cutoff": _postgres_timestamp_cutoff(cutoff)},
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in result.mappings():
        grouped[(str(row["run_id"]), str(row["candidate_id"]))].append(
            {
                "iteration_no": int(row["iteration_no"]),
                "archive_name": str(row["archive_name"]),
                "archive_version_id": str(row["archive_version_id"]),
                "change_kind": str(row["change_kind"]),
                "member_ordinal": int(row["member_ordinal"]),
            }
        )
    return grouped


async def _latest_checkpoint_iterations(
    session: AsyncSession, cutoff: datetime
) -> dict[str, int]:
    result = await session.execute(
        text(
            """
            SELECT c.run_id, max(c.iteration_no) AS iteration_no
            FROM autoresearch_checkpoints c
            JOIN experiment_runs r ON r.id=c.run_id
            WHERE r.spec_json->>'run_kind'='autoresearch_closed_loop_formal_branch'
              AND r.created_at <= :cutoff AND c.created_at <= :cutoff
            GROUP BY c.run_id
            """
        ),
        {"cutoff": _postgres_timestamp_cutoff(cutoff)},
    )
    return {str(row["run_id"]): int(row["iteration_no"]) for row in result.mappings()}


def _gold_iterations(memberships: Sequence[Mapping[str, Any]]) -> tuple[int, ...]:
    names_by_iteration: dict[int, set[str]] = defaultdict(set)
    for membership in memberships:
        names_by_iteration[int(membership["iteration_no"])].add(
            str(membership["archive_name"])
        )
    return tuple(
        sorted(
            iteration
            for iteration, names in names_by_iteration.items()
            if GOLD_ARCHIVES.issubset(names)
        )
    )


async def build_restoration_manifest(
    session: AsyncSession,
    *,
    snapshot_cutoff: datetime,
) -> dict[str, Any]:
    cutoff = (
        snapshot_cutoff
        if snapshot_cutoff.tzinfo is not None
        else snapshot_cutoff.replace(tzinfo=UTC)
    ).astimezone(UTC)
    runs = await _formal_runs(session, cutoff)
    candidates = await _restored_candidates(session, cutoff)
    occurrences = await _occurrences(session, cutoff)
    memberships = await _archive_memberships(session, cutoff)
    latest_checkpoint = await _latest_checkpoint_iterations(session, cutoff)

    restored: list[dict[str, Any]] = []
    for row in candidates:
        run_id = str(row["run_id"])
        candidate_id = str(row["candidate_id"])
        candidate_memberships = memberships.get((run_id, candidate_id), [])
        gold_iterations = _gold_iterations(candidate_memberships)
        latest_iteration = latest_checkpoint.get(run_id)
        restored.append(
            {
                "run_id": run_id,
                "run_status_at_snapshot": str(row["run_status"]),
                "target_key": str(row["target_key"]),
                "release_sha256": str(row["release_sha256"]),
                "candidate_id": candidate_id,
                "sequence": str(row["sequence"]),
                "sequence_sha256": str(row["sequence_sha256"]),
                "generation": int(row["generation"]),
                "display_hard_gate_pass": True,
                "instability_score_qualified": True,
                "guruprasad_instability_index": float(row["instability"]),
                "guruprasad_instability_ood_audit": True,
                "toxinpred3_label": str(row["toxin_label"]),
                "macrel_hemolysis_label": str(row["hemolysis_label"]),
                "evaluation_ids": dict(sorted(dict(row["evaluation_ids"]).items())),
                "occurrence_ids": list(
                    occurrences.get((run_id, str(row["sequence_sha256"])), ())
                ),
                "archive_memberships": candidate_memberships,
                "gold_frontier_intersection_iterations": list(gold_iterations),
                "latest_checkpoint_iteration": latest_iteration,
                "latest_checkpoint_gold_restored": (
                    latest_iteration is not None and latest_iteration in gold_iterations
                ),
            }
        )
    restored.sort(key=lambda row: (row["run_id"], row["candidate_id"]))

    target_counts = Counter(row["target_key"] for row in restored)
    target_sequences: dict[str, set[str]] = defaultdict(set)
    target_occurrences = Counter()
    release_counts = Counter()
    runs_with_restored: set[str] = set()
    global_sequences: set[str] = set()
    archive_candidates: set[str] = set()
    archive_membership_rows = 0
    gold_candidate_iterations = 0
    latest_gold = Counter()
    for row in restored:
        target = str(row["target_key"])
        target_sequences[target].add(str(row["sequence_sha256"]))
        target_occurrences[target] += len(row["occurrence_ids"])
        release_counts[str(row["release_sha256"])] += 1
        runs_with_restored.add(str(row["run_id"]))
        global_sequences.add(str(row["sequence_sha256"]))
        if row["archive_memberships"]:
            archive_candidates.add(str(row["candidate_id"]))
            archive_membership_rows += len(row["archive_memberships"])
        gold_candidate_iterations += len(row["gold_frontier_intersection_iterations"])
        if row["latest_checkpoint_gold_restored"]:
            latest_gold[target] += 1

    run_counts = Counter(row["run_id"] for row in restored)
    run_occurrences = Counter()
    run_gold = Counter()
    for row in restored:
        run_occurrences[str(row["run_id"])] += len(row["occurrence_ids"])
        run_gold[str(row["run_id"])] += int(row["latest_checkpoint_gold_restored"])
    run_summaries = [
        {
            "run_id": str(run["id"]),
            "target_key": str(run["target_key"]),
            "release_sha256": str(run["release_sha256"]),
            "run_status_at_snapshot": str(run["status"]),
            "restored_candidate_count": int(run_counts[str(run["id"])]),
            "restored_occurrence_count": int(run_occurrences[str(run["id"])]),
            "latest_checkpoint_gold_increment": int(run_gold[str(run["id"])]),
        }
        for run in runs
    ]
    manifest = {
        "schema_version": RESTORATION_SCHEMA,
        "policy": RESTORATION_POLICY,
        "snapshot_cutoff": _iso_utc(cutoff),
        "formal_metric_names": list(FORMAL_METRIC_NAMES),
        "decision_fields": {
            "primary": ["display_hard_gate_pass", "instability_score_qualified"],
            "guruprasad_instability_ood": "descriptive_audit_only",
            "ood_qualified": "deprecated_audit_alias_not_used",
        },
        "archive_membership_rewrite_count": 0,
        "summary": {
            "formal_run_count": len(runs),
            "runs_with_restored_candidates": len(runs_with_restored),
            "candidate_count": len(restored),
            "occurrence_count": sum(len(row["occurrence_ids"]) for row in restored),
            "global_distinct_sequence_count": len(global_sequences),
            "existing_archive_candidate_count": len(archive_candidates),
            "existing_archive_membership_row_count": archive_membership_rows,
            "gold_frontier_candidate_iteration_increment": gold_candidate_iterations,
            "latest_checkpoint_gold_increment": sum(latest_gold.values()),
        },
        "target_summary": {
            target: {
                "candidate_count": int(target_counts[target]),
                "occurrence_count": int(target_occurrences[target]),
                "distinct_sequence_count": len(target_sequences[target]),
                "latest_checkpoint_gold_increment": int(latest_gold[target]),
            }
            for target in TARGET_KEYS
        },
        "release_candidate_counts": dict(sorted(release_counts.items())),
        "run_summary": run_summaries,
        "restored_candidates": restored,
    }
    validate_restoration_manifest(manifest)
    return manifest


def successor_restored_candidate_ids(
    manifest: Mapping[str, Any], *, target_key: str
) -> tuple[str, ...]:
    validate_restoration_manifest(manifest)
    normalized = target_key.strip().casefold()
    if normalized not in TARGET_KEYS:
        raise ValueError("unknown AutoResearch target")
    return tuple(
        sorted(
            str(row["candidate_id"])
            for row in manifest["restored_candidates"]
            if str(row["target_key"]) == normalized
        )
    )


async def _get_or_create_artifact(
    session: AsyncSession,
    stored: StoredObject,
) -> Artifact:
    artifact = await session.scalar(select(Artifact).where(Artifact.sha256 == stored.sha256))
    if artifact is None:
        artifact = Artifact(
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            media_type=stored.media_type,
            storage_uri=stored.uri,
            metadata_json={
                "schema_version": RESTORATION_SCHEMA,
                "source": "autoresearch-instability-score-restoration",
            },
        )
        session.add(artifact)
        await session.flush()
    elif artifact.size_bytes != stored.size_bytes or artifact.media_type != stored.media_type:
        raise ValueError("restoration artifact content identity conflicts with PostgreSQL")
    return artifact


def _operational_record(
    *,
    target_key: str,
    manifest_sha256: str,
    manifest: Mapping[str, Any],
    status: str,
    output: Mapping[str, Any] | None = None,
) -> OperationalCallRecord:
    now = datetime.now(UTC)
    return OperationalCallRecord(
        operation_key=f"instability-score-restoration:{manifest_sha256}:{target_key}",
        target_key=target_key,  # type: ignore[arg-type]
        purpose="audit_reconciliation",
        tool_name=RESTORATION_TOOL,
        tool_version=RESTORATION_TOOL_VERSION,
        status=status,  # type: ignore[arg-type]
        input_payload={
            "manifest_sha256": manifest_sha256,
            "snapshot_cutoff": manifest["snapshot_cutoff"],
            "policy": RESTORATION_POLICY,
        },
        parameters={
            "historical_evaluations_mutated": False,
            "historical_archives_mutated": False,
            "historical_checkpoints_mutated": False,
        },
        execution_context={
            "authoritative_database": "postgresql",
            "schema_version": RESTORATION_SCHEMA,
        },
        output_payload=dict(output) if output is not None else None,
        queued_at=now,
        started_at=now,
        finished_at=now if status != "running" else None,
        actor=RESTORATION_ACTOR,
    )


async def persist_restoration(
    *,
    manifest: Mapping[str, Any],
    manifest_bytes: bytes,
    session_factory: Any,
    object_store: ContentAddressedObjectStore | None = None,
) -> dict[str, Any]:
    validate_restoration_manifest(manifest)
    manifest_sha256 = sha256_bytes(manifest_bytes)
    if canonical_manifest_bytes(manifest) != manifest_bytes:
        raise ValueError("restoration manifest bytes are not canonical")
    store = object_store or ContentAddressedObjectStore()
    stored = store.put_bytes(manifest_bytes, "application/json")
    if stored.sha256 != manifest_sha256 or stored.size_bytes != len(manifest_bytes):
        raise ValueError("restoration manifest CAS upload differs")

    for target in TARGET_KEYS:
        async with session_factory() as session, session.begin():
            await persist_operational_call(
                session,
                _operational_record(
                    target_key=target,
                    manifest_sha256=manifest_sha256,
                    manifest=manifest,
                    status="running",
                ),
            )

    rows_by_run: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in manifest["restored_candidates"]:
        rows_by_run[str(row["run_id"])].append(row)
    run_summary_by_id = {
        str(row["run_id"]): row for row in manifest["run_summary"]
    }
    persisted_candidate_events = 0
    persisted_run_events = 0
    for run_id in sorted(run_summary_by_id):
        async with session_factory() as session, session.begin():
            repository = ExperimentRepository(session)
            for row in rows_by_run.get(run_id, []):
                candidate_id = uuid.UUID(str(row["candidate_id"]))
                idempotency_key = (
                    f"{RESTORATION_POLICY}:{manifest_sha256}:{candidate_id}"
                )
                await repository.append_event(
                    "candidate",
                    candidate_id,
                    "candidate.instability_ood_gate_restored",
                    RESTORATION_ACTOR,
                    {
                        "event_idempotency_key": idempotency_key,
                        "manifest_sha256": manifest_sha256,
                        "run_id": run_id,
                        "target_key": row["target_key"],
                        "sequence_sha256": row["sequence_sha256"],
                        "display_hard_gate_pass": True,
                        "instability_score_qualified": True,
                        "guruprasad_instability_index": row[
                            "guruprasad_instability_index"
                        ],
                        "guruprasad_instability_ood_audit": True,
                        "evaluation_ids": row["evaluation_ids"],
                        "occurrence_ids": row["occurrence_ids"],
                        "old_evidence_mutated": False,
                    },
                    idempotency_key=idempotency_key,
                )
                persisted_candidate_events += 1
            summary = run_summary_by_id[run_id]
            run_key = f"{RESTORATION_POLICY}:{manifest_sha256}:{run_id}:summary"
            await repository.append_event(
                "run",
                uuid.UUID(run_id),
                "run.instability_ood_gate_restoration_recorded",
                RESTORATION_ACTOR,
                {
                    "event_idempotency_key": run_key,
                    "manifest_sha256": manifest_sha256,
                    "manifest_storage_uri": stored.uri,
                    **dict(summary),
                    "archive_membership_rewrite_count": 0,
                    "historical_evaluations_mutated": False,
                    "historical_checkpoints_mutated": False,
                },
                idempotency_key=run_key,
            )
            persisted_run_events += 1

    operational_ids: dict[str, dict[str, str]] = {}
    for target in TARGET_KEYS:
        target_summary = dict(manifest["target_summary"][target])
        output = {
            "manifest_sha256": manifest_sha256,
            "manifest_storage_uri": stored.uri,
            "manifest_size_bytes": stored.size_bytes,
            "target_summary": target_summary,
            "archive_membership_rewrite_count": 0,
        }
        async with session_factory() as session, session.begin():
            run, call = await persist_operational_call(
                session,
                _operational_record(
                    target_key=target,
                    manifest_sha256=manifest_sha256,
                    manifest=manifest,
                    status="succeeded",
                    output=output,
                ),
            )
            artifact = await _get_or_create_artifact(session, stored)
            edge = await session.get(
                EvidenceArtifact,
                (call.id, artifact.id, "eligibility_manifest"),
            )
            if edge is None:
                session.add(
                    EvidenceArtifact(
                        tool_call_id=call.id,
                        artifact_id=artifact.id,
                        role="eligibility_manifest",
                    )
                )
                await session.flush()
            witness = sha256_json(
                {
                    "tool_call_id": str(call.id),
                    "artifact_id": str(artifact.id),
                    "role": "eligibility_manifest",
                    "requested_storage_uri": stored.uri,
                }
            )
            location = await session.get(
                EvidenceArtifactLocation,
                (call.id, artifact.id, "eligibility_manifest", witness),
            )
            if location is None:
                session.add(
                    EvidenceArtifactLocation(
                        tool_call_id=call.id,
                        artifact_id=artifact.id,
                        role="eligibility_manifest",
                        location_witness_sha256=witness,
                        requested_storage_uri=stored.uri,
                        location_metadata_json={
                            "schema_version": RESTORATION_SCHEMA,
                            "target_key": target,
                            "snapshot_cutoff": manifest["snapshot_cutoff"],
                        },
                    )
                )
            operational_ids[target] = {
                "operational_run_id": str(run.id),
                "tool_call_id": str(call.id),
            }

    return {
        "schema_version": RESTORATION_SCHEMA,
        "status": "succeeded",
        "manifest_sha256": manifest_sha256,
        "manifest_storage_uri": stored.uri,
        "manifest_size_bytes": stored.size_bytes,
        "candidate_event_count": persisted_candidate_events,
        "run_summary_event_count": persisted_run_events,
        "archive_membership_rewrite_count": 0,
        "operational_ids": operational_ids,
    }


__all__ = [
    "RESTORATION_POLICY",
    "RESTORATION_SCHEMA",
    "build_restoration_manifest",
    "canonical_manifest_bytes",
    "persist_restoration",
    "successor_restored_candidate_ids",
    "validate_restoration_manifest",
]
