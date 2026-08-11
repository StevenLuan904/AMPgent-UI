from __future__ import annotations

import json
import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, is_dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.charge_design import CounterfactualCohortResult
from pepagent.db.models import (
    AgentDecision,
    Artifact,
    Candidate,
    EvidenceArtifact,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.evidence_replay import build_database_evidence_graph
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.search_sufficiency import ArchiveSnapshot, SaturationAssessment

V33_EVIDENCE_VERSION = "v33-database-evidence-v1"
ArtifactWriter = Callable[[dict[str, Any]], Awaitable[Any]]
ArtifactReader = Callable[[str], bytes]


def _stored_payload(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        value = asdict(value)
    if not isinstance(value, dict):
        raise TypeError("artifact writer must return a mapping or dataclass")
    required = {"sha256", "size_bytes", "media_type"}
    if not required.issubset(value) or not ({"uri", "storage_uri"} & set(value)):
        raise ValueError("artifact writer returned incomplete content-addressed identity")
    return {
        "sha256": str(value["sha256"]),
        "size_bytes": int(value["size_bytes"]),
        "media_type": str(value["media_type"]),
        "uri": str(value.get("uri", value.get("storage_uri"))),
    }


async def _register_json_artifact(
    session: AsyncSession,
    tool_call_id: uuid.UUID,
    payload: dict[str, Any],
    role: str,
    metadata: dict[str, Any],
    artifact_writer: ArtifactWriter,
) -> Artifact:
    stored = _stored_payload(await artifact_writer(payload))
    artifact = await session.scalar(select(Artifact).where(Artifact.sha256 == stored["sha256"]))
    if artifact is None:
        artifact = Artifact(
            sha256=stored["sha256"],
            size_bytes=stored["size_bytes"],
            media_type=stored["media_type"],
            storage_uri=stored["uri"],
            metadata_json=metadata,
        )
        session.add(artifact)
        await session.flush()
    link = await session.get(
        EvidenceArtifact,
        {"tool_call_id": tool_call_id, "artifact_id": artifact.id, "role": role},
    )
    if link is None:
        session.add(
            EvidenceArtifact(tool_call_id=tool_call_id, artifact_id=artifact.id, role=role)
        )
    return artifact


def build_v33_charge_persistence_plan(
    cohort: CounterfactualCohortResult,
) -> dict[str, Any]:
    """Create the exact parent/child/evaluation plan before any database mutation."""
    candidates: list[dict[str, Any]] = []
    evaluations: list[dict[str, Any]] = []
    seen_sequences: dict[str, str] = {}
    arm_order = (
        "baseline_unedited",
        "lysine_one",
        "arginine_one",
        "one_charge_preserving_control",
        "lysine_two",
        "arginine_two",
        "two_charge_preserving_control",
    )
    for parent_rank, parent in enumerate(cohort.selected_parents, start=1):
        arms = {parent.baseline.arm: parent.baseline}
        for block in parent.dose_blocks.values():
            if not block.reachable:
                raise ValueError("selected v33 parent contains an unreachable dose block")
            assert block.lysine_arm and block.arginine_arm and block.control_arm
            arms.update(
                {
                    block.lysine_arm.arm: block.lysine_arm,
                    block.arginine_arm.arm: block.arginine_arm,
                    block.control_arm.arm: block.control_arm,
                }
            )
        if set(arms) != set(arm_order):
            raise ValueError(f"v33 seven-arm identity drift for parent {parent.parent_id}")
        for arm_rank, arm_name in enumerate(arm_order):
            arm = arms[arm_name]
            prior = seen_sequences.get(arm.sequence_sha256)
            logical_id = f"{parent.parent_id}:{arm_name}"
            if prior is not None and prior != logical_id:
                raise ValueError(f"cross-arm sequence collision: {prior} and {logical_id}")
            seen_sequences[arm.sequence_sha256] = logical_id
            candidate = {
                "logical_id": logical_id,
                "parent_candidate_id": parent.parent_id,
                "is_baseline_parent": arm_name == "baseline_unedited",
                "parent_rank": parent_rank,
                "arm_rank": arm_rank,
                "arm": arm_name,
                "sequence": arm.sequence,
                "sequence_sha256": arm.sequence_sha256,
                "edit_positions_zero_based": arm.edit_positions_zero_based,
                "substitutions": arm.substitutions,
                "edit_count": arm.edit_count,
            }
            candidates.append(candidate)
            for metric_name, value in sorted(arm.metrics.items()):
                evaluations.append(
                    {
                        "logical_id": logical_id,
                        "metric_name": metric_name,
                        "numeric_value": float(value),
                        "unit": "descriptor",
                    }
                )
    plan = {
        "schema_version": "1.0",
        "evidence_version": V33_EVIDENCE_VERSION,
        "cohort_sha256": sha256_json(cohort.model_dump(mode="json")),
        "parent_order": [item.parent_id for item in cohort.selected_parents],
        "candidate_records": candidates,
        "descriptor_evaluations": evaluations,
        "rejections": [item.model_dump(mode="json") for item in cohort.rejections],
    }
    plan["plan_sha256"] = sha256_json(plan)
    return plan


def recover_v33_transform_identity(
    plan: dict[str, Any], persisted_candidates: list[dict[str, Any]]
) -> dict[str, str]:
    """Recover an exact lost-response result without advancing or duplicating the stream."""
    by_id = {str(item["id"]): item for item in persisted_candidates}
    if len(by_id) != len(persisted_candidates):
        raise ValueError("duplicate persisted v33 candidate identity")
    children: dict[tuple[str, str], dict[str, Any]] = {}
    for item in persisted_candidates:
        parent_id = item.get("parent_id")
        if parent_id is None:
            continue
        key = (str(item["sequence_sha256"]), str(parent_id))
        if key in children:
            raise ValueError("duplicate persisted v33 child provenance")
        children[key] = item
    recovered: dict[str, str] = {}
    for expected in plan["candidate_records"]:
        parent_id = expected["parent_candidate_id"]
        candidate = (
            by_id.get(parent_id)
            if expected["is_baseline_parent"]
            else children.get((expected["sequence_sha256"], parent_id))
        )
        if candidate is None or candidate["sequence_sha256"] != expected["sequence_sha256"]:
            raise ValueError(f"persisted v33 transform is incomplete: {expected['logical_id']}")
        recovered[expected["logical_id"]] = str(candidate["id"])
    if len(set(recovered.values())) != len(recovered):
        raise ValueError("one persisted candidate cannot satisfy multiple v33 arms")
    return recovered


async def persist_v33_charge_evidence(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    cohort: CounterfactualCohortResult,
    manifest_payload: dict[str, Any],
    literature_basis: dict[str, Any],
    environment_sha256: str,
    artifact_writer: ArtifactWriter,
) -> dict[str, Any]:
    """Persist one retry-safe v33 transform episode; this is not a registered activity."""
    repository = ExperimentRepository(session)
    plan = build_v33_charge_persistence_plan(cohort)
    existing = await session.scalar(
        select(ToolCall).where(
            ToolCall.run_id == run_id,
            ToolCall.tool_name == "v33-matched-charge-transformer",
        )
    )
    if existing is not None:
        if existing.output_sha256 != sha256_json(plan):
            raise ValueError("persisted v33 transform differs from retry payload")
        return await _recover_transform(session, run_id, existing, plan)

    parent_ids = [uuid.UUID(value) for value in plan["parent_order"]]
    parents = list(
        await session.scalars(
            select(Candidate)
            .where(Candidate.run_id == run_id, Candidate.id.in_(parent_ids))
            .order_by(Candidate.proposal_rank, Candidate.id)
        )
    )
    parent_by_id = {str(item.id): item for item in parents}
    if set(parent_by_id) != set(plan["parent_order"]):
        raise ValueError("v33 selected parents are not exact in-run database candidates")
    for selected in cohort.selected_parents:
        parent = parent_by_id[selected.parent_id]
        if parent.sequence_sha256 != selected.parent_sequence_sha256:
            raise ValueError(f"v33 parent sequence identity mismatch: {selected.parent_id}")
        if parent.generator_call_id is None:
            raise ValueError(f"v33 parent lacks generation evidence: {selected.parent_id}")

    literature_call = await repository.record_completed_tool_call(
        run_id,
        "v33-literature-basis-freezer",
        V33_EVIDENCE_VERSION,
        environment_sha256,
        {"benchmark_id": manifest_payload["benchmark_id"]},
        {"primary_sources_only": True},
        literature_basis,
        model_uri="deterministic://v33-literature-basis-freezer",
    )
    literature_artifact = await _register_json_artifact(
        session,
        literature_call.id,
        literature_basis,
        "literature_basis_manifest",
        {"benchmark_id": manifest_payload["benchmark_id"]},
        artifact_writer,
    )
    transform_call = await repository.record_completed_tool_call(
        run_id,
        "v33-matched-charge-transformer",
        V33_EVIDENCE_VERSION,
        environment_sha256,
        {
            "parent_order": plan["parent_order"],
            "manifest_sha256": sha256_json(manifest_payload),
            "literature_artifact_sha256": literature_artifact.sha256,
        },
        {"seven_matched_arms": True, "weighted_total_used": False},
        plan,
        model_uri="deterministic://v33-matched-charge-transformer",
    )
    await repository.record_tool_dependency(
        transform_call.id, literature_call.id, "uses_literature_basis"
    )
    for source_call_id in sorted(
        {parent_by_id[value].generator_call_id for value in plan["parent_order"]}, key=str
    ):
        assert source_call_id is not None
        await repository.record_tool_dependency(
            transform_call.id, source_call_id, "transforms_generated_parent"
        )
    cohort_artifact = await _register_json_artifact(
        session,
        transform_call.id,
        cohort.model_dump(mode="json"),
        "charge_counterfactual_cohort",
        {"benchmark_id": manifest_payload["benchmark_id"], "plan_sha256": plan["plan_sha256"]},
        artifact_writer,
    )
    await _register_json_artifact(
        session,
        transform_call.id,
        manifest_payload,
        "submitted_manifest",
        {"benchmark_id": manifest_payload["benchmark_id"]},
        artifact_writer,
    )

    candidate_by_logical: dict[str, Candidate] = {}
    for item in plan["candidate_records"]:
        parent = parent_by_id[item["parent_candidate_id"]]
        if item["is_baseline_parent"]:
            candidate = parent
        else:
            candidate = await repository.add_candidate(
                run_id,
                item["sequence"],
                generation=parent.generation + 1,
                proposal_rank=item["parent_rank"] * 10 + item["arm_rank"],
                generator_call_id=transform_call.id,
                parent_id=parent.id,
                metadata={
                    "benchmark_id": manifest_payload["benchmark_id"],
                    "parent_candidate_id": str(parent.id),
                    "counterfactual_arm": item["arm"],
                    "edit_positions_zero_based": item["edit_positions_zero_based"],
                    "substitutions": item["substitutions"],
                    "charge_plan_sha256": plan["plan_sha256"],
                },
                actor="v33-matched-charge-transformer",
            )
            if candidate.parent_id != parent.id or candidate.generator_call_id != transform_call.id:
                raise ValueError("v33 child recovery has incompatible provenance")
        candidate_by_logical[item["logical_id"]] = candidate
    for item in plan["descriptor_evaluations"]:
        await repository.record_evaluation(
            candidate_by_logical[item["logical_id"]].id,
            transform_call.id,
            item["metric_name"],
            item["numeric_value"],
            item["unit"],
            {
                "method_version": V33_EVIDENCE_VERSION,
                "logical_id": item["logical_id"],
                "deterministic_descriptor": True,
            },
        )
    await repository.append_event(
        "run",
        run_id,
        "v33.charge_counterfactual_cohort_persisted",
        "v33-matched-charge-transformer",
        {
            "transform_tool_call_id": str(transform_call.id),
            "cohort_artifact_sha256": cohort_artifact.sha256,
            "plan_sha256": plan["plan_sha256"],
            "parent_count": len(plan["parent_order"]),
            "arm_count": len(plan["candidate_records"]),
            "rejections": plan["rejections"],
        },
    )
    return {
        "transform_tool_call_id": str(transform_call.id),
        "literature_tool_call_id": str(literature_call.id),
        "cohort_artifact_sha256": cohort_artifact.sha256,
        "plan_sha256": plan["plan_sha256"],
        "candidate_ids_by_logical_id": {
            key: str(value.id) for key, value in candidate_by_logical.items()
        },
        "idempotently_recovered": False,
    }


async def _recover_transform(
    session: AsyncSession,
    run_id: uuid.UUID,
    transform_call: ToolCall,
    plan: dict[str, Any],
) -> dict[str, Any]:
    links = list(
        await session.scalars(
            select(EvidenceArtifact).where(EvidenceArtifact.tool_call_id == transform_call.id)
        )
    )
    roles = {item.role for item in links}
    if not {"charge_counterfactual_cohort", "submitted_manifest"}.issubset(roles):
        raise ValueError("persisted v33 transform lacks required artifacts")
    candidates = list(
        await session.scalars(
            select(Candidate).where(
                Candidate.run_id == run_id,
                (Candidate.generator_call_id == transform_call.id)
                | Candidate.id.in_([uuid.UUID(value) for value in plan["parent_order"]]),
            )
        )
    )
    recovered = recover_v33_transform_identity(
        plan,
        [
            {
                "id": str(item.id),
                "sequence_sha256": item.sequence_sha256,
                "parent_id": str(item.parent_id) if item.parent_id else None,
            }
            for item in candidates
        ],
    )
    return {
        "transform_tool_call_id": str(transform_call.id),
        "plan_sha256": plan["plan_sha256"],
        "candidate_ids_by_logical_id": recovered,
        "idempotently_recovered": True,
    }


async def persist_v33_archive_evidence(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    transform_tool_call_id: uuid.UUID,
    metric_tool_call_ids: list[uuid.UUID],
    snapshots: list[dict[str, Any]],
    saturation_assessment: dict[str, Any],
    environment_sha256: str,
    artifact_writer: ArtifactWriter,
) -> dict[str, Any]:
    """Persist checkpoint evolution and the saturation decision without early stopping."""
    repository = ExperimentRepository(session)
    validated_snapshots = [
        ArchiveSnapshot.model_validate(item).model_dump(mode="json") for item in snapshots
    ]
    validated_assessment = SaturationAssessment.model_validate(
        saturation_assessment
    ).model_dump(mode="json")
    snapshot_payload = {"schema_version": "1.0", "snapshots": validated_snapshots}
    output = {
        "snapshot_sha256": sha256_json(snapshot_payload),
        "saturation_assessment_sha256": sha256_json(validated_assessment),
        "fixed_full_budget_completed": not validated_assessment[
            "missing_seed_family_checkpoints"
        ],
    }
    existing = await session.scalar(
        select(ToolCall).where(
            ToolCall.run_id == run_id,
            ToolCall.tool_name == "v33-checkpoint-archive",
        )
    )
    if existing is not None:
        if existing.output_sha256 != sha256_json(output):
            raise ValueError("persisted v33 archive differs from retry payload")
        decision = await session.scalar(
            select(AgentDecision).where(
                AgentDecision.run_id == run_id,
                AgentDecision.decision_type == "v33_saturation",
            )
        )
        if decision is None or decision.structured_json != validated_assessment:
            raise ValueError("persisted v33 archive lacks its exact saturation decision")
        return {
            "archive_tool_call_id": str(existing.id),
            "decision_id": str(decision.id),
            "idempotently_recovered": True,
        }

    transform = await session.get(ToolCall, transform_tool_call_id)
    if transform is None or transform.run_id != run_id:
        raise ValueError("v33 archive transform dependency is missing or cross-run")
    unique_metric_ids = sorted(set(metric_tool_call_ids), key=str)
    if len(unique_metric_ids) != len(metric_tool_call_ids):
        raise ValueError("v33 archive metric dependency list contains duplicates")
    for call_id in unique_metric_ids:
        call = await session.get(ToolCall, call_id)
        if call is None or call.run_id != run_id:
            raise ValueError("v33 archive metric dependency is missing or cross-run")
    archive_call = await repository.record_completed_tool_call(
        run_id,
        "v33-checkpoint-archive",
        V33_EVIDENCE_VERSION,
        environment_sha256,
        {
            "transform_tool_call_id": str(transform_tool_call_id),
            "metric_tool_call_ids": [str(value) for value in unique_metric_ids],
            "snapshot_input_sha256": sha256_json(snapshot_payload),
        },
        {
            "fixed_full_budget_required": True,
            "adaptive_early_stop_used": False,
            "weighted_total_used": False,
        },
        output,
        model_uri="deterministic://v33-checkpoint-archive",
    )
    await repository.record_tool_dependency(
        archive_call.id, transform_tool_call_id, "archives_transformed_candidates"
    )
    for call_id in unique_metric_ids:
        await repository.record_tool_dependency(
            archive_call.id, call_id, "archives_metric_evidence"
        )
    snapshot_artifact = await _register_json_artifact(
        session,
        archive_call.id,
        snapshot_payload,
        "checkpoint_archive_snapshots",
        {"claim_scope": "protocol_budget_only"},
        artifact_writer,
    )
    assessment_artifact = await _register_json_artifact(
        session,
        archive_call.id,
        validated_assessment,
        "saturation_assessment",
        {"global_optimum_claim": False},
        artifact_writer,
    )
    prompt = (
        "Apply the preregistered v33 all-seed saturation gates after the complete fixed budget. "
        "Do not infer global optimality or use a weighted total."
    )
    response = json.dumps(
        validated_assessment, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    decision = await repository.record_agent_decision(
        run_id,
        0,
        "v33_saturation",
        "v33-evidence-governance-agent",
        V33_EVIDENCE_VERSION,
        prompt,
        response,
        validated_assessment,
        response_artifact_id=assessment_artifact.id,
    )
    await repository.record_agent_tool_edge(
        decision.id, archive_call.id, "input", "observes_complete_archive"
    )
    await repository.record_agent_tool_edge(
        decision.id, transform_tool_call_id, "input", "observes_transform_contract"
    )
    for call_id in unique_metric_ids:
        await repository.record_agent_tool_edge(
            decision.id, call_id, "input", "observes_metric_evidence"
        )
    await repository.record_agent_tool_edge(
        decision.id, archive_call.id, "output", "materializes_saturation_verdict"
    )
    await repository.append_event(
        "run",
        run_id,
        "v33.search_sufficiency_persisted",
        "v33-evidence-governance-agent",
        {
            "archive_tool_call_id": str(archive_call.id),
            "snapshot_artifact_sha256": snapshot_artifact.sha256,
            "assessment_artifact_sha256": assessment_artifact.sha256,
            "decision_id": str(decision.id),
        },
    )
    return {
        "archive_tool_call_id": str(archive_call.id),
        "decision_id": str(decision.id),
        "snapshot_artifact_sha256": snapshot_artifact.sha256,
        "assessment_artifact_sha256": assessment_artifact.sha256,
        "idempotently_recovered": False,
    }


def verify_v33_evidence_graph(
    graph: dict[str, Any], artifact_payloads_by_sha256: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Fail-closed structural replay using only database rows and artifact bytes."""
    calls = {item["id"]: item for item in graph["tool_calls"]}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for call in calls.values():
        by_name.setdefault(call["tool_name"], []).append(call)
    for name in (
        "v33-literature-basis-freezer",
        "v33-matched-charge-transformer",
        "v33-checkpoint-archive",
    ):
        if len(by_name.get(name, [])) != 1:
            raise ValueError(f"v33 replay requires exactly one {name} call")
    transform = by_name["v33-matched-charge-transformer"][0]
    literature = by_name["v33-literature-basis-freezer"][0]
    archive_call = by_name["v33-checkpoint-archive"][0]
    expected_role_call = {
        "literature_basis_manifest": literature["id"],
        "submitted_manifest": transform["id"],
        "charge_counterfactual_cohort": transform["id"],
        "checkpoint_archive_snapshots": archive_call["id"],
        "saturation_assessment": archive_call["id"],
    }
    links = graph["evidence_artifacts"]
    artifacts = {item["id"]: item for item in graph["artifacts"]}
    role_payload: dict[str, dict[str, Any]] = {}
    for link in links:
        if link["role"] not in expected_role_call:
            continue
        if link["tool_call_id"] != expected_role_call[link["role"]]:
            raise ValueError(f"v33 artifact role linked to wrong call: {link['role']}")
        artifact = artifacts.get(link["artifact_id"])
        if artifact is None:
            raise ValueError("v33 evidence link references a missing artifact")
        payload = artifact_payloads_by_sha256.get(artifact["sha256"])
        if payload is None:
            raise ValueError(f"v33 artifact bytes unavailable: {artifact['sha256']}")
        if sha256_json(payload) != artifact["sha256"]:
            raise ValueError(f"v33 artifact SHA mismatch: {artifact['sha256']}")
        if link["role"] in role_payload:
            raise ValueError(f"ambiguous v33 artifact role: {link['role']}")
        role_payload[link["role"]] = payload
    required_roles = {
        "literature_basis_manifest",
        "submitted_manifest",
        "charge_counterfactual_cohort",
        "checkpoint_archive_snapshots",
        "saturation_assessment",
    }
    if not required_roles.issubset(role_payload):
        missing = sorted(required_roles - role_payload.keys())
        raise ValueError(f"missing v33 artifact roles: {missing}")
    cohort = CounterfactualCohortResult.model_validate(
        role_payload["charge_counterfactual_cohort"]
    )
    plan = build_v33_charge_persistence_plan(cohort)
    candidate_by_id = {item["id"]: item for item in graph["candidates"]}
    sequence_index = {
        (item["sequence_sha256"], item.get("parent_id")): item
        for item in graph["candidates"]
    }
    logical_to_id: dict[str, str] = {}
    for item in plan["candidate_records"]:
        parent_id = item["parent_candidate_id"]
        if parent_id not in candidate_by_id:
            raise ValueError(f"missing v33 parent candidate: {parent_id}")
        lookup_parent = None if item["is_baseline_parent"] else parent_id
        candidate = sequence_index.get((item["sequence_sha256"], lookup_parent))
        if candidate is None:
            raise ValueError(f"missing v33 candidate arm: {item['logical_id']}")
        logical_to_id[item["logical_id"]] = candidate["id"]
    numeric = {
        (item["candidate_id"], item["metric_name"]): item["numeric_value"]
        for item in graph["evaluations"]
        if item["numeric_value"] is not None
    }
    for expected in plan["descriptor_evaluations"]:
        key = (logical_to_id[expected["logical_id"]], expected["metric_name"])
        observed = numeric.get(key)
        if observed is None or not math.isclose(
            float(observed), float(expected["numeric_value"]), abs_tol=1e-8, rel_tol=1e-6
        ):
            raise ValueError(f"v33 descriptor replay mismatch: {expected['logical_id']}/{key[1]}")
    archive = role_payload["checkpoint_archive_snapshots"]
    snapshots = [ArchiveSnapshot.model_validate(item) for item in archive.get("snapshots", [])]
    SaturationAssessment.model_validate(role_payload["saturation_assessment"])
    for snapshot in snapshots:
        removed = set(snapshot.removed_candidate_ids)
        if removed != set(snapshot.removed_candidate_dominance_witnesses):
            raise ValueError("v33 archive removal lacks exact dominance witnesses")
    dependency_edges = {
        (item["child_tool_call_id"], item["parent_tool_call_id"], item["relation_type"])
        for item in graph["tool_call_dependencies"]
    }
    if (transform["id"], literature["id"], "uses_literature_basis") not in dependency_edges:
        raise ValueError("v33 transform lacks literature dependency")
    if not any(
        edge[0] == archive_call["id"] and edge[1] == transform["id"]
        for edge in dependency_edges
    ):
        raise ValueError("v33 archive lacks transform dependency")
    decisions = [
        item for item in graph["agent_decisions"] if item["decision_type"] == "v33_saturation"
    ]
    if len(decisions) != 1:
        raise ValueError("v33 replay requires one saturation AgentDecision")
    decision_edges = {
        (
            item["decision_id"],
            item["tool_call_id"],
            item["direction"],
            item.get("relation_type"),
        )
        for item in graph["agent_decision_tool_call_edges"]
    }
    if not any(
        edge[:3] == (decisions[0]["id"], archive_call["id"], "input")
        for edge in decision_edges
    ):
        raise ValueError("v33 saturation decision lacks archive input edge")
    required_observed_calls = {
        edge[1] for edge in dependency_edges if edge[0] == archive_call["id"]
    }
    observed_calls = {
        edge[1]
        for edge in decision_edges
        if edge[0] == decisions[0]["id"] and edge[2] == "input"
    }
    missing_observations = required_observed_calls - observed_calls
    if missing_observations:
        raise ValueError(
            f"v33 saturation decision lacks evidence edges: {sorted(missing_observations)}"
        )
    result = {
        "schema_version": "1.0",
        "exact_replay": True,
        "parent_order": plan["parent_order"],
        "candidate_ids_by_logical_id": logical_to_id,
        "archive_snapshot_count": len(snapshots),
        "saturation_assessment": role_payload["saturation_assessment"],
        "manifest_sha256": sha256_json(role_payload["submitted_manifest"]),
        "literature_basis_sha256": sha256_json(role_payload["literature_basis_manifest"]),
    }
    result["replay_sha256"] = sha256_json(result)
    return result


async def build_v33_database_object_replay_bundle(
    session: AsyncSession, run_id: uuid.UUID, artifact_reader: ArtifactReader
) -> dict[str, Any]:
    graph = await build_database_evidence_graph(session, run_id)
    payloads: dict[str, dict[str, Any]] = {}
    for artifact in graph["artifacts"]:
        raw = artifact_reader(artifact["storage_uri"])
        if sha256_bytes(raw) != artifact["sha256"]:
            raise OSError(f"v33 object-store artifact checksum mismatch: {artifact['sha256']}")
        payloads[artifact["sha256"]] = json.loads(raw)
    replay = verify_v33_evidence_graph(graph, payloads)
    return {"schema_version": "1.0", "evidence_graph": graph, "replay": replay}


async def persist_v33_replay_bundle(
    session: AsyncSession,
    *,
    run_id: uuid.UUID,
    archive_tool_call_id: uuid.UUID,
    environment_sha256: str,
    artifact_writer: ArtifactWriter,
    artifact_reader: ArtifactReader,
) -> dict[str, Any]:
    """Persist the final database+object-store-only replay as a retry-safe evidence node."""
    repository = ExperimentRepository(session)
    existing = await session.scalar(
        select(ToolCall).where(
            ToolCall.run_id == run_id,
            ToolCall.tool_name == "v33-database-object-replay-verifier",
        )
    )
    if existing is not None:
        link = await session.scalar(
            select(EvidenceArtifact).where(
                EvidenceArtifact.tool_call_id == existing.id,
                EvidenceArtifact.role == "database_object_replay_bundle",
            )
        )
        if link is None:
            raise ValueError("persisted v33 replay call lacks its bundle artifact")
        artifact = await session.get(Artifact, link.artifact_id)
        if artifact is None:
            raise ValueError("persisted v33 replay bundle artifact row is missing")
        return {
            "replay_tool_call_id": str(existing.id),
            "replay_bundle_sha256": artifact.sha256,
            "exact_replay": True,
            "idempotently_recovered": True,
        }
    archive_call = await session.get(ToolCall, archive_tool_call_id)
    if archive_call is None or archive_call.run_id != run_id:
        raise ValueError("v33 replay archive dependency is missing or cross-run")
    bundle = await build_v33_database_object_replay_bundle(session, run_id, artifact_reader)
    replay_call = await repository.record_completed_tool_call(
        run_id,
        "v33-database-object-replay-verifier",
        V33_EVIDENCE_VERSION,
        environment_sha256,
        {
            "graph_sha256": bundle["evidence_graph"]["graph_sha256"],
            "expected_replay_sha256": bundle["replay"]["replay_sha256"],
        },
        {"filesystem_fallback_used": False, "exact_replay_required": True},
        {
            "exact_replay": True,
            "replay_sha256": bundle["replay"]["replay_sha256"],
        },
        model_uri="deterministic://v33-database-object-replay-verifier",
    )
    await repository.record_tool_dependency(
        replay_call.id, archive_tool_call_id, "verifies_complete_archive"
    )
    artifact = await _register_json_artifact(
        session,
        replay_call.id,
        bundle,
        "database_object_replay_bundle",
        {"exact_replay": True, "filesystem_fallback_used": False},
        artifact_writer,
    )
    await repository.append_event(
        "run",
        run_id,
        "v33.database_object_replay_verified",
        "v33-database-object-replay-verifier",
        {
            "replay_tool_call_id": str(replay_call.id),
            "replay_bundle_sha256": artifact.sha256,
            "replay_sha256": bundle["replay"]["replay_sha256"],
        },
    )
    return {
        "replay_tool_call_id": str(replay_call.id),
        "replay_bundle_sha256": artifact.sha256,
        "exact_replay": True,
        "idempotently_recovered": False,
    }
