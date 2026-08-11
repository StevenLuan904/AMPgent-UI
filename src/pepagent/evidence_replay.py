from __future__ import annotations

import math
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import (
    AgentDecision,
    AgentDecisionToolCallEdge,
    Artifact,
    Candidate,
    CandidateOccurrence,
    Evaluation,
    EvidenceArtifact,
    ExperimentRun,
    LifecycleEvent,
    ToolCall,
    ToolCallDependency,
)
from pepagent.multiobjective_portfolio import (
    MultiobjectivePortfolioManifest,
    build_portfolio,
)
from pepagent.provenance.hashing import sha256_json


async def _all(session: AsyncSession, model: type[Any], *criteria: Any) -> list[Any]:
    return list(await session.scalars(select(model).where(*criteria)))


async def build_database_evidence_graph(
    session: AsyncSession, run_id: uuid.UUID
) -> dict[str, Any]:
    """Build a deterministic, database-only evidence graph for one experiment run."""
    run = await session.get(ExperimentRun, run_id)
    if run is None:
        raise KeyError(f"run not found: {run_id}")
    candidates = await _all(session, Candidate, Candidate.run_id == run_id)
    occurrences = await _all(
        session, CandidateOccurrence, CandidateOccurrence.run_id == run_id
    )
    calls = await _all(session, ToolCall, ToolCall.run_id == run_id)
    call_ids = {call.id for call in calls}
    candidate_ids = {candidate.id for candidate in candidates}
    evaluations = await _all(session, Evaluation, Evaluation.candidate_id.in_(candidate_ids))
    dependencies = await _all(
        session,
        ToolCallDependency,
        ToolCallDependency.child_tool_call_id.in_(call_ids),
    )
    decisions = await _all(session, AgentDecision, AgentDecision.run_id == run_id)
    decision_ids = {decision.id for decision in decisions}
    decision_edges = await _all(
        session,
        AgentDecisionToolCallEdge,
        AgentDecisionToolCallEdge.decision_id.in_(decision_ids),
    )
    evidence_links = await _all(
        session, EvidenceArtifact, EvidenceArtifact.tool_call_id.in_(call_ids)
    )
    artifact_ids = {link.artifact_id for link in evidence_links}
    artifacts = await _all(session, Artifact, Artifact.id.in_(artifact_ids))
    events = await _all(
        session,
        LifecycleEvent,
        ((LifecycleEvent.aggregate_type == "run") & (LifecycleEvent.aggregate_id == run_id))
        | (
            (LifecycleEvent.aggregate_type == "candidate")
            & LifecycleEvent.aggregate_id.in_(candidate_ids)
        ),
    )

    for candidate in candidates:
        if candidate.generator_call_id is None or candidate.generator_call_id not in call_ids:
            raise ValueError(f"candidate lacks in-run generator evidence: {candidate.id}")
    dependency_keys = {
        (edge.child_tool_call_id, edge.parent_tool_call_id) for edge in dependencies
    }
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    calls_by_id = {call.id: call for call in calls}
    for occurrence in occurrences:
        if occurrence.tool_call_id not in calls_by_id:
            raise ValueError(
                f"candidate occurrence references an out-of-run call: {occurrence.id}"
            )
        parent = (
            await session.get(Candidate, occurrence.parent_candidate_id)
            if occurrence.parent_candidate_id is not None
            else None
        )
        if occurrence.parent_candidate_id is not None and parent is None:
            raise ValueError(f"candidate occurrence parent is missing: {occurrence.id}")
        if occurrence.candidate_id is not None:
            materialized = candidates_by_id.get(occurrence.candidate_id)
            if materialized is None:
                raise ValueError(
                    f"candidate occurrence materialization is out-of-run: {occurrence.id}"
                )
            if materialized.sequence_sha256 != occurrence.sequence_sha256:
                raise ValueError(
                    f"candidate occurrence materialization differs: {occurrence.id}"
                )
    for evaluation in evaluations:
        candidate = candidates_by_id[evaluation.candidate_id]
        if evaluation.tool_call_id not in call_ids:
            raise ValueError(f"evaluation references an out-of-run tool call: {evaluation.id}")
        if evaluation.tool_call_id != candidate.generator_call_id and (
            evaluation.tool_call_id,
            candidate.generator_call_id,
        ) not in dependency_keys:
            raise ValueError(
                f"evaluation tool call is not linked to candidate generation: {evaluation.id}"
            )
    for edge in decision_edges:
        if edge.tool_call_id not in call_ids:
            raise ValueError(f"decision edge references an out-of-run call: {edge.tool_call_id}")

    graph = {
        "schema_version": "1.0",
        "run": {
            "id": str(run.id),
            "target_id": str(run.target_id),
            "spec_sha256": run.spec_sha256,
            "spec_json": run.spec_json,
            "status": str(run.status),
            "temporal_workflow_id": run.temporal_workflow_id,
            "temporal_run_id": run.temporal_run_id,
        },
        "candidates": [
            {
                "id": str(item.id),
                "sequence": item.sequence,
                "sequence_sha256": item.sequence_sha256,
                "generation": item.generation,
                "parent_id": str(item.parent_id) if item.parent_id else None,
                "proposal_rank": item.proposal_rank,
                "status": str(item.status),
                "generator_call_id": str(item.generator_call_id),
                "metadata": item.metadata_json,
            }
            for item in sorted(
                candidates, key=lambda value: (value.proposal_rank or 0, str(value.id))
            )
        ],
        "candidate_occurrences": [
            {
                "id": str(item.id),
                "tool_call_id": str(item.tool_call_id),
                "candidate_id": str(item.candidate_id) if item.candidate_id else None,
                "parent_candidate_id": (
                    str(item.parent_candidate_id) if item.parent_candidate_id else None
                ),
                "occurrence_rank": item.occurrence_rank,
                "occurrence_kind": item.occurrence_kind,
                "opaque_arm_label": item.opaque_arm_label,
                "sequence": item.sequence,
                "sequence_sha256": item.sequence_sha256,
                "metadata": item.metadata_json,
            }
            for item in sorted(
                occurrences,
                key=lambda value: (str(value.tool_call_id), value.occurrence_rank),
            )
        ],
        "tool_calls": [
            {
                "id": str(item.id),
                "tool_name": item.tool_name,
                "tool_version": item.tool_version,
                "model_uri": item.model_uri,
                "weights_sha256": item.weights_sha256,
                "environment_sha256": item.environment_sha256,
                "idempotency_key": item.idempotency_key,
                "input_sha256": item.input_sha256,
                "input_json": item.input_json,
                "parameters_json": item.parameters_json,
                "random_seed": item.random_seed,
                "status": str(item.status),
                "attempt": item.attempt,
                "output_sha256": item.output_sha256,
                "error_json": item.error_json,
            }
            for item in sorted(calls, key=lambda value: str(value.id))
        ],
        "tool_call_dependencies": [
            {
                "child_tool_call_id": str(item.child_tool_call_id),
                "parent_tool_call_id": str(item.parent_tool_call_id),
                "relation_type": item.relation_type,
            }
            for item in sorted(
                dependencies,
                key=lambda value: (
                    str(value.child_tool_call_id),
                    str(value.parent_tool_call_id),
                    value.relation_type,
                ),
            )
        ],
        "evaluations": [
            {
                "id": str(item.id),
                "candidate_id": str(item.candidate_id),
                "tool_call_id": str(item.tool_call_id),
                "metric_name": item.metric_name,
                "numeric_value": item.numeric_value,
                "text_value": item.text_value,
                "unit": item.unit,
                "status": str(item.status),
                "out_of_domain": item.out_of_domain,
                "limitations": item.limitations_json,
                "raw": item.raw_json,
            }
            for item in sorted(
                evaluations,
                key=lambda value: (str(value.candidate_id), value.metric_name, str(value.id)),
            )
        ],
        "agent_decisions": [
            {
                "id": str(item.id),
                "generation": item.generation,
                "decision_type": item.decision_type,
                "agent_name": item.agent_name,
                "agent_version": item.agent_version,
                "model_name": item.model_name,
                "prompt_sha256": item.prompt_sha256,
                "response_sha256": item.response_sha256,
                "structured_json": item.structured_json,
                "status": item.status,
            }
            for item in sorted(decisions, key=lambda value: str(value.id))
        ],
        "agent_decision_tool_call_edges": [
            {
                "decision_id": str(item.decision_id),
                "tool_call_id": str(item.tool_call_id),
                "direction": item.direction,
                "relation_type": item.relation_type,
            }
            for item in sorted(
                decision_edges,
                key=lambda value: (
                    str(value.decision_id), value.direction, str(value.tool_call_id)
                ),
            )
        ],
        "artifacts": [
            {
                "id": str(item.id),
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
                "media_type": item.media_type,
                "storage_uri": item.storage_uri,
                "metadata": item.metadata_json,
            }
            for item in sorted(artifacts, key=lambda value: value.sha256)
        ],
        "evidence_artifacts": [
            {
                "tool_call_id": str(item.tool_call_id),
                "artifact_id": str(item.artifact_id),
                "role": item.role,
            }
            for item in sorted(
                evidence_links,
                key=lambda value: (str(value.tool_call_id), value.role, str(value.artifact_id)),
            )
        ],
        "lifecycle_events": [
            {
                "id": str(item.id),
                "aggregate_type": item.aggregate_type,
                "aggregate_id": str(item.aggregate_id),
                "sequence_no": item.sequence_no,
                "event_type": item.event_type,
                "actor": item.actor,
                "payload_sha256": item.payload_sha256,
                "payload_json": item.payload_json,
            }
            for item in sorted(
                events,
                key=lambda value: (
                    value.aggregate_type,
                    str(value.aggregate_id),
                    value.sequence_no,
                ),
            )
        ],
    }
    graph["graph_sha256"] = sha256_json(graph)
    return graph


