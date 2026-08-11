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
    ExperimentRun,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.evidence_replay import build_database_evidence_graph
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.v34_evidence import (
    V34_EVIDENCE_VERSION,
    validate_v34_provider_change_request_ledger,
    validate_v34_replay_graph,
)

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
    observed_tool_call_id: uuid.UUID,
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
            existing.id, observed_tool_call_id, "input", "observes_holdout_evaluation"
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
    await repository.record_agent_tool_edge(
        existing.id, observed_tool_call_id, "input", "observes_holdout_evaluation"
    )
    await repository.record_agent_tool_edge(
        existing.id, tool_call_id, "output", "materializes_blinded_adjudication"
    )
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


async def persist_v34_final_decision(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    tool_call_id: uuid.UUID,
    observed_tool_call_ids: Sequence[uuid.UUID],
    prompt_text: str,
    response_text: str,
    structured: dict[str, Any],
) -> AgentDecision:
    """Persist the single promotion verdict and its complete preregistered inputs."""
    logical_id = "v34-global:v34-factorial-analysis"
    payload = {**structured, "v34_logical_id": logical_id}
    existing = await session.scalar(
        select(AgentDecision).where(
            AgentDecision.run_id == run_id,
            AgentDecision.decision_type == "v34_factorial_promotion",
            AgentDecision.structured_json["v34_logical_id"].astext == logical_id,
        )
    )
    repository = ExperimentRepository(session)
    if existing is None:
        existing = await repository.record_agent_decision(
            run_id,
            0,
            "v34_factorial_promotion",
            "v34-factorial-adjudicator",
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
        raise ValueError("persisted v34 promotion verdict differs from retry payload")
    for observed_id in sorted(set(observed_tool_call_ids), key=str):
        await repository.record_agent_tool_edge(
            existing.id, observed_id, "input", "observes_locked_factorial_evidence"
        )
    await repository.record_agent_tool_edge(
        existing.id, tool_call_id, "output", "materializes_promotion_verdict"
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
    call_id_by_logical = {logical_id: call_id for call_id, logical_id in call_logical.items()}
    governance_payloads: list[dict[str, Any]] = []
    for link in graph["evidence_artifacts"]:
        if link["role"] != "provider_change_request_ledger":
            continue
        artifact = artifacts[link["artifact_id"]]
        payload = artifact_payloads_by_sha256.get(artifact["sha256"])
        if payload is None or sha256_json(payload) != artifact["sha256"]:
            raise ValueError("v34 provider governance artifact is missing or corrupt")
        governance_payloads.append(payload)
    if len(governance_payloads) != 1:
        raise ValueError("v34 replay requires one provider change-request ledger")
    validate_v34_provider_change_request_ledger(
        plan["provider_governance_contract"], governance_payloads[0]
    )
    expected_parent_rows = [
        {
            "id": str(episode_parent["candidate_id"]),
            "sequence_sha256": str(episode_parent["sequence_sha256"]),
        }
        for episode_parent in sorted(
            {
                episode["parent_id"]: {
                    "candidate_id": episode["parent_id"],
                    "sequence_sha256": episode["parent_sequence_sha256"],
                    "order": episode["parent_order"],
                }
                for episode in plan["episodes"]
            }.values(),
            key=lambda item: item["order"],
        )
    ]
    observed_parent_rows = [
        {
            "id": str(item["id"]),
            "sequence_sha256": str(item["sequence_sha256"]),
        }
        for item in graph.get("v34_parent_candidates", [])
    ]
    if observed_parent_rows != expected_parent_rows:
        raise ValueError("v34 replay parent identity or order drifted")

    requests = governance_payloads[0]["change_requests"]
    expected_lineage = [
        {
            "request_id": str(item["request_id"]),
            "rejecting_run_id": str(item["rejecting_run_id"]),
            "change_request_run_id": str(item["change_request_run_id"]),
        }
        for item in requests
    ]
    observed_lineage = [
        {
            "request_id": str(item["request_id"]),
            "rejecting_run_id": str(item["rejecting_run_id"]),
            "change_request_run_id": str(item["change_request_run_id"]),
        }
        for item in graph.get("provider_governance_lineage", [])
        if item.get("parentage_verified") is True
    ]
    if observed_lineage != expected_lineage:
        raise ValueError("v34 provider child-run lineage is missing or drifted")
    governance_hash_fields = (
        "reproducible_input_artifact_sha256",
        "violated_contract_artifact_sha256",
        "acceptance_criteria_artifact_sha256",
        "external_request_receipt_artifact_sha256",
        "replacement_release_manifest_sha256",
        "read_only_acceptance_receipt_artifact_sha256",
    )
    expected_governance_hashes = {
        str(item[field])
        for item in requests
        for field in governance_hash_fields
        if item.get(field)
    }
    observed_governance_hashes = {
        str(item["sha256"])
        for item in graph.get("provider_governance_artifacts", [])
        if item.get("content_verified") is True
    }
    if observed_governance_hashes != expected_governance_hashes:
        raise ValueError("v34 provider referenced artifacts are missing or corrupt")
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
                "candidate_id": item.get("candidate_id"),
                "metadata": item["metadata"],
            }
            for item in observed
        ]
        if compact != expected:
            raise ValueError("v34 database proposal occurrences differ from artifact")

    expected_decisions: dict[tuple[str, str], tuple[set[str], str, str]] = {}
    for episode in plan["episodes"]:
        decision_logical = episode["intervention_decision_tool_id"]
        adjudication_logical = episode["blinded_adjudication_tool_id"]
        direct_inputs = {
            str(parent)
            for parent, child in episode["dependencies"]
            if child == decision_logical
        }
        evaluation_logical = next(
            tool["logical_id"]
            for tool in episode["tool_calls"]
            if tool["tool_name"] == "v34-independent-evaluation"
        )
        expected_decisions[("v34_intervention", decision_logical)] = (
            direct_inputs,
            "materializes_intervention_decision",
            "observes_episode_evidence",
        )
        expected_decisions[("v34_blinded_adjudication", adjudication_logical)] = (
            {evaluation_logical},
            "materializes_blinded_adjudication",
            "observes_holdout_evaluation",
        )
    analysis_logical = "v34-global:v34-factorial-analysis"
    analysis_inputs = {
        str(parent)
        for parent, child in plan["required_dependencies"]
        if child == analysis_logical
    }
    expected_decisions[("v34_factorial_promotion", analysis_logical)] = (
        analysis_inputs,
        "materializes_promotion_verdict",
        "observes_locked_factorial_evidence",
    )
    decisions_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for decision in graph.get("agent_decisions", []):
        logical_id = decision.get("structured_json", {}).get("v34_logical_id")
        key = (str(decision.get("decision_type")), str(logical_id))
        if key not in expected_decisions:
            raise ValueError("v34 replay contains an unexpected AgentDecision")
        if key in decisions_by_key:
            raise ValueError("v34 replay contains duplicate AgentDecision identities")
        if decision.get("status") != "succeeded":
            raise ValueError("v34 replay contains a non-succeeded AgentDecision")
        decisions_by_key[key] = decision
    if set(decisions_by_key) != set(expected_decisions):
        raise ValueError("v34 replay AgentDecision set is incomplete")

    edges_by_decision: dict[str, list[dict[str, Any]]] = {}
    for edge in graph.get("agent_decision_tool_call_edges", []):
        edges_by_decision.setdefault(str(edge["decision_id"]), []).append(edge)
    for key, decision in decisions_by_key.items():
        _, logical_id = key
        input_logicals, output_relation, input_relation = expected_decisions[key]
        observed_edges = edges_by_decision.get(str(decision["id"]), [])
        observed_inputs = {
            call_logical[str(edge["tool_call_id"])]
            for edge in observed_edges
            if edge["direction"] == "input" and edge["relation_type"] == input_relation
        }
        expected_output_call = call_id_by_logical[logical_id]
        observed_outputs = {
            str(edge["tool_call_id"])
            for edge in observed_edges
            if edge["direction"] == "output"
            and edge["relation_type"] == output_relation
        }
        expected_edge_keys = {
            (call_id_by_logical[item], "input", input_relation) for item in input_logicals
        }
        expected_edge_keys.add((expected_output_call, "output", output_relation))
        observed_edge_keys = {
            (
                str(edge["tool_call_id"]),
                str(edge["direction"]),
                str(edge["relation_type"]),
            )
            for edge in observed_edges
        }
        if (
            observed_inputs != input_logicals
            or observed_outputs != {expected_output_call}
            or observed_edge_keys != expected_edge_keys
        ):
            raise ValueError("v34 replay AgentDecision evidence edges drifted")

    evaluation_payload_by_call: dict[str, dict[str, Any]] = {}
    for link in graph["evidence_artifacts"]:
        if link["role"] != "holdout_endpoint_vector":
            continue
        artifact = artifacts[link["artifact_id"]]
        payload = artifact_payloads_by_sha256.get(artifact["sha256"])
        if payload is None or sha256_json(payload) != artifact["sha256"]:
            raise ValueError("v34 holdout endpoint artifact is missing or corrupt")
        evaluation_payload_by_call[str(link["tool_call_id"])] = payload
    db_evaluations_by_call: dict[str, list[dict[str, Any]]] = {}
    for item in graph.get("evaluations", []):
        db_evaluations_by_call.setdefault(str(item["tool_call_id"]), []).append(item)
    evaluation_logicals = {
        tool["logical_id"]
        for episode in plan["episodes"]
        for tool in episode["tool_calls"]
        if tool["tool_name"] == "v34-independent-evaluation"
    }
    for logical_id in evaluation_logicals:
        call_id = call_id_by_logical[logical_id]
        payload = evaluation_payload_by_call.get(call_id)
        if payload is None or payload.get("schema_version") != "1.0":
            raise ValueError("v34 replay lacks a typed holdout endpoint vector")
        expected_rows = payload.get("evaluations")
        if not isinstance(expected_rows, list):
            raise ValueError("v34 holdout endpoint vector lacks an evaluation list")
        observed_rows = [
            {
                "candidate_id": item["candidate_id"],
                "metric_name": item["metric_name"],
                "numeric_value": item["numeric_value"],
                "text_value": item["text_value"],
                "unit": item["unit"],
                "out_of_domain": item["out_of_domain"],
                "limitations": item["limitations"],
                "raw": item["raw"],
            }
            for item in sorted(
                db_evaluations_by_call.get(call_id, []),
                key=lambda value: (value["candidate_id"], value["metric_name"]),
            )
        ]
        if observed_rows != expected_rows:
            raise ValueError("v34 database evaluations differ from holdout artifact")
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
    parent_order = {
        str(episode["parent_id"]): int(episode["parent_order"])
        for episode in plan["episodes"]
    }
    parent_ids = [uuid.UUID(item) for item in parent_order]
    parent_candidates = list(
        await session.scalars(select(Candidate).where(Candidate.id.in_(parent_ids)))
    )
    graph["v34_parent_candidates"] = [
        {
            "id": str(item.id),
            "run_id": str(item.run_id),
            "sequence_sha256": item.sequence_sha256,
        }
        for item in sorted(
            parent_candidates, key=lambda item: parent_order[str(item.id)]
        )
    ]
    governance_link = next(
        (
            item
            for item in graph["evidence_artifacts"]
            if item["role"] == "provider_change_request_ledger"
        ),
        None,
    )
    requests: list[dict[str, Any]] = []
    if governance_link is not None:
        artifact_by_id = {item["id"]: item for item in graph["artifacts"]}
        ledger_artifact = artifact_by_id[governance_link["artifact_id"]]
        requests = list(payloads[ledger_artifact["sha256"]].get("change_requests", []))
    lineage: list[dict[str, Any]] = []
    referenced_hashes: set[str] = set()
    governance_hash_fields = (
        "reproducible_input_artifact_sha256",
        "violated_contract_artifact_sha256",
        "acceptance_criteria_artifact_sha256",
        "external_request_receipt_artifact_sha256",
        "replacement_release_manifest_sha256",
        "read_only_acceptance_receipt_artifact_sha256",
    )
    for request in requests:
        rejecting = await session.get(ExperimentRun, uuid.UUID(request["rejecting_run_id"]))
        change = await session.get(
            ExperimentRun, uuid.UUID(request["change_request_run_id"])
        )
        parentage_verified = (
            rejecting is not None
            and change is not None
            and change.parent_run_id == rejecting.id
        )
        lineage.append(
            {
                "request_id": request["request_id"],
                "rejecting_run_id": request["rejecting_run_id"],
                "change_request_run_id": request["change_request_run_id"],
                "parentage_verified": parentage_verified,
            }
        )
        referenced_hashes.update(
            str(request[field])
            for field in governance_hash_fields
            if request.get(field)
        )
    referenced_artifacts = list(
        await session.scalars(select(Artifact).where(Artifact.sha256.in_(referenced_hashes)))
    )
    governance_artifacts: list[dict[str, Any]] = []
    for artifact in sorted(referenced_artifacts, key=lambda item: item.sha256):
        raw = artifact_reader(artifact.storage_uri)
        governance_artifacts.append(
            {
                "sha256": artifact.sha256,
                "storage_uri": artifact.storage_uri,
                "content_verified": sha256_bytes(raw) == artifact.sha256,
            }
        )
    graph["provider_governance_lineage"] = lineage
    graph["provider_governance_artifacts"] = governance_artifacts
    graph["graph_sha256"] = sha256_json(
        {key: value for key, value in graph.items() if key != "graph_sha256"}
    )
    replay = verify_v34_database_object_replay(plan, graph, payloads)
    return {"schema_version": "1.0", "evidence_graph": graph, "replay": replay}
