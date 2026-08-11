from __future__ import annotations

import json
import math
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import (
    AgentDecision,
    Artifact,
    Candidate,
    CandidateOccurrence,
    Evaluation,
    EvidenceArtifact,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.evidence_replay import build_database_evidence_graph
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.v34_evidence import V34_EVIDENCE_VERSION, validate_v34_replay_graph

ArtifactWriter = Callable[[dict[str, Any]], Awaitable[Any]]
ArtifactReader = Callable[[str], bytes]


def _stored_payload(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        value = asdict(value)
    if not isinstance(value, Mapping):
        raise TypeError("artifact writer must return a mapping or dataclass")
    required = {"sha256", "size_bytes", "media_type"}
    if not required.issubset(value) or not ({"uri", "storage_uri"} & set(value)):
        raise ValueError("artifact writer returned incomplete content-addressed identity")
    return {
        "sha256": str(value["sha256"]),
        "size_bytes": int(value["size_bytes"]),
        "media_type": str(value["media_type"]),
        "storage_uri": str(value.get("uri", value.get("storage_uri"))),
    }


def _tool_contract(plan: Mapping[str, Any], logical_id: str) -> Mapping[str, Any]:
    matches = [
        tool
        for episode in plan["episodes"]
        for tool in episode["tool_calls"]
        if tool["logical_id"] == logical_id
    ]
    matches.extend(
        tool for tool in plan["global_tool_calls"] if tool["logical_id"] == logical_id
    )
    if len(matches) != 1:
        raise ValueError(f"v34 logical ToolCall is absent or ambiguous: {logical_id}")
    return matches[0]


async def _register_exact_json_artifact(
    session: AsyncSession,
    *,
    tool_call_id: uuid.UUID,
    role: str,
    payload: dict[str, Any],
    artifact_writer: ArtifactWriter,
) -> Artifact:
    stored = _stored_payload(await artifact_writer(payload))
    if stored["sha256"] != sha256_json(payload):
        raise ValueError("artifact writer content hash differs from canonical JSON payload")
    artifact = await session.scalar(
        select(Artifact).where(Artifact.sha256 == stored["sha256"])
    )
    if artifact is None:
        artifact = Artifact(
            sha256=stored["sha256"],
            size_bytes=stored["size_bytes"],
            media_type=stored["media_type"],
            storage_uri=stored["storage_uri"],
            metadata_json={"v34_role": role},
        )
        session.add(artifact)
        await session.flush()
    else:
        observed = (artifact.size_bytes, artifact.media_type, artifact.storage_uri)
        expected = (
            stored["size_bytes"],
            stored["media_type"],
            stored["storage_uri"],
        )
        if observed != expected:
            raise ValueError("existing artifact identity differs from retry payload")
    conflicting = await session.scalar(
        select(EvidenceArtifact).where(
            EvidenceArtifact.tool_call_id == tool_call_id,
            EvidenceArtifact.role == role,
            EvidenceArtifact.artifact_id != artifact.id,
        )
    )
    if conflicting is not None:
        raise ValueError("v34 artifact role is already linked to different content")
    link = await session.get(
        EvidenceArtifact,
        {"tool_call_id": tool_call_id, "artifact_id": artifact.id, "role": role},
    )
    if link is None:
        session.add(
            EvidenceArtifact(
                tool_call_id=tool_call_id,
                artifact_id=artifact.id,
                role=role,
            )
        )
        await session.flush()
    return artifact


async def persist_v34_tool_result(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    plan: Mapping[str, Any],
    logical_id: str,
    environment_sha256: str,
    input_payload: dict[str, Any],
    parameters: dict[str, Any],
    output_payload: dict[str, Any],
    artifact_payloads_by_role: Mapping[str, dict[str, Any]],
    artifact_writer: ArtifactWriter,
    model_uri: str | None = None,
    weights_sha256: str | None = None,
    random_seed: int | None = None,
) -> ToolCall:
    """Persist one frozen v34 node and all required artifacts with exact retries."""
    contract = _tool_contract(plan, logical_id)
    if contract["tool_name"] == "v34-assignment-reveal":
        await assert_v34_reveal_gate(session, run_id=run_id, plan=plan)
    if contract["tool_name"] == "v34-factorial-analysis":
        await assert_v34_reveal_gate(session, run_id=run_id, plan=plan)
        calls = list(
            await session.scalars(select(ToolCall).where(ToolCall.run_id == run_id))
        )
        if not any(
            item.input_json.get("v34_logical_id")
            == "v34-global:v34-assignment-reveal"
            and item.status == "succeeded"
            for item in calls
        ):
            raise ValueError("v34 analysis is blocked until assignment reveal succeeds")
    required_roles = set(contract["required_artifact_roles"])
    if set(artifact_payloads_by_role) != required_roles:
        raise ValueError(f"v34 artifact roles differ from contract for {logical_id}")
    repository = ExperimentRepository(session)
    call_input = {"v34_logical_id": logical_id, "payload": input_payload}
    call_parameters = {
        "v34_plan_sha256": plan["plan_sha256"],
        "parameters": parameters,
    }
    call = await repository.record_completed_tool_call(
        run_id,
        str(contract["tool_name"]),
        V34_EVIDENCE_VERSION,
        environment_sha256,
        call_input,
        call_parameters,
        output_payload,
        model_uri=model_uri,
        weights_sha256=weights_sha256,
        random_seed=random_seed,
    )
    if call.output_sha256 != sha256_json(output_payload):
        raise ValueError("persisted v34 ToolCall differs from retry output")
    if call.input_json != call_input or call.parameters_json != call_parameters:
        raise ValueError("persisted v34 ToolCall differs from retry contract")
    for role, payload in sorted(artifact_payloads_by_role.items()):
        await _register_exact_json_artifact(
            session,
            tool_call_id=call.id,
            role=role,
            payload=payload,
            artifact_writer=artifact_writer,
        )
    return call


async def persist_v34_proposal_occurrences(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    proposal_call_id: uuid.UUID,
    parent_candidate_id: uuid.UUID,
    opaque_arm_label: str,
    occurrences: Sequence[Mapping[str, Any]],
    expected_count: int = 8,
) -> list[CandidateOccurrence]:
    """Persist every raw proposal event, including duplicates and rejected sequences."""
    if len(occurrences) != expected_count:
        raise ValueError("v34 proposal occurrence count differs from fixed budget")
    repository = ExperimentRepository(session)
    rows: list[CandidateOccurrence] = []
    for rank, item in enumerate(occurrences, start=1):
        if int(item.get("occurrence_rank", rank)) != rank:
            raise ValueError("v34 proposal occurrence ranks must be contiguous and ordered")
        candidate_id = item.get("candidate_id")
        rows.append(
            await repository.record_candidate_occurrence(
                run_id=run_id,
                tool_call_id=proposal_call_id,
                parent_candidate_id=parent_candidate_id,
                occurrence_rank=rank,
                occurrence_kind=str(item["occurrence_kind"]),
                opaque_arm_label=opaque_arm_label,
                sequence=str(item["sequence"]),
                candidate_id=uuid.UUID(str(candidate_id)) if candidate_id else None,
                metadata=dict(item.get("metadata", {})),
            )
        )
    return rows


async def persist_v34_dependency_graph(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    plan: Mapping[str, Any],
) -> None:
    """Materialize the frozen logical DAG only after every referenced call exists."""
    calls = list(await session.scalars(select(ToolCall).where(ToolCall.run_id == run_id)))
    by_logical: dict[str, ToolCall] = {}
    for call in calls:
        logical_id = call.input_json.get("v34_logical_id")
        if logical_id is None:
            continue
        if logical_id in by_logical:
            raise ValueError(f"duplicate persisted v34 logical ToolCall: {logical_id}")
        by_logical[str(logical_id)] = call
    expected = set(plan["required_tool_call_ids"])
    if set(by_logical) != expected:
        raise ValueError("v34 dependency graph cannot materialize before all calls exist")
    repository = ExperimentRepository(session)
    for parent_logical, child_logical in plan["required_dependencies"]:
        await repository.record_tool_dependency(
            by_logical[child_logical].id,
            by_logical[parent_logical].id,
            "v34_preregistered_dependency",
        )


async def persist_v34_blinded_adjudication(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    tool_call_id: uuid.UUID,
    logical_id: str,
    prompt_text: str,
    response_text: str,
    structured: dict[str, Any],
) -> AgentDecision:
    """Persist a retry-safe blinded verdict that cannot encode the sealed arm name."""
    if structured.get("locked_before_assignment_reveal") is not True:
        raise ValueError("v34 adjudication must lock before assignment reveal")
    if "arm" in structured or "arm_identity" in structured:
        raise ValueError("v34 blinded adjudication cannot contain arm identity")
    payload = {**structured, "v34_logical_id": logical_id}
    existing = await session.scalar(
        select(AgentDecision).where(
            AgentDecision.run_id == run_id,
            AgentDecision.decision_type == "v34_blinded_adjudication",
            AgentDecision.structured_json["v34_logical_id"].astext == logical_id,
        )
    )
    repository = ExperimentRepository(session)
    if existing is None:
        existing = await repository.record_agent_decision(
            run_id,
            0,
            "v34_blinded_adjudication",
            "v34-blinded-adjudicator",
            V34_EVIDENCE_VERSION,
            prompt_text,
            response_text,
            payload,
        )
        await repository.record_agent_tool_edge(
            existing.id, tool_call_id, "output", "materializes_blinded_adjudication"
        )
        return existing
    if (
        existing.prompt_text != prompt_text
        or existing.response_text != response_text
        or existing.structured_json != payload
    ):
        raise ValueError("persisted v34 adjudication differs from retry payload")
    return existing


async def persist_v34_intervention_decision(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    tool_call_id: uuid.UUID,
    logical_id: str,
    observed_tool_call_ids: Sequence[uuid.UUID],
    prompt_text: str,
    response_text: str,
    structured: dict[str, Any],
) -> AgentDecision:
    """Persist the proposal/review action and its exact evidence edges."""
    payload = {**structured, "v34_logical_id": logical_id}
    existing = await session.scalar(
        select(AgentDecision).where(
            AgentDecision.run_id == run_id,
            AgentDecision.decision_type == "v34_intervention",
            AgentDecision.structured_json["v34_logical_id"].astext == logical_id,
        )
    )
    repository = ExperimentRepository(session)
    if existing is None:
        existing = await repository.record_agent_decision(
            run_id,
            0,
            "v34_intervention",
            "v34-ablation-agent",
            V34_EVIDENCE_VERSION,
            prompt_text,
            response_text,
            payload,
        )
    elif (
        existing.prompt_text != prompt_text
        or existing.response_text != response_text
        or existing.structured_json != payload
    ):
        raise ValueError("persisted v34 intervention differs from retry payload")
    for observed_id in sorted(set(observed_tool_call_ids), key=str):
        await repository.record_agent_tool_edge(
            existing.id, observed_id, "input", "observes_episode_evidence"
        )
    await repository.record_agent_tool_edge(
        existing.id, tool_call_id, "output", "materializes_intervention_decision"
    )
    return existing


async def persist_v34_evaluation(
    session: AsyncSession,
    *,
    candidate_id: uuid.UUID,
    tool_call_id: uuid.UUID,
    metric_name: str,
    numeric_value: float | None,
    text_value: str | None,
    unit: str | None,
    raw: dict[str, Any],
    out_of_domain: bool = False,
    limitations: Sequence[str] = (),
) -> Evaluation:
    """Persist one endpoint exactly and reject non-finite or retry-drifted evidence."""
    if numeric_value is not None and not math.isfinite(numeric_value):
        raise ValueError("v34 evaluation cannot persist a non-finite numeric value")
    candidate = await session.get(Candidate, candidate_id)
    call = await session.get(ToolCall, tool_call_id)
    if candidate is None or call is None or candidate.run_id != call.run_id:
        raise ValueError("v34 evaluation candidate/call mapping is missing or cross-run")
    repository = ExperimentRepository(session)
    evaluation = await repository.record_evaluation(
        candidate_id,
        tool_call_id,
        metric_name,
        numeric_value,
        unit,
        raw,
        text_value=text_value,
        out_of_domain=out_of_domain,
        limitations=list(limitations),
    )
    observed = (
        evaluation.numeric_value,
        evaluation.text_value,
        evaluation.unit,
        evaluation.out_of_domain,
        evaluation.limitations_json,
        evaluation.raw_json,
    )
    expected = (
        numeric_value,
        text_value,
        unit,
        out_of_domain,
        list(limitations),
        raw,
    )
    if observed != expected:
        raise ValueError("persisted v34 evaluation differs from retry payload")
    return evaluation


async def assert_v34_reveal_gate(
    session: AsyncSession, *, run_id: uuid.UUID, plan: Mapping[str, Any]
) -> None:
    expected = {
        episode["blinded_adjudication_tool_id"] for episode in plan["episodes"]
    }
    decisions = list(
        await session.scalars(
            select(AgentDecision).where(
                AgentDecision.run_id == run_id,
                AgentDecision.decision_type == "v34_blinded_adjudication",
            )
        )
    )
    observed = {
        str(item.structured_json.get("v34_logical_id"))
        for item in decisions
        if item.structured_json.get("locked_before_assignment_reveal") is True
    }
    if observed != expected or len(decisions) != len(expected):
        raise ValueError("v34 assignment reveal is blocked until all adjudications lock")


def _logical_replay_graph(
    plan: Mapping[str, Any], graph: Mapping[str, Any]
) -> dict[str, Any]:
    logical_by_call: dict[str, str] = {}
    tools: list[dict[str, Any]] = []
    for call in graph["tool_calls"]:
        logical_id = call["input_json"].get("v34_logical_id")
        if logical_id is None:
            continue
        logical_by_call[call["id"]] = str(logical_id)
        tools.append({"logical_id": str(logical_id), "status": call["status"]})
    dependencies = [
        {
            "parent_logical_id": logical_by_call[item["parent_tool_call_id"]],
            "child_logical_id": logical_by_call[item["child_tool_call_id"]],
        }
        for item in graph["tool_call_dependencies"]
        if item["parent_tool_call_id"] in logical_by_call
        and item["child_tool_call_id"] in logical_by_call
    ]
    artifact_by_id = {item["id"]: item for item in graph["artifacts"]}
    artifacts = [
        {
            "tool_call_logical_id": logical_by_call[item["tool_call_id"]],
            "role": item["role"],
            "sha256": artifact_by_id[item["artifact_id"]]["sha256"],
        }
        for item in graph["evidence_artifacts"]
        if item["tool_call_id"] in logical_by_call
    ]
    adjudications = [
        {
            "tool_call_logical_id": item["structured_json"].get("v34_logical_id"),
            "locked_before_assignment_reveal": item["structured_json"].get(
                "locked_before_assignment_reveal"
            ),
        }
        for item in graph["agent_decisions"]
        if item["decision_type"] == "v34_blinded_adjudication"
    ]
    return {
        "tool_calls": tools,
        "dependencies": dependencies,
        "artifacts": artifacts,
        "adjudications": adjudications,
    }


def verify_v34_database_object_replay(
    plan: Mapping[str, Any],
    graph: Mapping[str, Any],
    artifact_payloads_by_sha256: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    """Verify graph topology and every raw proposal occurrence from DB/object bytes."""
    logical = _logical_replay_graph(plan, graph)
    validate_v34_replay_graph(plan, logical)
    call_logical = {
        item["id"]: str(item["input_json"].get("v34_logical_id"))
        for item in graph["tool_calls"]
        if item["input_json"].get("v34_logical_id") is not None
    }
    artifacts = {item["id"]: item for item in graph["artifacts"]}
    occurrence_payload_by_call: dict[str, dict[str, Any]] = {}
    for link in graph["evidence_artifacts"]:
        if link["role"] != "proposal_occurrences":
            continue
        artifact = artifacts[link["artifact_id"]]
        payload = artifact_payloads_by_sha256.get(artifact["sha256"])
        if payload is None or sha256_json(payload) != artifact["sha256"]:
            raise ValueError("v34 proposal occurrence artifact is missing or corrupt")
        occurrence_payload_by_call[link["tool_call_id"]] = payload
    db_by_call: dict[str, list[dict[str, Any]]] = {}
    for item in graph["candidate_occurrences"]:
        db_by_call.setdefault(item["tool_call_id"], []).append(item)
    proposal_context = {
        episode["tool_calls"][2]["logical_id"]: (
            episode["parent_id"],
            episode["opaque_label"],
        )
        for episode in plan["episodes"]
    }
    for call_id, logical_id in call_logical.items():
        if logical_id not in proposal_context:
            continue
        payload = occurrence_payload_by_call.get(call_id)
        if payload is None:
            raise ValueError("v34 proposal call lacks occurrence artifact")
        expected = payload.get("occurrences", [])
        observed = sorted(db_by_call.get(call_id, []), key=lambda item: item["occurrence_rank"])
        if len(expected) != plan["raw_proposals_per_episode"]:
            raise ValueError("v34 proposal artifact differs from fixed budget")
        parent_id, opaque_label = proposal_context[logical_id]
        if any(
            item["parent_candidate_id"] != parent_id
            or item["opaque_arm_label"] != opaque_label
            for item in observed
        ):
            raise ValueError("v34 proposal occurrence parent or opaque label drifted")
        compact = [
            {
                "occurrence_rank": item["occurrence_rank"],
                "occurrence_kind": item["occurrence_kind"],
                "sequence": item["sequence"],
                "sequence_sha256": item["sequence_sha256"],
                "metadata": item["metadata"],
            }
            for item in observed
        ]
        if compact != expected:
            raise ValueError("v34 database proposal occurrences differ from artifact")
    result = {
        "schema_version": "1.0",
        "exact_replay": True,
        "plan_sha256": plan["plan_sha256"],
        "episode_count": plan["episode_count"],
        "tool_call_count": len(logical["tool_calls"]),
        "candidate_occurrence_count": len(graph["candidate_occurrences"]),
        "evidence_graph_sha256": graph["graph_sha256"],
    }
    result["replay_sha256"] = sha256_json(result)
    return result


async def build_v34_database_object_replay_bundle(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    plan: Mapping[str, Any],
    artifact_reader: ArtifactReader,
) -> dict[str, Any]:
    graph = await build_database_evidence_graph(session, run_id)
    payloads: dict[str, dict[str, Any]] = {}
    for artifact in graph["artifacts"]:
        raw = artifact_reader(artifact["storage_uri"])
        if sha256_bytes(raw) != artifact["sha256"]:
            raise OSError(f"v34 object-store checksum mismatch: {artifact['sha256']}")
        payloads[artifact["sha256"]] = json.loads(raw)
    replay = verify_v34_database_object_replay(plan, graph, payloads)
    return {"schema_version": "1.0", "evidence_graph": graph, "replay": replay}
