from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import (
    AgentDecision,
    AgentDecisionToolCallEdge,
    Candidate,
    CandidateOccurrence,
    Evaluation,
    ExperimentRun,
    LifecycleEvent,
    Target,
    ToolCall,
    ToolCallDependency,
)
from pepagent.domain.enums import CandidateStatus, EvaluationStatus, RunStatus
from pepagent.domain.schemas import ExperimentSpec
from pepagent.provenance.hashing import sha256_json, sha256_text


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
    ) -> LifecycleEvent:
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
            payload_sha256=sha256_json(payload),
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
    ) -> ToolCall:
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
