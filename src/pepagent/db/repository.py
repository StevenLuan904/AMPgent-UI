from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import (
    AgentDecision,
    AgentDecisionToolCallEdge,
    Artifact,
    AutoResearchAction,
    AutoResearchArchiveMembership,
    AutoResearchArchiveVersion,
    AutoResearchCheckpoint,
    AutoResearchMetricDelta,
    Candidate,
    CandidateLineageEdge,
    CandidateOccurrence,
    Evaluation,
    ExperimentRun,
    LifecycleEvent,
    RunStageCheckpoint,
    Target,
    ToolCall,
    ToolCallDependency,
)
from pepagent.domain.enums import CandidateStatus, EvaluationStatus, RunStatus
from pepagent.domain.schemas import ExperimentSpec
from pepagent.provenance.hashing import sha256_json, sha256_text

_AUTORESEARCH_ACTION_KINDS = frozenset({"point_edit", "controlled_mix", "de_novo"})
_AUTORESEARCH_SOURCE_ROLES = frozenset({"primary_parent", "donor", "backbone", "target_module"})
_AUTORESEARCH_DELTA_DIRECTIONS = frozenset({"minimize", "maximize", "audit", "categorical"})


def _require_sha256(value: str, field_name: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64:
        raise ValueError(f"{field_name} must be a 64-character SHA-256 digest")
    try:
        int(normalized, 16)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be hexadecimal") from exc
    return normalized


def _normalize_string_list(values: list[str], field_name: str) -> list[str]:
    normalized = [str(value).strip() for value in values]
    if not normalized or any(not value for value in normalized):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return normalized


def _validate_autoresearch_action_contract(
    *,
    iteration_no: int,
    branch_key: str,
    action_ordinal: int,
    action_kind: str,
    rationale_text: str,
    expected_objectives: list[str],
    forbidden_changes: list[str],
    action_spec: dict[str, Any],
) -> tuple[str, str, list[str], list[str], dict[str, Any]]:
    if iteration_no < 0:
        raise ValueError("AutoResearch iteration must be non-negative")
    if action_ordinal < 1:
        raise ValueError("AutoResearch action ordinal must be positive")
    normalized_branch = branch_key.strip()
    normalized_kind = action_kind.strip()
    normalized_rationale = rationale_text.strip()
    if not normalized_branch or not normalized_rationale:
        raise ValueError("AutoResearch branch and rationale must be non-empty")
    if normalized_kind not in _AUTORESEARCH_ACTION_KINDS:
        raise ValueError(f"unsupported AutoResearch action kind: {normalized_kind}")
    normalized_objectives = _normalize_string_list(expected_objectives, "expected objectives")
    normalized_forbidden = _normalize_string_list(forbidden_changes, "forbidden changes")
    normalized_spec = dict(action_spec)
    operations = normalized_spec.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ValueError("AutoResearch action spec must contain executable operations")
    if not all(isinstance(operation, dict) and operation for operation in operations):
        raise ValueError("each AutoResearch operation must be a non-empty object")
    sources = normalized_spec.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("AutoResearch action sources must be a list")
    if normalized_kind == "de_novo" and sources:
        raise ValueError("de_novo AutoResearch actions cannot declare source candidates")
    if normalized_kind != "de_novo" and not sources:
        raise ValueError("non-de-novo AutoResearch actions require source candidates")
    normalized_action_sources: list[dict[str, Any]] = []
    if normalized_kind != "de_novo":
        lineage_sources: list[dict[str, Any]] = []
        for index, source in enumerate(sources, start=1):
            if not isinstance(source, dict):
                raise ValueError("AutoResearch action sources must be typed objects")
            candidate_id = uuid.UUID(str(source["candidate_id"]))
            lineage_sources.append(
                {
                    "parent_candidate_id": candidate_id,
                    "relation_role": source["relation_role"],
                    "source_ordinal": source.get("source_ordinal", index),
                    "source_spans": source.get("source_spans") or [],
                    "metadata": source.get("metadata") or {},
                }
            )
        for source in _normalize_autoresearch_lineage_sources(normalized_kind, lineage_sources):
            normalized_action_sources.append(
                {
                    "candidate_id": str(source["parent_candidate_id"]),
                    "relation_role": source["relation_role"],
                    "source_ordinal": source["source_ordinal"],
                    "source_spans": source["source_spans"],
                    "metadata": source["metadata"],
                }
            )
    normalized_spec["sources"] = normalized_action_sources
    return (
        normalized_branch,
        normalized_kind,
        normalized_objectives,
        normalized_forbidden,
        normalized_spec,
    )


def _normalize_autoresearch_lineage_sources(
    action_kind: str, sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if action_kind == "de_novo":
        if sources:
            raise ValueError("de_novo lineage cannot declare parents or donors")
        return [
            {
                "parent_candidate_id": None,
                "relation_role": "de_novo_origin",
                "source_ordinal": 1,
                "source_spans": [],
                "metadata": {},
            }
        ]
    if not sources:
        raise ValueError("evolution lineage must contain at least one source")
    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(sources, start=1):
        parent_id = uuid.UUID(str(source["parent_candidate_id"]))
        role = str(source["relation_role"]).strip()
        ordinal = int(source.get("source_ordinal", index))
        spans = source.get("source_spans") or []
        metadata = source.get("metadata") or {}
        if role not in _AUTORESEARCH_SOURCE_ROLES:
            raise ValueError(f"unsupported lineage source role: {role}")
        if ordinal != index:
            raise ValueError("lineage source ordinals must be contiguous and ordered")
        if not isinstance(spans, list) or not all(isinstance(span, dict) for span in spans):
            raise ValueError("lineage source spans must be a list of objects")
        if not isinstance(metadata, dict):
            raise ValueError("lineage metadata must be an object")
        normalized.append(
            {
                "parent_candidate_id": parent_id,
                "relation_role": role,
                "source_ordinal": ordinal,
                "source_spans": spans,
                "metadata": metadata,
            }
        )
    parent_ids = [source["parent_candidate_id"] for source in normalized]
    if len(parent_ids) != len(set(parent_ids)):
        raise ValueError("lineage source candidates must be unique")
    anchor_count = sum(
        source["relation_role"] in {"primary_parent", "backbone"} for source in normalized
    )
    if anchor_count != 1:
        raise ValueError("lineage requires exactly one primary parent or backbone")
    if action_kind == "point_edit" and len(normalized) != 1:
        raise ValueError("point_edit lineage requires exactly one parent")
    if action_kind == "controlled_mix":
        if len(normalized) < 2:
            raise ValueError("controlled_mix lineage requires at least two sources")
        if not any(source["relation_role"] in {"donor", "target_module"} for source in normalized):
            raise ValueError("controlled_mix lineage requires a donor or target module")
    return normalized


def _build_metric_comparison(
    parent: Evaluation,
    child: Evaluation,
    direction: str,
) -> tuple[str, str, float | None, bool | None, dict[str, Any]]:
    if direction not in _AUTORESEARCH_DELTA_DIRECTIONS:
        raise ValueError(f"unsupported metric direction: {direction}")
    if parent.status != EvaluationStatus.SUCCEEDED or child.status != EvaluationStatus.SUCCEEDED:
        raise ValueError("metric deltas require two succeeded evaluations")
    if parent.metric_name != child.metric_name or parent.unit != child.unit:
        raise ValueError("metric delta evaluations must share metric name and unit")
    numeric = parent.numeric_value is not None and child.numeric_value is not None
    categorical = parent.text_value is not None and child.text_value is not None
    if numeric == categorical:
        raise ValueError("metric delta requires exactly one shared value representation")
    if numeric:
        if direction == "categorical":
            raise ValueError("numeric metric delta cannot use categorical direction")
        delta = float(child.numeric_value) - float(parent.numeric_value)
        improved = None
        if direction == "maximize":
            improved = delta > 0
        elif direction == "minimize":
            improved = delta < 0
        kind = "numeric_delta"
    else:
        if direction != "categorical":
            raise ValueError("categorical metric transition requires categorical direction")
        delta = None
        improved = None
        kind = "categorical_transition"
    comparison = {
        "parent": {
            "numeric_value": parent.numeric_value,
            "text_value": parent.text_value,
            "out_of_domain": parent.out_of_domain,
        },
        "child": {
            "numeric_value": child.numeric_value,
            "text_value": child.text_value,
            "out_of_domain": child.out_of_domain,
        },
        "unit": child.unit,
    }
    return kind, direction, delta, improved, comparison


def _derive_archive_membership_changes(
    previous_active: set[uuid.UUID], current_active: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    return {
        **{candidate_id: "remove" for candidate_id in previous_active - current_active},
        **{candidate_id: "retain" for candidate_id in previous_active & current_active},
        **{candidate_id: "add" for candidate_id in current_active - previous_active},
    }


def _validate_score_all_counts(
    candidate_count: int,
    required_metric_count: int,
    completed_evaluation_count: int,
) -> int:
    if candidate_count < 1 or required_metric_count < 1:
        raise ValueError("score-all checkpoint counts must be positive")
    expected = candidate_count * required_metric_count
    if completed_evaluation_count != expected:
        raise ValueError(
            "score-all checkpoint cannot close before every candidate has every required metric"
        )
    return expected


def _validate_occurrence_parent_semantics(
    occurrence_kind: str, parent_candidate_id: uuid.UUID | None
) -> None:
    if parent_candidate_id is None and occurrence_kind != "de_novo":
        raise ValueError("parentless candidate occurrence must have de_novo kind")
    if parent_candidate_id is not None and occurrence_kind == "de_novo":
        raise ValueError("de_novo candidate occurrence cannot declare a parent")


def _validate_occurrence_run_semantics(
    run_id: uuid.UUID,
    *,
    parent: Candidate | None,
    candidate: Candidate | None,
) -> None:
    if parent is not None and parent.run_id != run_id:
        raise ValueError("candidate occurrence parent is cross-run")
    if candidate is not None and candidate.run_id != run_id:
        raise ValueError("candidate occurrence materialization is cross-run")


def _candidate_occurrence_identity_matches(
    existing: CandidateOccurrence, identity: dict[str, Any]
) -> bool:
    return all(getattr(existing, key) == value for key, value in identity.items())


def _lifecycle_event_lock_id(aggregate_type: str, aggregate_id: uuid.UUID) -> int:
    """Return a stable signed PostgreSQL advisory-lock key for one aggregate."""
    digest = bytes.fromhex(
        sha256_json(
            {
                "lock_domain": "pepagent.lifecycle_event_sequence.v1",
                "aggregate_type": aggregate_type,
                "aggregate_id": str(aggregate_id),
            }
        )
    )
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class ExperimentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_run(
        self,
        spec: ExperimentSpec,
        actor: str = "api",
        parent_run_id: uuid.UUID | None = None,
        raw_spec_payload: dict[str, Any] | None = None,
    ) -> ExperimentRun:
        target_digest = sha256_text(spec.target.sequence)
        target = await self.session.scalar(
            select(Target).where(Target.sequence_sha256 == target_digest)
        )
        if target is None:
            target = Target(
                name=spec.target.name,
                organism=spec.target.organism,
                accession=spec.target.accession,
                sequence=spec.target.sequence,
                sequence_sha256=target_digest,
                metadata_json={
                    "pocket_residues": spec.target.pocket_residues,
                    "source_database": spec.target.source_database,
                    "source_uri": spec.target.source_uri,
                    "source_version": spec.target.source_version,
                    "source_retrieved_at": (
                        spec.target.source_retrieved_at.isoformat()
                        if spec.target.source_retrieved_at
                        else None
                    ),
                },
            )
            self.session.add(target)
            await self.session.flush()

        payload = raw_spec_payload if raw_spec_payload is not None else spec.model_dump(mode="json")
        run = ExperimentRun(
            target_id=target.id,
            spec_json=payload,
            spec_sha256=sha256_json(payload),
            status=RunStatus.CREATED,
            parent_run_id=parent_run_id,
        )
        self.session.add(run)
        await self.session.flush()
        await self.append_event("run", run.id, "run.created", actor, payload)
        return run

    async def append_event(
        self,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> LifecycleEvent:
        # Sequence allocation is a read-modify-write operation.  Concurrent
        # workers may append to the same run aggregate, so serialize only this
        # short aggregate-local critical section for the current transaction.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _lifecycle_event_lock_id(aggregate_type, aggregate_id)},
        )
        payload_sha256 = sha256_json(payload)
        if idempotency_key is not None:
            if not idempotency_key or payload.get("event_idempotency_key") != idempotency_key:
                raise ValueError("lifecycle event idempotency key is missing from its payload")
            existing = await self.session.scalar(
                select(LifecycleEvent)
                .where(
                    LifecycleEvent.aggregate_type == aggregate_type,
                    LifecycleEvent.aggregate_id == aggregate_id,
                    LifecycleEvent.event_type == event_type,
                    LifecycleEvent.payload_json["event_idempotency_key"].as_string()
                    == idempotency_key,
                )
                .limit(1)
            )
            if existing is not None:
                if existing.actor != actor or existing.payload_sha256 != payload_sha256:
                    raise ValueError("lifecycle event idempotency identity drifted")
                return existing
        last = await self.session.scalar(
            select(func.max(LifecycleEvent.sequence_no)).where(
                LifecycleEvent.aggregate_type == aggregate_type,
                LifecycleEvent.aggregate_id == aggregate_id,
            )
        )
        event = LifecycleEvent(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            sequence_no=(last or 0) + 1,
            event_type=event_type,
            actor=actor,
            payload_json=payload,
            payload_sha256=payload_sha256,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def mark_run_started(
        self, run_id: uuid.UUID, workflow_id: str, temporal_run_id: str | None
    ) -> None:
        run = await self.session.get(ExperimentRun, run_id, with_for_update=True)
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        if run.temporal_workflow_id == workflow_id and run.status in {
            RunStatus.RUNNING,
            RunStatus.SUCCEEDED,
        }:
            if run.status == RunStatus.RUNNING and run.started_at is None:
                # Formal reservation may bind the workflow and mark it running
                # before Temporal's mark_run_started activity executes.  Repair
                # that partial state under the existing row lock, while keeping
                # the lifecycle event exactly once.
                run.started_at = datetime.now(UTC)
                if run.temporal_run_id is None:
                    run.temporal_run_id = temporal_run_id
                existing_started_event = await self.session.scalar(
                    select(LifecycleEvent.id)
                    .where(
                        LifecycleEvent.aggregate_type == "run",
                        LifecycleEvent.aggregate_id == run.id,
                        LifecycleEvent.event_type == "run.started",
                    )
                    .limit(1)
                )
                if existing_started_event is None:
                    await self.append_event(
                        "run",
                        run.id,
                        "run.started",
                        "temporal",
                        {"workflow_id": workflow_id},
                    )
            return
        run.status = RunStatus.RUNNING
        run.temporal_workflow_id = workflow_id
        run.temporal_run_id = temporal_run_id
        run.started_at = datetime.now(UTC)
        await self.append_event(
            "run", run.id, "run.started", "temporal", {"workflow_id": workflow_id}
        )

    async def add_candidate(
        self,
        run_id: uuid.UUID,
        sequence: str,
        generation: int,
        proposal_rank: int,
        generator_call_id: uuid.UUID | None = None,
        parent_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
        actor: str = "pepmlm",
    ) -> Candidate:
        normalized = "".join(sequence.split()).upper()
        digest = sha256_text(normalized)
        existing = await self.session.scalar(
            select(Candidate).where(Candidate.run_id == run_id, Candidate.sequence_sha256 == digest)
        )
        if existing is not None:
            if existing.generator_call_id is None and generator_call_id is not None:
                existing.generator_call_id = generator_call_id
            return existing
        candidate = Candidate(
            run_id=run_id,
            sequence=normalized,
            sequence_sha256=digest,
            generation=generation,
            parent_id=parent_id,
            status=CandidateStatus.GENERATED,
            proposal_rank=proposal_rank,
            generator_call_id=generator_call_id,
            metadata_json=metadata or {},
        )
        self.session.add(candidate)
        await self.session.flush()
        await self.append_event(
            "candidate",
            candidate.id,
            "candidate.generated",
            actor,
            {"run_id": str(run_id), "sequence_sha256": digest, "generation": generation},
        )
        return candidate

    async def transition_candidate(
        self,
        candidate_id: uuid.UUID,
        new_status: CandidateStatus,
        actor: str,
        reason: str,
    ) -> None:
        candidate = await self.session.get(Candidate, candidate_id, with_for_update=True)
        if candidate is None:
            raise KeyError(f"candidate not found: {candidate_id}")
        old_status = candidate.status
        if old_status == new_status:
            return
        candidate.status = new_status
        await self.append_event(
            "candidate",
            candidate.id,
            "candidate.status_changed",
            actor,
            {"from": old_status, "to": new_status, "reason": reason},
        )

    async def record_candidate_occurrence(
        self,
        *,
        run_id: uuid.UUID,
        tool_call_id: uuid.UUID,
        parent_candidate_id: uuid.UUID | None,
        occurrence_rank: int,
        occurrence_kind: str,
        opaque_arm_label: str,
        sequence: str,
        candidate_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CandidateOccurrence:
        """Record one proposal exactly; a retry with changed payload fails closed."""
        if occurrence_rank < 1:
            raise ValueError("candidate occurrence rank must be positive")
        normalized_metadata = metadata or {}
        _validate_occurrence_parent_semantics(occurrence_kind, parent_candidate_id)
        call = await self.session.get(ToolCall, tool_call_id)
        parent = (
            await self.session.get(Candidate, parent_candidate_id)
            if parent_candidate_id is not None
            else None
        )
        candidate = await self.session.get(Candidate, candidate_id) if candidate_id else None
        if call is None or call.run_id != run_id:
            raise ValueError("candidate occurrence tool call is missing or cross-run")
        if parent_candidate_id is not None and parent is None:
            raise ValueError("candidate occurrence parent does not exist")
        if candidate_id is not None and candidate is None:
            raise ValueError("candidate occurrence materialization is missing or cross-run")
        _validate_occurrence_run_semantics(
            run_id,
            parent=parent,
            candidate=candidate,
        )
        normalized = "".join(sequence.split()).upper()
        digest = sha256_text(normalized)
        if candidate is not None and candidate.sequence_sha256 != digest:
            raise ValueError("candidate occurrence and materialized candidate differ")
        identity = {
            "run_id": run_id,
            "tool_call_id": tool_call_id,
            "candidate_id": candidate_id,
            "parent_candidate_id": parent_candidate_id,
            "occurrence_rank": occurrence_rank,
            "occurrence_kind": occurrence_kind,
            "opaque_arm_label": opaque_arm_label,
            "sequence": normalized,
            "sequence_sha256": digest,
            "metadata_json": normalized_metadata,
        }
        existing = await self.session.scalar(
            select(CandidateOccurrence).where(
                CandidateOccurrence.tool_call_id == tool_call_id,
                CandidateOccurrence.occurrence_rank == occurrence_rank,
            )
        )
        if existing is not None:
            if not _candidate_occurrence_identity_matches(existing, identity):
                raise ValueError("candidate occurrence retry payload drifted")
            return existing
        occurrence = CandidateOccurrence(**identity)
        self.session.add(occurrence)
        await self.session.flush()
        return occurrence

    async def record_completed_tool_call(
        self,
        run_id: uuid.UUID,
        tool_name: str,
        tool_version: str,
        environment_sha256: str,
        input_payload: dict[str, Any],
        parameters: dict[str, Any],
        output_payload: dict[str, Any],
        weights_sha256: str | None = None,
        model_uri: str | None = None,
        random_seed: int | None = None,
        attempt: int = 1,
        logical_stage: str | None = None,
        display_category: str | None = None,
    ) -> ToolCall:
        if (logical_stage is None) != (display_category is None):
            raise ValueError("tool-call observer stage and category must be supplied together")
        input_sha256 = sha256_json(input_payload)
        output_sha256 = sha256_json(output_payload)
        idempotency_key = sha256_json(
            {
                "run_id": str(run_id),
                "tool_name": tool_name,
                "tool_version": tool_version,
                "environment_sha256": environment_sha256,
                "weights_sha256": weights_sha256,
                "input_sha256": input_sha256,
                "parameters": parameters,
                "random_seed": random_seed,
            }
        )
        existing = await self.session.scalar(
            select(ToolCall).where(ToolCall.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        call = ToolCall(
            run_id=run_id,
            tool_name=tool_name,
            tool_version=tool_version,
            model_uri=model_uri,
            weights_sha256=weights_sha256,
            environment_sha256=environment_sha256,
            idempotency_key=idempotency_key,
            input_sha256=input_sha256,
            input_json=input_payload,
            parameters_json=parameters,
            random_seed=random_seed,
            status=EvaluationStatus.SUCCEEDED,
            attempt=attempt,
            queued_at=now,
            started_at=now,
            finished_at=now,
            output_sha256=output_sha256,
        )
        self.session.add(call)
        await self.session.flush()
        await self.append_event(
            "run",
            run_id,
            "tool_call.succeeded",
            tool_name,
            {
                "tool_call_id": str(call.id),
                "idempotency_key": idempotency_key,
                "input_sha256": input_sha256,
                "output_sha256": output_sha256,
                **(
                    {
                        "observer_schema_version": "v38.tool-call-display.1",
                        "logical_stage": logical_stage,
                        "display_category": display_category,
                    }
                    if logical_stage is not None
                    else {}
                ),
            },
        )
        return call

    async def record_evaluation(
        self,
        candidate_id: uuid.UUID,
        tool_call_id: uuid.UUID,
        metric_name: str,
        numeric_value: float | None,
        unit: str | None,
        raw: dict[str, Any],
        *,
        text_value: str | None = None,
        out_of_domain: bool = False,
        limitations: list[str] | None = None,
    ) -> Evaluation:
        existing = await self.session.scalar(
            select(Evaluation).where(
                Evaluation.candidate_id == candidate_id,
                Evaluation.metric_name == metric_name,
                Evaluation.tool_call_id == tool_call_id,
            )
        )
        if existing is not None:
            return existing
        evaluation = Evaluation(
            candidate_id=candidate_id,
            tool_call_id=tool_call_id,
            metric_name=metric_name,
            numeric_value=numeric_value,
            text_value=text_value,
            unit=unit,
            status=EvaluationStatus.SUCCEEDED,
            out_of_domain=out_of_domain,
            limitations_json=limitations or [],
            raw_json=raw,
        )
        self.session.add(evaluation)
        await self.session.flush()
        await self.append_event(
            "candidate",
            candidate_id,
            "evaluation.recorded",
            "metric-worker",
            {
                "evaluation_id": str(evaluation.id),
                "metric_name": metric_name,
                "tool_call_id": str(tool_call_id),
            },
        )
        return evaluation

    async def record_evaluations_bulk(
        self,
        tool_call_id: uuid.UUID,
        rows: list[dict[str, Any]],
    ) -> list[Evaluation]:
        """Persist a score-all metric batch with one read and one flush.

        The scalar helper above remains appropriate for interactive/small writes and
        retains its candidate-level lifecycle event.  Formal score-all activities
        can contain thousands of candidates; issuing a select, flush, and lifecycle
        allocation per row made otherwise finished metric activities serialize for
        minutes.  Their durable lifecycle is already represented by the typed
        tool-call event and the run-level ``v38.sequence_metric.persisted`` event, so
        this method keeps the exact Evaluation evidence while avoiding redundant
        per-candidate event traffic.
        """
        if not rows:
            return []
        identities = [
            (
                uuid.UUID(str(row["candidate_id"])),
                str(row["metric_name"]),
                tool_call_id,
            )
            for row in rows
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("bulk evaluation rows contain duplicate evidence identities")
        candidate_ids = {identity[0] for identity in identities}
        metric_names = {identity[1] for identity in identities}
        existing_rows = list(
            await self.session.scalars(
                select(Evaluation).where(
                    Evaluation.candidate_id.in_(candidate_ids),
                    Evaluation.metric_name.in_(metric_names),
                    Evaluation.tool_call_id == tool_call_id,
                )
            )
        )
        existing = {
            (item.candidate_id, item.metric_name, item.tool_call_id): item for item in existing_rows
        }
        created: dict[tuple[uuid.UUID, str, uuid.UUID], Evaluation] = {}
        for row, identity in zip(rows, identities, strict=True):
            if identity in existing:
                continue
            evaluation = Evaluation(
                candidate_id=identity[0],
                tool_call_id=tool_call_id,
                metric_name=identity[1],
                numeric_value=row.get("numeric_value"),
                text_value=row.get("text_value"),
                unit=row.get("unit"),
                status=EvaluationStatus.SUCCEEDED,
                out_of_domain=bool(row.get("out_of_domain", False)),
                limitations_json=row.get("limitations") or [],
                raw_json=row.get("raw") or {},
            )
            self.session.add(evaluation)
            created[identity] = evaluation
        if created:
            await self.session.flush()
        return [existing.get(identity) or created[identity] for identity in identities]

    async def record_tool_dependency(
        self,
        child_tool_call_id: uuid.UUID,
        parent_tool_call_id: uuid.UUID,
        relation_type: str,
    ) -> ToolCallDependency:
        if child_tool_call_id == parent_tool_call_id:
            raise ValueError("a tool call cannot depend on itself")
        child = await self.session.get(ToolCall, child_tool_call_id)
        parent = await self.session.get(ToolCall, parent_tool_call_id)
        if child is None or parent is None:
            raise KeyError("both child and parent tool calls must exist")
        if child.run_id != parent.run_id:
            raise ValueError("tool-call dependency edges cannot cross experiment runs")
        key = {
            "child_tool_call_id": child_tool_call_id,
            "parent_tool_call_id": parent_tool_call_id,
            "relation_type": relation_type,
        }
        existing = await self.session.get(ToolCallDependency, key)
        if existing is not None:
            return existing
        dependency = ToolCallDependency(**key)
        self.session.add(dependency)
        await self.session.flush()
        return dependency

    async def record_agent_decision(
        self,
        run_id: uuid.UUID,
        generation: int,
        decision_type: str,
        agent_name: str,
        agent_version: str,
        prompt_text: str,
        response_text: str,
        structured: dict[str, Any],
        *,
        model_name: str | None = None,
        prompt_artifact_id: uuid.UUID | None = None,
        response_artifact_id: uuid.UUID | None = None,
    ) -> AgentDecision:
        decision = AgentDecision(
            run_id=run_id,
            generation=generation,
            decision_type=decision_type,
            agent_name=agent_name,
            agent_version=agent_version,
            model_name=model_name,
            prompt_text=prompt_text,
            response_text=response_text,
            prompt_sha256=sha256_text(prompt_text),
            response_sha256=sha256_text(response_text),
            structured_json=structured,
            status="succeeded",
            prompt_artifact_id=prompt_artifact_id,
            response_artifact_id=response_artifact_id,
        )
        self.session.add(decision)
        await self.session.flush()
        await self.append_event(
            "run",
            run_id,
            "agent_decision.recorded",
            agent_name,
            {
                "decision_id": str(decision.id),
                "generation": generation,
                "decision_type": decision_type,
                "prompt_sha256": decision.prompt_sha256,
                "response_sha256": decision.response_sha256,
            },
        )
        return decision

    async def record_agent_tool_edge(
        self,
        decision_id: uuid.UUID,
        tool_call_id: uuid.UUID,
        direction: str,
        relation_type: str,
    ) -> AgentDecisionToolCallEdge:
        if direction not in {"input", "output"}:
            raise ValueError("Agent edge direction must be input or output")
        decision = await self.session.get(AgentDecision, decision_id)
        tool_call = await self.session.get(ToolCall, tool_call_id)
        if decision is None or tool_call is None:
            raise KeyError("Agent decision and tool call must both exist")
        if decision.run_id != tool_call.run_id:
            raise ValueError("Agent decision edges cannot cross experiment runs")
        key = {
            "decision_id": decision_id,
            "tool_call_id": tool_call_id,
            "direction": direction,
            "relation_type": relation_type,
        }
        existing = await self.session.get(AgentDecisionToolCallEdge, key)
        if existing is not None:
            return existing
        edge = AgentDecisionToolCallEdge(**key)
        self.session.add(edge)
        await self.session.flush()
        return edge

    async def _run_ancestor_ids(self, run_id: uuid.UUID) -> set[uuid.UUID]:
        """Return the current run and its immutable predecessor chain."""
        ancestors: set[uuid.UUID] = set()
        current_id: uuid.UUID | None = run_id
        while current_id is not None:
            if current_id in ancestors:
                raise ValueError("experiment run lineage contains a cycle")
            run = await self.session.get(ExperimentRun, current_id)
            if run is None:
                raise KeyError(f"run not found in lineage: {current_id}")
            ancestors.add(current_id)
            current_id = run.parent_run_id
        return ancestors

    async def _candidate_ancestor_ids(self, candidate_id: uuid.UUID) -> set[uuid.UUID]:
        """Traverse legacy and typed parent edges to prevent lineage cycles."""
        ancestors: set[uuid.UUID] = set()
        frontier = [candidate_id]
        while frontier:
            current_id = frontier.pop()
            candidate = await self.session.get(Candidate, current_id)
            if candidate is None:
                raise KeyError(f"candidate not found in lineage: {current_id}")
            direct_parents = list(
                await self.session.scalars(
                    select(CandidateLineageEdge.parent_candidate_id).where(
                        CandidateLineageEdge.child_candidate_id == current_id,
                        CandidateLineageEdge.parent_candidate_id.is_not(None),
                    )
                )
            )
            if candidate.parent_id is not None:
                direct_parents.append(candidate.parent_id)
            for parent_id in direct_parents:
                if parent_id is None or parent_id in ancestors:
                    continue
                ancestors.add(parent_id)
                frontier.append(parent_id)
        return ancestors

    async def record_autoresearch_action(
        self,
        *,
        run_id: uuid.UUID,
        iteration_no: int,
        branch_key: str,
        action_ordinal: int,
        action_kind: str,
        random_seed: int,
        agent_decision_id: uuid.UUID,
        rationale_text: str,
        expected_objectives: list[str],
        forbidden_changes: list[str],
        action_spec: dict[str, Any],
        actor: str = "autoresearch-controller",
    ) -> AutoResearchAction:
        """Persist one executable Agent action; slot retries must be byte-identical."""
        (
            normalized_branch,
            normalized_kind,
            normalized_objectives,
            normalized_forbidden,
            normalized_spec,
        ) = _validate_autoresearch_action_contract(
            iteration_no=iteration_no,
            branch_key=branch_key,
            action_ordinal=action_ordinal,
            action_kind=action_kind,
            rationale_text=rationale_text,
            expected_objectives=expected_objectives,
            forbidden_changes=forbidden_changes,
            action_spec=action_spec,
        )
        run = await self.session.get(ExperimentRun, run_id)
        decision = await self.session.get(AgentDecision, agent_decision_id)
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        if decision is None or decision.run_id != run_id:
            raise ValueError("AutoResearch action decision is missing or cross-run")
        if decision.status != "succeeded" or decision.generation != iteration_no:
            raise ValueError(
                "AutoResearch action requires the succeeded decision for its iteration"
            )
        permitted_runs = await self._run_ancestor_ids(run_id)
        for source in normalized_spec["sources"]:
            source_candidate_id = uuid.UUID(source["candidate_id"])
            source_candidate = await self.session.get(Candidate, source_candidate_id)
            if source_candidate is None or source_candidate.run_id not in permitted_runs:
                raise ValueError(
                    "AutoResearch action source is missing or outside the run ancestry"
                )
        payload = {
            "schema_version": "autoresearch.action.v1",
            "run_id": str(run_id),
            "iteration_no": iteration_no,
            "branch_key": normalized_branch,
            "action_ordinal": action_ordinal,
            "action_kind": normalized_kind,
            "random_seed": random_seed,
            "agent_decision_id": str(agent_decision_id),
            "rationale_text": rationale_text.strip(),
            "expected_objectives": normalized_objectives,
            "forbidden_changes": normalized_forbidden,
            "action_spec": normalized_spec,
        }
        action_sha256 = sha256_json(payload)
        identity = {
            "run_id": run_id,
            "iteration_no": iteration_no,
            "branch_key": normalized_branch,
            "action_ordinal": action_ordinal,
            "action_kind": normalized_kind,
            "random_seed": random_seed,
            "agent_decision_id": agent_decision_id,
            "rationale_text": rationale_text.strip(),
            "expected_objectives_json": normalized_objectives,
            "forbidden_changes_json": normalized_forbidden,
            "action_spec_json": normalized_spec,
            "action_sha256": action_sha256,
        }
        existing = await self.session.scalar(
            select(AutoResearchAction).where(
                AutoResearchAction.run_id == run_id,
                AutoResearchAction.iteration_no == iteration_no,
                AutoResearchAction.branch_key == normalized_branch,
                AutoResearchAction.action_ordinal == action_ordinal,
            )
        )
        if existing is not None:
            if not all(getattr(existing, key) == value for key, value in identity.items()):
                raise ValueError("AutoResearch action retry payload drifted")
            return existing
        action = AutoResearchAction(**identity)
        self.session.add(action)
        await self.session.flush()
        await self.append_event(
            "run",
            run_id,
            "autoresearch.action.recorded",
            actor,
            {"action_id": str(action.id), "action_sha256": action_sha256},
        )
        return action

    async def record_candidate_lineage(
        self,
        *,
        action_id: uuid.UUID,
        child_candidate_id: uuid.UUID,
        sources: list[dict[str, Any]],
        actor: str = "autoresearch-controller",
    ) -> list[CandidateLineageEdge]:
        """Persist the complete source set for a materialized child exactly once."""
        action = await self.session.get(AutoResearchAction, action_id)
        child = await self.session.get(Candidate, child_candidate_id)
        if action is None or child is None:
            raise KeyError("AutoResearch action and child candidate must exist")
        if child.run_id != action.run_id or child.generation != action.iteration_no + 1:
            raise ValueError("lineage child must be in the action run and next generation")
        normalized_sources = _normalize_autoresearch_lineage_sources(action.action_kind, sources)
        declared_sources = {
            (
                uuid.UUID(source["candidate_id"]),
                source["relation_role"],
                source["source_ordinal"],
                sha256_json(source.get("source_spans") or []),
                sha256_json(source.get("metadata") or {}),
            )
            for source in action.action_spec_json.get("sources", [])
        }
        requested_sources = {
            (
                source["parent_candidate_id"],
                source["relation_role"],
                source["source_ordinal"],
                sha256_json(source["source_spans"]),
                sha256_json(source["metadata"]),
            )
            for source in normalized_sources
            if source["parent_candidate_id"] is not None
        }
        if action.action_kind != "de_novo" and declared_sources != requested_sources:
            raise ValueError("materialized child lineage differs from the frozen action sources")
        conflicting_action = await self.session.scalar(
            select(CandidateLineageEdge).where(
                CandidateLineageEdge.child_candidate_id == child_candidate_id,
                CandidateLineageEdge.action_id != action_id,
            )
        )
        if conflicting_action is not None:
            raise ValueError("materialized child is already owned by another action")
        permitted_runs = await self._run_ancestor_ids(action.run_id)
        rows: list[dict[str, Any]] = []
        anchor_id: uuid.UUID | None = None
        for source in normalized_sources:
            parent_id = source["parent_candidate_id"]
            parent = await self.session.get(Candidate, parent_id) if parent_id else None
            if parent_id is not None:
                if parent is None or parent.run_id not in permitted_runs:
                    raise ValueError("lineage source is missing or outside the run ancestry")
                if parent_id == child_candidate_id:
                    raise ValueError("candidate lineage cannot be self-referential")
                if child_candidate_id in await self._candidate_ancestor_ids(parent_id):
                    raise ValueError("candidate lineage would introduce a cycle")
            if source["relation_role"] in {"primary_parent", "backbone"}:
                anchor_id = parent_id
            edge_payload = {
                "schema_version": "autoresearch.lineage-edge.v1",
                "action_sha256": action.action_sha256,
                "child_sequence_sha256": child.sequence_sha256,
                "parent_sequence_sha256": parent.sequence_sha256 if parent else None,
                "relation_role": source["relation_role"],
                "source_ordinal": source["source_ordinal"],
                "source_spans": source["source_spans"],
                "metadata": source["metadata"],
            }
            rows.append(
                {
                    "action_id": action_id,
                    "child_candidate_id": child_candidate_id,
                    "parent_candidate_id": parent_id,
                    "relation_role": source["relation_role"],
                    "source_ordinal": source["source_ordinal"],
                    "source_spans_json": source["source_spans"],
                    "edge_sha256": sha256_json(edge_payload),
                    "metadata_json": source["metadata"],
                }
            )
        if child.parent_id is not None and child.parent_id != anchor_id:
            raise ValueError("typed lineage anchor conflicts with legacy candidate parent")
        existing = list(
            await self.session.scalars(
                select(CandidateLineageEdge).where(
                    CandidateLineageEdge.action_id == action_id,
                    CandidateLineageEdge.child_candidate_id == child_candidate_id,
                )
            )
        )
        if existing:
            existing_hashes = {edge.edge_sha256 for edge in existing}
            requested_hashes = {row["edge_sha256"] for row in rows}
            if existing_hashes != requested_hashes or len(existing) != len(rows):
                raise ValueError("candidate lineage retry payload drifted or is partial")
            return sorted(existing, key=lambda edge: edge.source_ordinal)
        edges = [CandidateLineageEdge(**row) for row in rows]
        self.session.add_all(edges)
        await self.session.flush()
        await self.append_event(
            "candidate",
            child_candidate_id,
            "autoresearch.lineage.recorded",
            actor,
            {
                "action_id": str(action_id),
                "edge_sha256s": [edge.edge_sha256 for edge in edges],
            },
        )
        return edges

    async def record_autoresearch_metric_delta(
        self,
        *,
        action_id: uuid.UUID,
        child_candidate_id: uuid.UUID,
        comparator_candidate_id: uuid.UUID,
        metric_name: str,
        parent_evaluation_id: uuid.UUID,
        child_evaluation_id: uuid.UUID,
        direction: str,
        actor: str = "autoresearch-controller",
    ) -> AutoResearchMetricDelta:
        """Persist one comparable parent-child metric delta under one tool contract."""
        action = await self.session.get(AutoResearchAction, action_id)
        child = await self.session.get(Candidate, child_candidate_id)
        comparator = await self.session.get(Candidate, comparator_candidate_id)
        parent_evaluation = await self.session.get(Evaluation, parent_evaluation_id)
        child_evaluation = await self.session.get(Evaluation, child_evaluation_id)
        if any(
            item is None
            for item in (action, child, comparator, parent_evaluation, child_evaluation)
        ):
            raise KeyError("metric delta action, candidates, and evaluations must exist")
        assert action is not None
        assert child is not None
        assert comparator is not None
        assert parent_evaluation is not None
        assert child_evaluation is not None
        if child.run_id != action.run_id:
            raise ValueError("metric delta child is cross-run")
        if comparator.run_id not in await self._run_ancestor_ids(action.run_id):
            raise ValueError("metric delta comparator is outside the run ancestry")
        lineage = await self.session.scalar(
            select(CandidateLineageEdge).where(
                CandidateLineageEdge.action_id == action_id,
                CandidateLineageEdge.child_candidate_id == child_candidate_id,
                CandidateLineageEdge.parent_candidate_id == comparator_candidate_id,
            )
        )
        if lineage is None:
            raise ValueError("metric delta comparator is not a recorded lineage source")
        if (
            parent_evaluation.candidate_id != comparator_candidate_id
            or child_evaluation.candidate_id != child_candidate_id
            or parent_evaluation.metric_name != metric_name
            or child_evaluation.metric_name != metric_name
        ):
            raise ValueError("metric delta evaluation identities do not match candidates")
        parent_call = await self.session.get(ToolCall, parent_evaluation.tool_call_id)
        child_call = await self.session.get(ToolCall, child_evaluation.tool_call_id)
        if parent_call is None or child_call is None:
            raise KeyError("metric delta tool-call evidence is missing")
        tool_contract = (
            parent_call.tool_name,
            parent_call.tool_version,
            parent_call.model_uri,
            parent_call.weights_sha256,
            parent_call.environment_sha256,
        )
        if tool_contract != (
            child_call.tool_name,
            child_call.tool_version,
            child_call.model_uri,
            child_call.weights_sha256,
            child_call.environment_sha256,
        ):
            raise ValueError("parent-child metric deltas require the same frozen tool contract")
        kind, normalized_direction, numeric_delta, improved, comparison = _build_metric_comparison(
            parent_evaluation, child_evaluation, direction
        )
        comparison["tool_contract"] = {
            "tool_name": parent_call.tool_name,
            "tool_version": parent_call.tool_version,
            "model_uri": parent_call.model_uri,
            "weights_sha256": parent_call.weights_sha256,
            "environment_sha256": parent_call.environment_sha256,
        }
        payload = {
            "schema_version": "autoresearch.metric-delta.v1",
            "action_sha256": action.action_sha256,
            "child_sequence_sha256": child.sequence_sha256,
            "comparator_sequence_sha256": comparator.sequence_sha256,
            "metric_name": metric_name,
            "parent_evaluation_id": str(parent_evaluation_id),
            "child_evaluation_id": str(child_evaluation_id),
            "comparison_kind": kind,
            "direction": normalized_direction,
            "numeric_delta": numeric_delta,
            "improved": improved,
            "comparison": comparison,
        }
        identity = {
            "action_id": action_id,
            "child_candidate_id": child_candidate_id,
            "comparator_candidate_id": comparator_candidate_id,
            "metric_name": metric_name,
            "parent_evaluation_id": parent_evaluation_id,
            "child_evaluation_id": child_evaluation_id,
            "comparison_kind": kind,
            "direction": normalized_direction,
            "numeric_delta": numeric_delta,
            "improved": improved,
            "comparison_json": comparison,
            "delta_sha256": sha256_json(payload),
        }
        existing = await self.session.scalar(
            select(AutoResearchMetricDelta).where(
                AutoResearchMetricDelta.action_id == action_id,
                AutoResearchMetricDelta.child_candidate_id == child_candidate_id,
                AutoResearchMetricDelta.comparator_candidate_id == comparator_candidate_id,
                AutoResearchMetricDelta.metric_name == metric_name,
            )
        )
        if existing is not None:
            if not all(getattr(existing, key) == value for key, value in identity.items()):
                raise ValueError("AutoResearch metric delta retry payload drifted")
            return existing
        delta = AutoResearchMetricDelta(**identity)
        self.session.add(delta)
        await self.session.flush()
        await self.append_event(
            "candidate",
            child_candidate_id,
            "autoresearch.metric_delta.recorded",
            actor,
            {"delta_id": str(delta.id), "delta_sha256": delta.delta_sha256},
        )
        return delta

    async def record_autoresearch_archive_version(
        self,
        *,
        run_id: uuid.UUID,
        iteration_no: int,
        branch_key: str,
        archive_name: str,
        previous_version_id: uuid.UUID | None,
        policy_sha256: str,
        tool_call_id: uuid.UUID,
        snapshot_artifact_id: uuid.UUID,
        memberships: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        actor: str = "autoresearch-controller",
    ) -> AutoResearchArchiveVersion:
        """Append a full, replayable archive transition rather than only a winner list."""
        if iteration_no < 0:
            raise ValueError("archive iteration must be non-negative")
        normalized_branch = branch_key.strip()
        normalized_archive = archive_name.strip()
        normalized_policy_sha256 = _require_sha256(policy_sha256, "archive policy")
        if not normalized_branch or not normalized_archive:
            raise ValueError("archive branch and name must be non-empty")
        run = await self.session.get(ExperimentRun, run_id)
        call = await self.session.get(ToolCall, tool_call_id)
        artifact = await self.session.get(Artifact, snapshot_artifact_id)
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        if call is None or call.run_id != run_id or call.status != EvaluationStatus.SUCCEEDED:
            raise ValueError("archive tool call is missing, failed, or cross-run")
        if artifact is None:
            raise KeyError("archive snapshot artifact is missing")
        previous = (
            await self.session.get(AutoResearchArchiveVersion, previous_version_id)
            if previous_version_id
            else None
        )
        latest = await self.session.scalar(
            select(AutoResearchArchiveVersion)
            .where(
                AutoResearchArchiveVersion.run_id == run_id,
                AutoResearchArchiveVersion.branch_key == normalized_branch,
                AutoResearchArchiveVersion.archive_name == normalized_archive,
            )
            .order_by(AutoResearchArchiveVersion.iteration_no.desc())
            .limit(1)
        )
        if latest is not None and latest.iteration_no == iteration_no:
            existing_version = latest
        else:
            existing_version = None
            if latest is not None and previous_version_id != latest.id:
                raise ValueError("archive update must point at the latest current-run version")
        previous_active: set[uuid.UUID] = set()
        if previous_version_id is not None:
            if previous is None:
                raise KeyError("previous archive version is missing")
            if (
                previous.branch_key != normalized_branch
                or previous.archive_name != normalized_archive
                or previous.iteration_no >= iteration_no
                or previous.run_id not in await self._run_ancestor_ids(run_id)
            ):
                raise ValueError("previous archive version is not a valid predecessor")
            previous_active = set(
                await self.session.scalars(
                    select(AutoResearchArchiveMembership.candidate_id).where(
                        AutoResearchArchiveMembership.archive_version_id == previous_version_id,
                        AutoResearchArchiveMembership.is_active.is_(True),
                    )
                )
            )
        elif latest is not None and existing_version is None:
            raise ValueError("non-initial archive update requires previous_version_id")

        normalized_memberships: list[dict[str, Any]] = []
        candidate_ids: list[uuid.UUID] = []
        active_ordinals: list[int] = []
        active_ids: set[uuid.UUID] = set()
        permitted_runs = await self._run_ancestor_ids(run_id)
        for row in memberships:
            candidate_id = uuid.UUID(str(row["candidate_id"]))
            candidate = await self.session.get(Candidate, candidate_id)
            if candidate is None or candidate.run_id not in permitted_runs:
                raise ValueError("archive member is missing or outside the run ancestry")
            is_active = bool(row["is_active"])
            member_ordinal = (
                int(row["member_ordinal"]) if row.get("member_ordinal") is not None else None
            )
            if is_active:
                if member_ordinal is None or member_ordinal < 1:
                    raise ValueError("active archive members require positive ordinals")
                active_ids.add(candidate_id)
                active_ordinals.append(member_ordinal)
            elif member_ordinal is not None:
                raise ValueError("removed archive members cannot retain an ordinal")
            source_action_id = (
                uuid.UUID(str(row["source_action_id"]))
                if row.get("source_action_id") is not None
                else None
            )
            if source_action_id is not None:
                source_action = await self.session.get(AutoResearchAction, source_action_id)
                if source_action is None or source_action.run_id != run_id:
                    raise ValueError("archive source action is missing or cross-run")
            witnesses = [
                str(uuid.UUID(str(value))) for value in row.get("witness_candidate_ids", [])
            ]
            if len(witnesses) != len(set(witnesses)):
                raise ValueError("archive membership witnesses must be unique")
            for witness in witnesses:
                witness_candidate = await self.session.get(Candidate, uuid.UUID(witness))
                if witness_candidate is None or witness_candidate.run_id not in permitted_runs:
                    raise ValueError(
                        "archive membership witness is missing or outside the run ancestry"
                    )
            reason = str(row.get("reason", "")).strip()
            if not reason:
                raise ValueError("archive membership requires a reason")
            candidate_ids.append(candidate_id)
            normalized_memberships.append(
                {
                    "candidate_id": candidate_id,
                    "change_kind": str(row["change_kind"]),
                    "is_active": is_active,
                    "member_ordinal": member_ordinal,
                    "source_action_id": source_action_id,
                    "reason": reason,
                    "witness_candidate_ids_json": witnesses,
                    "metadata_json": row.get("metadata") or {},
                }
            )
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("archive version contains duplicate candidates")
        if sorted(active_ordinals) != list(range(1, len(active_ordinals) + 1)):
            raise ValueError("active archive ordinals must be unique and contiguous")
        expected_changes = _derive_archive_membership_changes(previous_active, active_ids)
        if set(candidate_ids) != set(expected_changes):
            raise ValueError("archive memberships must include every add, retain, and remove")
        for row in normalized_memberships:
            expected_change = expected_changes[row["candidate_id"]]
            if row["change_kind"] != expected_change:
                raise ValueError("archive membership change does not match the actual transition")

        version_identity = {
            "run_id": run_id,
            "iteration_no": iteration_no,
            "branch_key": normalized_branch,
            "archive_name": normalized_archive,
            "previous_version_id": previous_version_id,
            "policy_sha256": normalized_policy_sha256,
            "tool_call_id": tool_call_id,
            "snapshot_artifact_id": snapshot_artifact_id,
            "snapshot_sha256": artifact.sha256,
            "metadata_json": metadata or {},
        }
        if existing_version is not None:
            if not all(
                getattr(existing_version, key) == value for key, value in version_identity.items()
            ):
                raise ValueError("archive version retry payload drifted")
            existing_memberships = list(
                await self.session.scalars(
                    select(AutoResearchArchiveMembership).where(
                        AutoResearchArchiveMembership.archive_version_id == existing_version.id
                    )
                )
            )
            existing_projection = {
                (
                    row.candidate_id,
                    row.change_kind,
                    row.is_active,
                    row.member_ordinal,
                    row.source_action_id,
                    row.reason,
                    tuple(row.witness_candidate_ids_json),
                    sha256_json(row.metadata_json),
                )
                for row in existing_memberships
            }
            requested_projection = {
                (
                    row["candidate_id"],
                    row["change_kind"],
                    row["is_active"],
                    row["member_ordinal"],
                    row["source_action_id"],
                    row["reason"],
                    tuple(row["witness_candidate_ids_json"]),
                    sha256_json(row["metadata_json"]),
                )
                for row in normalized_memberships
            }
            if existing_projection != requested_projection:
                raise ValueError("archive membership retry payload drifted")
            return existing_version
        version = AutoResearchArchiveVersion(**version_identity)
        self.session.add(version)
        await self.session.flush()
        rows = [
            AutoResearchArchiveMembership(archive_version_id=version.id, **row)
            for row in normalized_memberships
        ]
        self.session.add_all(rows)
        await self.session.flush()
        await self.append_event(
            "run",
            run_id,
            "autoresearch.archive.updated",
            actor,
            {
                "archive_version_id": str(version.id),
                "snapshot_sha256": version.snapshot_sha256,
                "active_count": len(active_ids),
            },
        )
        return version

    async def record_autoresearch_checkpoint(
        self,
        *,
        run_id: uuid.UUID,
        iteration_no: int,
        run_stage_checkpoint_id: uuid.UUID,
        agent_decision_id: uuid.UUID,
        action_batch_sha256: str,
        archive_before_sha256: str,
        archive_after_sha256: str,
        score_all_candidate_count: int,
        score_all_required_metric_count: int,
        score_all_completed_evaluation_count: int,
        next_controller_action: str,
        replay_artifact_id: uuid.UUID,
        metadata: dict[str, Any] | None = None,
        actor: str = "autoresearch-controller",
    ) -> AutoResearchCheckpoint:
        """Close an iteration only with complete score-all and verified replay evidence."""
        if iteration_no < 0:
            raise ValueError("AutoResearch checkpoint iteration must be non-negative")
        expected_count = _validate_score_all_counts(
            score_all_candidate_count,
            score_all_required_metric_count,
            score_all_completed_evaluation_count,
        )
        normalized_action_sha = _require_sha256(action_batch_sha256, "action batch")
        normalized_before_sha = _require_sha256(archive_before_sha256, "archive before")
        normalized_after_sha = _require_sha256(archive_after_sha256, "archive after")
        normalized_next_action = next_controller_action.strip()
        if not normalized_next_action:
            raise ValueError("checkpoint next controller action must be non-empty")
        run = await self.session.get(ExperimentRun, run_id)
        stage_checkpoint = await self.session.get(RunStageCheckpoint, run_stage_checkpoint_id)
        decision = await self.session.get(AgentDecision, agent_decision_id)
        replay_artifact = await self.session.get(Artifact, replay_artifact_id)
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        if (
            stage_checkpoint is None
            or stage_checkpoint.run_id != run_id
            or not stage_checkpoint.stage_name.startswith("autoresearch")
            or stage_checkpoint.stage_status != "completed"
        ):
            raise ValueError("AutoResearch checkpoint requires a completed stage receipt")
        if (
            decision is None
            or decision.run_id != run_id
            or decision.generation != iteration_no
            or decision.status != "succeeded"
        ):
            raise ValueError("AutoResearch checkpoint decision is missing or inconsistent")
        if replay_artifact is None:
            raise KeyError("AutoResearch checkpoint replay artifact is missing")
        receipt_payload = {
            "schema_version": "autoresearch.checkpoint.v1",
            "run_id": str(run_id),
            "iteration_no": iteration_no,
            "run_stage_checkpoint_receipt_sha256": stage_checkpoint.receipt_sha256,
            "agent_decision_id": str(agent_decision_id),
            "action_batch_sha256": normalized_action_sha,
            "archive_before_sha256": normalized_before_sha,
            "archive_after_sha256": normalized_after_sha,
            "score_all_candidate_count": score_all_candidate_count,
            "score_all_required_metric_count": score_all_required_metric_count,
            "score_all_expected_evaluation_count": expected_count,
            "score_all_completed_evaluation_count": score_all_completed_evaluation_count,
            "next_controller_action": normalized_next_action,
            "replay_sha256": replay_artifact.sha256,
            "metadata": metadata or {},
        }
        identity = {
            "run_id": run_id,
            "iteration_no": iteration_no,
            "run_stage_checkpoint_id": run_stage_checkpoint_id,
            "agent_decision_id": agent_decision_id,
            "action_batch_sha256": normalized_action_sha,
            "archive_before_sha256": normalized_before_sha,
            "archive_after_sha256": normalized_after_sha,
            "score_all_candidate_count": score_all_candidate_count,
            "score_all_required_metric_count": score_all_required_metric_count,
            "score_all_expected_evaluation_count": expected_count,
            "score_all_completed_evaluation_count": score_all_completed_evaluation_count,
            "next_controller_action": normalized_next_action,
            "replay_artifact_id": replay_artifact_id,
            "replay_sha256": replay_artifact.sha256,
            "replay_verified": True,
            "receipt_sha256": sha256_json(receipt_payload),
            "metadata_json": metadata or {},
        }
        existing = await self.session.scalar(
            select(AutoResearchCheckpoint).where(
                AutoResearchCheckpoint.run_id == run_id,
                AutoResearchCheckpoint.iteration_no == iteration_no,
            )
        )
        if existing is not None:
            if not all(getattr(existing, key) == value for key, value in identity.items()):
                raise ValueError("AutoResearch checkpoint retry payload drifted")
            return existing
        checkpoint = AutoResearchCheckpoint(**identity)
        self.session.add(checkpoint)
        await self.session.flush()
        await self.append_event(
            "run",
            run_id,
            "autoresearch.checkpoint.recorded",
            actor,
            {
                "checkpoint_id": str(checkpoint.id),
                "receipt_sha256": checkpoint.receipt_sha256,
                "replay_sha256": checkpoint.replay_sha256,
            },
        )
        return checkpoint