def replay_v32_portfolio(
    graph: dict[str, Any], manifest: MultiobjectivePortfolioManifest
) -> dict[str, Any]:
    """Reconstruct the v32 selection only from the persisted evidence graph."""
    metrics: dict[str, dict[str, float]] = {}
    labels: dict[str, dict[str, str]] = {}
    for evaluation in graph["evaluations"]:
        candidate_id = evaluation["candidate_id"]
        metric_name = evaluation["metric_name"]
        if evaluation["numeric_value"] is not None:
            value = float(evaluation["numeric_value"])
            if not math.isfinite(value):
                raise ValueError(f"non-finite replay metric: {candidate_id}/{metric_name}")
            if metric_name in metrics.setdefault(candidate_id, {}):
                raise ValueError(f"ambiguous replay metric: {candidate_id}/{metric_name}")
            metrics[candidate_id][metric_name] = value
        if evaluation["text_value"] is not None:
            if metric_name in labels.setdefault(candidate_id, {}):
                raise ValueError(f"ambiguous replay label: {candidate_id}/{metric_name}")
            labels[candidate_id][metric_name] = evaluation["text_value"]
    candidates = []
    for candidate in graph["candidates"]:
        candidates.append(
            {
                "id": candidate["id"],
                "seed": candidate["metadata"]["generator_seed"],
                "sequence": candidate["sequence"],
                "sequence_sha256": candidate["sequence_sha256"],
                "metrics": metrics.get(candidate["id"], {}),
                "labels": labels.get(candidate["id"], {}),
            }
        )
    return build_portfolio(candidates, manifest)
