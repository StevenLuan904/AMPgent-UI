from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from pepagent.db.models import (
    AgentDecision,
    AgentDecisionToolCallEdge,
    Artifact,
    Candidate,
    Evaluation,
    EvidenceArtifact,
    ExperimentRun,
    LifecycleEvent,
    ModelRelease,
    ModelReleaseArtifact,
    PocketEvidence,
    Target,
    TargetPocket,
    ToolCall,
    ToolCallDependency,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import get_session
from pepagent.domain.schemas import CandidateRecord, ExperimentSpec
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore


class RunCreated(BaseModel):
    run_id: uuid.UUID
    workflow_id: str
    status: str


class ImportedMetric(BaseModel):
    name: str
    value: float | None = None
    unit: str | None = None
    text_value: str | None = None


class ValidationImport(BaseModel):
    evaluator: str
    evaluator_version: str
    target: dict
    peptide_sequence: str
    environment_sha256: str
    weights_sha256: str | None = None
    model_uri: str | None = None
    parameters: dict = Field(default_factory=dict)
    raw_output: dict
    metrics: list[ImportedMetric]
    out_of_domain: bool = False
    limitations: list[str] = Field(default_factory=list)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.temporal = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    yield


app = FastAPI(title="PepAgent control plane", version="0.3.0", lifespan=lifespan)
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/runs", response_model=RunCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_run(spec: ExperimentSpec, session: SessionDep) -> RunCreated:
    async with session.begin():
        run = await ExperimentRepository(session).create_run(spec)
    workflow_id = f"pepagent-run-{run.id}"
    await app.state.temporal.start_workflow(
        "PeptideDesignWorkflow",
        {"run_id": str(run.id), "spec": spec.model_dump(mode="json")},
        id=workflow_id,
        task_queue="pepagent-control",
    )
    return RunCreated(run_id=run.id, workflow_id=workflow_id, status=run.status)


@app.get("/v1/runs/{run_id}")
async def get_run(run_id: uuid.UUID, session: SessionDep) -> dict:
    run = await session.get(ExperimentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = list(
        await session.scalars(
            select(LifecycleEvent)
            .where(
                LifecycleEvent.aggregate_type == "run",
                LifecycleEvent.aggregate_id == run_id,
            )
            .order_by(LifecycleEvent.sequence_no)
        )
    )
    return {
        "id": run.id,
        "target_id": run.target_id,
        "status": run.status,
        "spec_sha256": run.spec_sha256,
        "workflow_id": run.temporal_workflow_id,
        "created_at": run.created_at,
        "events": [
            {
                "sequence_no": event.sequence_no,
                "event_type": event.event_type,
                "actor": event.actor,
                "payload": event.payload_json,
                "payload_sha256": event.payload_sha256,
                "occurred_at": event.occurred_at,
            }
            for event in events
        ],
    }


@app.get("/v1/runs/{run_id}/candidates", response_model=list[CandidateRecord])
async def get_candidates(run_id: uuid.UUID, session: SessionDep) -> list[Candidate]:
    return list(
        await session.scalars(
            select(Candidate)
            .where(Candidate.run_id == run_id)
            .order_by(Candidate.generation, Candidate.proposal_rank)
        )
    )


@app.get("/v1/runs/{run_id}/evidence")
async def get_run_evidence(run_id: uuid.UUID, session: SessionDep) -> dict:
    run = await session.get(ExperimentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    calls = list(
        await session.scalars(
            select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.queued_at)
        )
    )
    dependencies = list(
        await session.scalars(
            select(ToolCallDependency)
            .join(ToolCall, ToolCallDependency.child_tool_call_id == ToolCall.id)
            .where(ToolCall.run_id == run_id)
            .order_by(
                ToolCallDependency.child_tool_call_id,
                ToolCallDependency.parent_tool_call_id,
                ToolCallDependency.relation_type,
            )
        )
    )
    call_payloads: list[dict] = []
    for call in calls:
        artifacts = list(
            await session.execute(
                select(EvidenceArtifact, Artifact)
                .join(Artifact, EvidenceArtifact.artifact_id == Artifact.id)
                .where(EvidenceArtifact.tool_call_id == call.id)
                .order_by(EvidenceArtifact.role)
            )
        )
        call_payloads.append(
            {
                "id": call.id,
                "tool_name": call.tool_name,
                "tool_version": call.tool_version,
                "model_uri": call.model_uri,
                "weights_sha256": call.weights_sha256,
                "environment_sha256": call.environment_sha256,
                "input_sha256": call.input_sha256,
                "input": call.input_json,
                "parameters": call.parameters_json,
                "random_seed": call.random_seed,
                "status": call.status,
                "attempt": call.attempt,
                "output_sha256": call.output_sha256,
                "artifacts": [
                    {
                        "role": link.role,
                        "sha256": artifact.sha256,
                        "size_bytes": artifact.size_bytes,
                        "media_type": artifact.media_type,
                        "uri": artifact.storage_uri,
                        "metadata": artifact.metadata_json,
                    }
                    for link, artifact in artifacts
                ],
            }
        )
    evaluations = list(
        await session.execute(
            select(Evaluation, Candidate)
            .join(Candidate, Evaluation.candidate_id == Candidate.id)
            .where(Candidate.run_id == run_id)
            .order_by(Candidate.proposal_rank, Evaluation.metric_name)
        )
    )
    decisions = list(
        await session.scalars(
            select(AgentDecision)
            .where(AgentDecision.run_id == run_id)
            .order_by(AgentDecision.generation, AgentDecision.created_at)
        )
    )
    decision_edges = list(
        await session.scalars(
            select(AgentDecisionToolCallEdge)
            .join(AgentDecision, AgentDecisionToolCallEdge.decision_id == AgentDecision.id)
            .where(AgentDecision.run_id == run_id)
            .order_by(
                AgentDecisionToolCallEdge.decision_id,
                AgentDecisionToolCallEdge.direction,
                AgentDecisionToolCallEdge.relation_type,
            )
        )
    )
    return {
        "run_id": run_id,
        "spec_sha256": run.spec_sha256,
        "tool_calls": call_payloads,
        "dependencies": [
            {
                "child_tool_call_id": edge.child_tool_call_id,
                "parent_tool_call_id": edge.parent_tool_call_id,
                "relation_type": edge.relation_type,
            }
            for edge in dependencies
        ],
        "evaluations": [
            {
                "id": evaluation.id,
                "candidate_id": candidate.id,
                "candidate_sequence_sha256": candidate.sequence_sha256,
                "tool_call_id": evaluation.tool_call_id,
                "metric_name": evaluation.metric_name,
                "numeric_value": evaluation.numeric_value,
                "text_value": evaluation.text_value,
                "unit": evaluation.unit,
                "status": evaluation.status,
                "out_of_domain": evaluation.out_of_domain,
                "limitations": evaluation.limitations_json,
            }
            for evaluation, candidate in evaluations
        ],
        "agent_decisions": [
            {
                "id": decision.id,
                "generation": decision.generation,
                "decision_type": decision.decision_type,
                "agent_name": decision.agent_name,
                "agent_version": decision.agent_version,
                "model_name": decision.model_name,
                "prompt_text": decision.prompt_text,
                "response_text": decision.response_text,
                "prompt_sha256": decision.prompt_sha256,
                "response_sha256": decision.response_sha256,
                "structured": decision.structured_json,
                "status": decision.status,
                "prompt_artifact_id": decision.prompt_artifact_id,
                "response_artifact_id": decision.response_artifact_id,
                "created_at": decision.created_at,
            }
            for decision in decisions
        ],
        "agent_decision_edges": [
            {
                "decision_id": edge.decision_id,
                "tool_call_id": edge.tool_call_id,
                "direction": edge.direction,
                "relation_type": edge.relation_type,
            }
            for edge in decision_edges
        ],
    }


@app.get("/v1/model-releases")
async def list_model_releases(session: SessionDep) -> list[dict]:
    releases = list(
        await session.scalars(
            select(ModelRelease).order_by(ModelRelease.name, ModelRelease.created_at)
        )
    )
    payload: list[dict] = []
    for release in releases:
        artifacts = list(
            await session.execute(
                select(ModelReleaseArtifact, Artifact)
                .join(Artifact, ModelReleaseArtifact.artifact_id == Artifact.id)
                .where(ModelReleaseArtifact.model_release_id == release.id)
                .order_by(ModelReleaseArtifact.role)
            )
        )
        payload.append(
            {
                "id": release.id,
                "name": release.name,
                "role": release.role,
                "source_uri": release.source_uri,
                "source_revision": release.source_revision,
                "weights_sha256": release.weights_sha256,
                "adapter_version": release.adapter_version,
                "admission_status": release.admission_status,
                "mlflow_model_name": release.mlflow_model_name,
                "mlflow_model_version": release.mlflow_model_version,
                "artifacts": [
                    {
                        "role": link.role,
                        "sha256": artifact.sha256,
                        "size_bytes": artifact.size_bytes,
                        "uri": artifact.storage_uri,
                    }
                    for link, artifact in artifacts
                ],
            }
        )
    return payload


@app.get("/v1/targets/by-accession/{accession}/pockets")
async def get_target_pockets(accession: str, session: SessionDep) -> dict:
    target = await session.scalar(select(Target).where(Target.accession == accession))
    if target is None:
        raise HTTPException(status_code=404, detail="target not found")
    pockets = list(
        await session.scalars(
            select(TargetPocket)
            .where(TargetPocket.target_id == target.id)
            .order_by(
                TargetPocket.conditioning_enabled.desc(),
                TargetPocket.evidence_score.desc(),
                TargetPocket.pocket_key,
            )
        )
    )
    payload: list[dict] = []
    for pocket in pockets:
        evidence = list(
            await session.scalars(
                select(PocketEvidence)
                .where(PocketEvidence.pocket_id == pocket.id)
                .order_by(PocketEvidence.confidence.desc(), PocketEvidence.created_at)
            )
        )
        payload.append(
            {
                "id": pocket.id,
                "key": pocket.pocket_key,
                "name": pocket.name,
                "type": pocket.pocket_type,
                "functional_role": pocket.functional_role,
                "status": pocket.status,
                "evidence_grade": pocket.evidence_grade,
                "evidence_score": pocket.evidence_score,
                "conditioning_priority": pocket.conditioning_priority,
                "conditioning_enabled": pocket.conditioning_enabled,
                "residue_indices": pocket.residue_indices,
                "context": pocket.context_json,
                "limitations": pocket.limitations_json,
                "evidence": [
                    {
                        "id": item.id,
                        "kind": item.evidence_kind,
                        "grade": item.evidence_grade,
                        "source_type": item.source_type,
                        "source_uri": item.source_uri,
                        "source_accession": item.source_accession,
                        "source_version": item.source_version,
                        "source_revision_date": item.source_revision_date,
                        "retrieved_at": item.retrieved_at,
                        "chain_ids": item.chain_ids,
                        "source_residue_indices": item.source_residue_indices,
                        "target_residue_indices": item.residue_indices,
                        "confidence": item.confidence,
                        "experimental_method": item.experimental_method,
                        "resolution_angstrom": item.resolution_angstrom,
                        "mapping": item.mapping_json,
                        "limitations": item.limitations_json,
                        "details": item.evidence_json,
                        "evidence_sha256": item.evidence_sha256,
                    }
                    for item in evidence
                ],
            }
        )
    return {
        "target": {
            "id": target.id,
            "name": target.name,
            "organism": target.organism,
            "accession": target.accession,
            "sequence_sha256": target.sequence_sha256,
            "metadata": target.metadata_json,
        },
        "pockets": payload,
    }


@app.post(
    "/v1/runs/{run_id}/replay",
    response_model=RunCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
async def replay_run(run_id: uuid.UUID, session: SessionDep) -> RunCreated:
    source = await session.get(ExperimentRun, run_id)
    if source is None:
        raise HTTPException(status_code=404, detail="run not found")
    try:
        spec = ExperimentSpec.model_validate(source.spec_json)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"source run is no longer admitted for replay: {error}",
        ) from error
    repository = ExperimentRepository(session)
    replay = await repository.create_run(
        spec,
        actor="replay-api",
        parent_run_id=source.id,
        raw_spec_payload=source.spec_json,
    )
    await repository.append_event(
        "run",
        replay.id,
        "run.replay_created",
        "replay-api",
        {"source_run_id": str(source.id), "source_spec_sha256": source.spec_sha256},
    )
    await session.commit()
    workflow_id = f"pepagent-run-{replay.id}"
    await app.state.temporal.start_workflow(
        "PeptideDesignWorkflow",
        {"run_id": str(replay.id), "spec": source.spec_json},
        id=workflow_id,
        task_queue="pepagent-control",
    )
    return RunCreated(run_id=replay.id, workflow_id=workflow_id, status=replay.status)


@app.post("/v1/validations", status_code=status.HTTP_201_CREATED)
async def import_validation(payload: ValidationImport, session: SessionDep) -> dict:
    """Import a completed, externally executed validation with immutable evidence."""
    if payload.evaluator.lower() == "peppap":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="PepPAP is frozen; historical evidence is retained but new imports are disabled",
        )
    spec = ExperimentSpec(
        target=payload.target,
        peptide_lengths=[len(payload.peptide_sequence)],
        candidates_per_length=1,
        structure_top_k=1,
        generations=1,
        affinity_evaluators=[payload.evaluator],
    )
    output_bytes = json.dumps(
        payload.raw_output, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    stored = await asyncio.to_thread(
        lambda: ContentAddressedObjectStore().put_bytes(output_bytes, "application/json")
    )
    async with session.begin():
        repository = ExperimentRepository(session)
        run = await repository.create_run(spec, actor="validation-import")
        run.status = "succeeded"
        candidate = await repository.add_candidate(
            run.id,
            payload.peptide_sequence,
            generation=0,
            proposal_rank=1,
            metadata={"validation_import": True},
        )
        call = await repository.record_completed_tool_call(
            run.id,
            payload.evaluator,
            payload.evaluator_version,
            payload.environment_sha256,
            {
                "target_sequence": spec.target.sequence,
                "peptide_sequence": payload.peptide_sequence,
            },
            payload.parameters,
            payload.raw_output,
            weights_sha256=payload.weights_sha256,
            model_uri=payload.model_uri,
        )
        artifact = await session.scalar(select(Artifact).where(Artifact.sha256 == stored.sha256))
        if artifact is None:
            artifact = Artifact(
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                media_type=stored.media_type,
                storage_uri=stored.uri,
                metadata_json={"evaluator": payload.evaluator},
            )
            session.add(artifact)
            await session.flush()
        session.add(
            EvidenceArtifact(tool_call_id=call.id, artifact_id=artifact.id, role="raw_output")
        )
        for metric in payload.metrics:
            await repository.record_evaluation(
                candidate.id,
                call.id,
                metric.name,
                metric.value,
                metric.unit,
                payload.raw_output,
                text_value=metric.text_value,
                out_of_domain=payload.out_of_domain,
                limitations=payload.limitations,
            )
        await repository.append_event(
            "run",
            run.id,
            "run.succeeded",
            "validation-import",
            {"artifact_sha256": stored.sha256, "evaluator": payload.evaluator},
        )
    return {
        "run_id": run.id,
        "candidate_id": candidate.id,
        "tool_call_id": call.id,
        "artifact_uri": stored.uri,
        "artifact_sha256": stored.sha256,
    }


def run() -> None:
    import uvicorn

    uvicorn.run("pepagent.api.main:app", host="0.0.0.0", port=8080)
