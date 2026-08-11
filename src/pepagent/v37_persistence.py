from __future__ import annotations

import math
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import (
    AgentDecision,
    Artifact,
    EvidenceArtifact,
    LifecycleEvent,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.provenance.hashing import sha256_json, sha256_text
from pepagent.v37_evidence import validate_v37_replay_graph

V37_EVIDENCE_VERSION = "v37.database-object-replay.1"
ArtifactWriter = Callable[[dict[str, Any]], Awaitable[Any]]

_COMMON_ROLES = ("attempt_ledger", "failure_ledger")
_GLOBAL_ROLES = {
    "knowledge": (
        "knowledge_evidence",
        "provider_release_receipt",
        "agent_decision",
        "stop_event",
    ),
    "stage1-shortlist": (
        "shortlist_manifest",
        "risk_exclusion_witness",
        "pareto_layers",
        "diversity_witness",
        "shortfall_witness",
        "agent_decision",
        "stop_event",
    ),
    "structure": (
        "pose_manifest",
        "coordinate_audit",
        "structure_inputs_outputs",
        "stop_event",
    ),
    "rosetta": ("decoy_manifest", "rosetta_inputs_outputs", "stop_event"),
    "pepshot": (
        "pepshot_evidence",
        "provider_release_receipt",
        "agent_decision",
        "stop_event",
    ),
    "final-portfolio": (
        "eligibility_and_exclusions",
        "final_portfolio",
        "pareto_layers",
        "diversity_witness",
        "shortfall_witness",
        "candidate_evidence_cards",
        "agent_decision",
        "stop_event",
    ),
    "replay": ("database_object_replay", "agent_decision", "stop_event"),
}


def build_v37_artifact_contract(plan: Mapping[str, Any]) -> dict[str, list[str]]:
    """Bind every logical stage to the minimum DB/object evidence needed for replay."""
    roles: dict[str, list[str]] = {}
    for item in plan["generator_calls"]:
        roles[item["logical_id"]] = [
            "raw_proposals",
            "proposal_occurrences",
            "retention_witness",
            "source_runtime_receipt",
            *_COMMON_ROLES,
        ]
    for item in plan["metric_calls"]:
        roles[item["logical_id"]] = [
            "evaluation_vector",
            "source_runtime_receipt",
            *_COMMON_ROLES,
        ]
    for item in plan["global_calls"]:
        stage = item["logical_id"].removeprefix("v37:")
        roles[item["logical_id"]] = [*_GLOBAL_ROLES[stage], *_COMMON_ROLES]
    if set(roles) != set(plan["required_tool_call_ids"]):
        raise ValueError("v37 artifact contract does not cover every frozen ToolCall")
    return roles


def _stored_payload(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        value = asdict(value)
    if not isinstance(value, Mapping):
        raise TypeError("v37 artifact writer must return a mapping or dataclass")
    required = {"sha256", "size_bytes", "media_type"}
    if not required.issubset(value) or not ({"uri", "storage_uri"} & set(value)):
        raise ValueError("v37 artifact writer returned incomplete identity")
    return {
        "sha256": str(value["sha256"]),
        "size_bytes": int(value["size_bytes"]),
        "media_type": str(value["media_type"]),
        "storage_uri": str(value.get("uri", value.get("storage_uri"))),
    }


async def persist_v37_tool_result(
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
    """Persist one v37 logical node with exact idempotent object links."""
    role_contract = build_v37_artifact_contract(plan)
    if logical_id not in role_contract:
        raise ValueError("unknown v37 logical ToolCall")
    if Counter(artifact_payloads_by_role) != Counter(role_contract[logical_id]):
        raise ValueError("v37 artifact roles differ from replay contract")
    repository = ExperimentRepository(session)
    call_input = {"v37_logical_id": logical_id, "payload": input_payload}
    call_parameters = {"v37_plan_sha256": plan["plan_sha256"], **parameters}
    call = await repository.record_completed_tool_call(
        run_id,
        next(
            item["tool_name"]
            for section in ("generator_calls", "metric_calls", "global_calls")
            for item in plan[section]
            if item["logical_id"] == logical_id
        ),
        V37_EVIDENCE_VERSION,
        environment_sha256,
        call_input,
        call_parameters,
        output_payload,
        model_uri=model_uri,
        weights_sha256=weights_sha256,
        random_seed=random_seed,
    )
    if (
        call.input_json != call_input
        or call.parameters_json != call_parameters
        or call.output_sha256 != sha256_json(output_payload)
    ):
        raise ValueError("persisted v37 ToolCall differs from retry payload")
    for role, payload in sorted(artifact_payloads_by_role.items()):
        stored = _stored_payload(await artifact_writer(payload))
        if stored["sha256"] != sha256_json(payload):
            raise ValueError("v37 object writer hash differs from canonical JSON")
        artifact = await session.scalar(
            select(Artifact).where(Artifact.sha256 == stored["sha256"])
        )
        if artifact is None:
            artifact = Artifact(
                sha256=stored["sha256"],
                size_bytes=stored["size_bytes"],
                media_type=stored["media_type"],
                storage_uri=stored["storage_uri"],
                metadata_json={"v37_role": role},
            )
            session.add(artifact)
            await session.flush()
        elif (
            artifact.size_bytes,
            artifact.media_type,
            artifact.storage_uri,
        ) != (stored["size_bytes"], stored["media_type"], stored["storage_uri"]):
            raise ValueError("v37 retry resolved an existing hash to different storage")
        conflicting = await session.scalar(
            select(EvidenceArtifact).where(
                EvidenceArtifact.tool_call_id == call.id,
                EvidenceArtifact.role == role,
                EvidenceArtifact.artifact_id != artifact.id,
            )
        )
        if conflicting is not None:
            raise ValueError("v37 retry changed an artifact role payload")
        link = await session.get(
            EvidenceArtifact,
            {"tool_call_id": call.id, "artifact_id": artifact.id, "role": role},
        )
        if link is None:
            session.add(
                EvidenceArtifact(tool_call_id=call.id, artifact_id=artifact.id, role=role)
            )
            await session.flush()
    return call


async def persist_v37_dependencies(
    session: AsyncSession, *, run_id: uuid.UUID, plan: Mapping[str, Any]
) -> None:
    calls = list(await session.scalars(select(ToolCall).where(ToolCall.run_id == run_id)))
    by_logical: dict[str, ToolCall] = {}
    for call in calls:
        logical_id = call.input_json.get("v37_logical_id")
        if logical_id is None:
            continue
        if logical_id in by_logical:
            raise ValueError("duplicate persisted v37 logical ToolCall")
        by_logical[str(logical_id)] = call
    if set(by_logical) != set(plan["required_tool_call_ids"]):
        raise ValueError("v37 dependencies require the complete ToolCall set")
    repository = ExperimentRepository(session)
    for parent, child in plan["dependencies"]:
        await repository.record_tool_dependency(
            by_logical[child].id,
            by_logical[parent].id,
            "v37_preregistered_dependency",
        )


async def persist_v37_agent_decision(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    logical_id: str,
    tool_call_id: uuid.UUID,
    observed_tool_call_ids: Sequence[uuid.UUID],
    prompt_text: str,
    response_text: str,
    structured: dict[str, Any],
) -> AgentDecision:
    """Persist one stage decision and restore every edge on an exact retry."""
    payload = {**structured, "v37_logical_id": logical_id}
    existing = await session.scalar(
        select(AgentDecision).where(
            AgentDecision.run_id == run_id,
            AgentDecision.decision_type == "v37_stage_decision",
            AgentDecision.structured_json["v37_logical_id"].astext == logical_id,
        )
    )
    repository = ExperimentRepository(session)
    if existing is None:
        existing = await repository.record_agent_decision(
            run_id,
            0,
            "v37_stage_decision",
            "v37-champion-agent",
            V37_EVIDENCE_VERSION,
            prompt_text,
            response_text,
            payload,
        )
    elif (
        existing.prompt_text != prompt_text
        or existing.response_text != response_text
        or existing.structured_json != payload
    ):
        raise ValueError("persisted v37 AgentDecision differs from retry payload")
    for observed_id in sorted(set(observed_tool_call_ids), key=str):
        await repository.record_agent_tool_edge(
            existing.id, observed_id, "input", "observes_v37_stage_evidence"
        )
    await repository.record_agent_tool_edge(
        existing.id, tool_call_id, "output", "materializes_v37_stage_decision"
    )
    return existing


async def persist_v37_proposal_events(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    logical_id: str,
    occurrences: Sequence[Mapping[str, Any]],
    expected_count: int,
) -> list[LifecycleEvent]:
    """Persist de-novo raw occurrences without inventing a parent Candidate row."""
    if len(occurrences) != expected_count:
        raise ValueError("v37 raw proposal count differs from fixed budget")
    repository = ExperimentRepository(session)
    rows: list[LifecycleEvent] = []
    for rank, item in enumerate(occurrences, start=1):
        if int(item.get("raw_rank", rank)) != rank:
            raise ValueError("v37 raw proposal ranks must be contiguous")
        payload = {
            "v37_logical_id": logical_id,
            "raw_rank": rank,
            "sequence": item.get("sequence"),
            "sequence_sha256": item.get("sequence_sha256"),
            "valid": bool(item["valid"]),
            "duplicate": bool(item["duplicate"]),
            "retained": bool(item["retained"]),
            "candidate_id": str(item["candidate_id"]) if item.get("candidate_id") else None,
            "reason": item.get("reason"),
        }
        existing = await session.scalar(
            select(LifecycleEvent).where(
                LifecycleEvent.aggregate_type == "run",
                LifecycleEvent.aggregate_id == run_id,
                LifecycleEvent.event_type == "v37.proposal_occurrence",
                LifecycleEvent.payload_json["v37_logical_id"].astext == logical_id,
                LifecycleEvent.payload_json["raw_rank"].astext == str(rank),
            )
        )
        if existing is not None:
            if existing.payload_json != payload:
                raise ValueError("persisted v37 proposal event differs from retry payload")
            rows.append(existing)
            continue
        rows.append(
            await repository.append_event(
                "run", run_id, "v37.proposal_occurrence", "v37-generator", payload
            )
        )
    return rows


def _artifact_payloads(
    graph: Mapping[str, Any], payloads_by_sha256: Mapping[str, dict[str, Any]]
) -> dict[tuple[str, str], dict[str, Any]]:
    call_logical = {
        str(item["id"]): str(item["input_json"]["v37_logical_id"])
        for item in graph["tool_calls"]
    }
    artifacts = {str(item["id"]): item for item in graph["artifacts"]}
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for link in graph["evidence_artifacts"]:
        artifact = artifacts[str(link["artifact_id"])]
        payload = payloads_by_sha256.get(str(artifact["sha256"]))
        if payload is None or sha256_json(payload) != artifact["sha256"]:
            raise ValueError("v37 object artifact is missing or corrupt")
        key = (call_logical[str(link["tool_call_id"])], str(link["role"]))
        if key in result:
            raise ValueError("v37 object artifact role is duplicated")
        result[key] = payload
    return result


def _logical_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
    logical_by_call = {
        str(item["id"]): str(item["input_json"]["v37_logical_id"])
        for item in graph.get("tool_calls", [])
        if "v37_logical_id" in item.get("input_json", {})
    }
    return {
        "tool_calls": list(graph.get("tool_calls", [])),
        "logical_dependencies": [
            {
                "parent_logical_id": logical_by_call[str(item["parent_tool_call_id"])],
                "child_logical_id": logical_by_call[str(item["child_tool_call_id"])],
            }
            for item in graph.get("tool_call_dependencies", [])
        ],
    }


def validate_v37_database_object_replay(
    *,
    manifest: Mapping[str, Any],
    plan: Mapping[str, Any],
    graph: Mapping[str, Any],
    artifact_payloads_by_sha256: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    """Fail closed unless the DB/object graph reconstructs every v37 decision input."""
    validate_v37_replay_graph(_logical_graph(graph), plan)
    calls = {
        str(item["input_json"]["v37_logical_id"]): item for item in graph["tool_calls"]
    }
    if len(calls) != len(graph["tool_calls"]) or any(
        item.get("status") != "succeeded" for item in calls.values()
    ):
        raise ValueError("v37 replay has duplicate or non-succeeded ToolCalls")
    role_contract = build_v37_artifact_contract(plan)
    observed_roles: dict[str, list[str]] = {}
    call_id_to_logical = {str(item["id"]): logical for logical, item in calls.items()}
    for link in graph.get("evidence_artifacts", []):
        observed_roles.setdefault(call_id_to_logical[str(link["tool_call_id"])], []).append(
            str(link["role"])
        )
    for logical_id, roles in role_contract.items():
        if Counter(observed_roles.get(logical_id, [])) != Counter(roles):
            raise ValueError(f"v37 artifact roles drifted for {logical_id}")
    payloads = _artifact_payloads(graph, artifact_payloads_by_sha256)

    candidates = {str(item["id"]): item for item in graph.get("candidates", [])}
    expected_candidate_ids: set[str] = set()
    proposal_event_rows = [
        item
        for item in graph.get("lifecycle_events", [])
        if item["event_type"] == "v37.proposal_occurrence"
    ]
    events_by_logical: dict[str, list[dict[str, Any]]] = {}
    for event in proposal_event_rows:
        events_by_logical.setdefault(
            str(event["payload_json"]["v37_logical_id"]), []
        ).append(event["payload_json"])
    raw_budget = int(manifest["generators"]["raw_proposals_per_generator_seed"])
    retain_budget = int(
        manifest["generators"]["evaluated_valid_unique_per_generator_seed"]
    )
    for generator in plan["generator_calls"]:
        logical_id = generator["logical_id"]
        occurrence_artifact = payloads[(logical_id, "proposal_occurrences")]
        expected = occurrence_artifact.get("occurrences")
        if occurrence_artifact.get("schema_version") != "1.0" or not isinstance(
            expected, list
        ):
            raise ValueError("v37 proposal occurrence artifact schema drifted")
        observed = sorted(
            events_by_logical.get(logical_id, []), key=lambda item: int(item["raw_rank"])
        )
        if len(expected) != raw_budget or observed != expected:
            raise ValueError("v37 proposal events differ from object occurrence evidence")
        retained = [item for item in observed if item["retained"]]
        if len(retained) > retain_budget:
            raise ValueError("v37 retained proposals exceed the frozen per-seed budget")
        for item in retained:
            candidate_id = item.get("candidate_id")
            if candidate_id is None or candidate_id in expected_candidate_ids:
                raise ValueError("v37 retained proposal candidate identity is missing or reused")
            candidate = candidates.get(candidate_id)
            if candidate is None or candidate["sequence_sha256"] != item["sequence_sha256"]:
                raise ValueError("v37 retained proposal differs from Candidate evidence")
            expected_candidate_ids.add(candidate_id)
    if set(candidates) != expected_candidate_ids:
        raise ValueError("v37 Candidate set is not exactly the retained proposal set")

    evaluations_by_call: dict[str, list[dict[str, Any]]] = {}
    for evaluation in graph.get("evaluations", []):
        evaluations_by_call.setdefault(str(evaluation["tool_call_id"]), []).append(evaluation)
    for metric in plan["metric_calls"]:
        logical_id = metric["logical_id"]
        call_id = str(calls[logical_id]["id"])
        expected = payloads[(logical_id, "evaluation_vector")].get("evaluations")
        observed = sorted(
            evaluations_by_call.get(call_id, []), key=lambda item: item["candidate_id"]
        )
        compact = [
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
            for item in observed
        ]
        if not isinstance(expected, list) or compact != expected:
            raise ValueError("v37 Evaluation rows differ from object metric evidence")
        if {item["candidate_id"] for item in observed} != expected_candidate_ids or any(
            item["metric_name"] != metric["metric_name"] for item in observed
        ):
            raise ValueError("v37 metric coverage or candidate join is incomplete")

    shortlist = payloads[("v37:stage1-shortlist", "shortlist_manifest")]
    shortlist_ids = list(shortlist.get("candidate_ids", []))
    if len(shortlist_ids) != len(set(shortlist_ids)) or not set(shortlist_ids).issubset(
        expected_candidate_ids
    ):
        raise ValueError("v37 shortlist identity set is invalid")
    if len(shortlist_ids) > int(plan["expected_structure_shortlist"]):
        raise ValueError("v37 shortlist exceeds its frozen quota")

    pose_manifest = payloads[("v37:structure", "pose_manifest")]
    poses = pose_manifest.get("poses")
    if not isinstance(poses, list):
        raise ValueError("v37 pose manifest schema drifted")
    pose_ids: set[str] = set()
    pose_counts: Counter[str] = Counter()
    for pose in poses:
        candidate_id = str(pose["candidate_id"])
        pose_id = str(pose["pose_id"])
        if candidate_id not in shortlist_ids or pose_id in pose_ids:
            raise ValueError("v37 structure pose identity mapping is invalid")
        if not all(
            isinstance(pose.get(field), str) and len(pose[field]) == 64
            for field in ("structure_sha256", "coordinate_audit_sha256")
        ):
            raise ValueError("v37 structure pose lacks exact coordinate evidence")
        pose_ids.add(pose_id)
        pose_counts[candidate_id] += 1
    expected_poses = int(manifest["stage_2_structure_confirmation"]["poses_per_candidate"])
    if any(pose_counts[item] != expected_poses for item in shortlist_ids):
        raise ValueError("v37 structure pose coverage differs from frozen protocol")

    decoys = payloads[("v37:rosetta", "decoy_manifest")].get("decoys")
    if not isinstance(decoys, list):
        raise ValueError("v37 Rosetta decoy manifest schema drifted")
    decoy_counts: Counter[str] = Counter()
    decoy_ids: set[str] = set()
    for decoy in decoys:
        pose_id = str(decoy["pose_id"])
        decoy_id = str(decoy["decoy_id"])
        score = float(decoy["interface_delta_g_reu"])
        if pose_id not in pose_ids or decoy_id in decoy_ids or not math.isfinite(score):
            raise ValueError("v37 Rosetta decoy identity or score is invalid")
        decoy_ids.add(decoy_id)
        decoy_counts[pose_id] += 1
    expected_decoys = int(
        manifest["stage_2_structure_confirmation"]["rosetta_decoys_per_pose"]
    )
    if any(decoy_counts[item] != expected_decoys for item in pose_ids):
        raise ValueError("v37 Rosetta decoy coverage differs from frozen protocol")

    pepshot = payloads[("v37:pepshot", "pepshot_evidence")]
    reviews = pepshot.get("reviews")
    if not isinstance(reviews, list) or {str(item["candidate_id"]) for item in reviews} != set(
        shortlist_ids
    ):
        raise ValueError("v37 PepShot candidate review coverage is incomplete")
    required_pepshot = {
        "candidate_id",
        "pose_id",
        "request_sha256",
        "bundle_sha256",
        "image_manifest_sha256",
        "read_order_sha256",
        "review_sha256",
        "validation_sha256",
        "status",
    }
    for review in reviews:
        if not required_pepshot.issubset(review) or review["pose_id"] not in pose_ids:
            raise ValueError("v37 PepShot review evidence is incomplete")
        if review["status"] not in {"reviewed", "insufficient_evidence"}:
            raise ValueError("v37 PepShot review status is invalid")

    decision_logicals = {
        "v37:knowledge",
        "v37:stage1-shortlist",
        "v37:pepshot",
        "v37:final-portfolio",
        "v37:replay",
    }
    decisions = {
        str(item["structured_json"].get("v37_logical_id")): item
        for item in graph.get("agent_decisions", [])
        if item["decision_type"] == "v37_stage_decision"
    }
    if set(decisions) != decision_logicals or any(
        item.get("status") != "succeeded" for item in decisions.values()
    ):
        raise ValueError("v37 AgentDecision set is incomplete")
    decision_edges = graph.get("agent_decision_tool_call_edges", [])
    edges_by_decision: dict[str, list[dict[str, Any]]] = {}
    for edge in decision_edges:
        edges_by_decision.setdefault(str(edge["decision_id"]), []).append(edge)
    for logical_id, decision in decisions.items():
        output_call_id = str(calls[logical_id]["id"])
        edges = edges_by_decision.get(str(decision["id"]), [])
        outputs = {
            str(item["tool_call_id"])
            for item in edges
            if item["direction"] == "output"
            and item["relation_type"] == "materializes_v37_stage_decision"
        }
        inputs = [
            item
            for item in edges
            if item["direction"] == "input"
            and item["relation_type"] == "observes_v37_stage_evidence"
        ]
        if outputs != {output_call_id} or not inputs:
            raise ValueError("v37 AgentDecision evidence edges are incomplete")

    stopped = {
        str(item["payload_json"].get("v37_logical_id"))
        for item in graph.get("lifecycle_events", [])
        if item["event_type"] == "v37.stage_stopped"
        and item["payload_json"].get("stop_reason")
    }
    expected_stops = {f"v37:{stage}" for stage in _GLOBAL_ROLES}
    if stopped != expected_stops:
        raise ValueError("v37 stop/failure evidence is incomplete")

    final_portfolio = payloads[("v37:final-portfolio", "final_portfolio")]
    final_ids = list(final_portfolio.get("candidate_ids", []))
    quota = int(manifest["final_portfolio"]["total_quota"])
    if len(final_ids) != len(set(final_ids)) or len(final_ids) > quota:
        raise ValueError("v37 final portfolio identity or quota is invalid")
    if not set(final_ids).issubset(set(shortlist_ids)):
        raise ValueError("v37 final portfolio includes a non-shortlisted candidate")

    result = {
        "schema_version": "1.0",
        "exact_replay": True,
        "plan_sha256": plan["plan_sha256"],
        "candidate_count": len(candidates),
        "raw_proposal_occurrence_count": len(proposal_event_rows),
        "shortlist_count": len(shortlist_ids),
        "pose_count": len(poses),
        "rosetta_decoy_count": len(decoys),
        "final_portfolio_count": len(final_ids),
        "agent_decision_count": len(decisions),
    }
    result["replay_sha256"] = sha256_json(result)
    return result


def proposal_occurrence_payload(
    *,
    logical_id: str,
    raw_rank: int,
    sequence: str | None,
    valid: bool,
    duplicate: bool,
    retained: bool,
    candidate_id: str | None,
    reason: str | None,
) -> dict[str, Any]:
    normalized = "".join((sequence or "").split()).upper() or None
    return {
        "v37_logical_id": logical_id,
        "raw_rank": raw_rank,
        "sequence": normalized,
        "sequence_sha256": sha256_text(normalized) if normalized else None,
        "valid": valid,
        "duplicate": duplicate,
        "retained": retained,
        "candidate_id": candidate_id,
        "reason": reason,
    }
