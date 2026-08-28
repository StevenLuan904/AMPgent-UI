from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from sqlalchemy import func, select
from temporalio import activity

from pepagent.autoresearch_closed_loop import (
    ARCHIVE_NAMES,
    CandidateEvidence,
    ContinuationPolicy,
    ControlledCrossoverAction,
    DeNovoAction,
    MaskedSubstitutionAction,
    MetricObservation,
    MultiFrontArchivePolicy,
    MultiFrontArchiveSnapshot,
    PepMLMTargetedAction,
    apply_evolution_action,
    build_multi_front_archive,
    parse_evolution_action,
    parse_persisted_archive_snapshot,
    update_multi_front_archive,
    validate_action_child,
)
from pepagent.autoresearch_planner import (
    GOLD_CANDIDATE_TARGET,
    PlannerDeltaEvidence,
    build_multifront_rule_action_plan,
)
from pepagent.autoresearch_score_ingest import (
    FORMAL_SCORE_COLUMNS,
    GURUPRASAD_OOD_COLUMN,
    safe_relative_score_bundle_path,
    validate_score_all_bundle,
    validate_score_source_map_receipt,
)
from pepagent.db.models import (
    AgentDecision,
    Artifact,
    AutoResearchAction,
    AutoResearchArchiveMembership,
    AutoResearchArchiveVersion,
    AutoResearchMetricDelta,
    Candidate,
    CandidateLineageEdge,
    CandidateOccurrence,
    Evaluation,
    ExperimentRun,
    RunStageCheckpoint,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.enums import EvaluationStatus, RunStatus
from pepagent.provenance.hashing import sha256_bytes, sha256_json, sha256_text
from pepagent.sequence_family import cluster_sequence_families
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore
from pepagent.v38_science_execution import V38SequenceExecutionContract
from pepagent.workers.activities import (
    _register_artifact,
    _run_json_cli,
    _store_json,
    _verify_pepmlm_release,
)

_METRIC_DIRECTIONS = {
    "hydrophobic_moment_eisenberg": "audit",
    "hydrophobic_ratio_modlamp": "audit",
    "maximum_hydrophobic_run": "minimize",
    "net_charge_ph7_4": "audit",
    "guruprasad_instability_index": "minimize",
    "macrel_amp_probability": "maximize",
    "macrel_hemolysis_probability": "minimize",
    "macrel_hemolysis_label": "categorical",
    "llamp_log10_mic_um": "minimize",
    "amp_read_log10_mic_um": "minimize",
    "toxinpred3_hybrid_score": "minimize",
    "toxinpred3_label": "categorical",
}

_IMPORTED_METRIC_UNITS = {
    "amp_read_log10_mic_um": "log10(uM)",
    "llamp_log10_mic_um": "log10(uM)",
    "macrel_amp_probability": "probability",
    "toxinpred3_label": "label",
    "toxinpred3_hybrid_score": "dimensionless",
    "macrel_hemolysis_label": "label",
    "macrel_hemolysis_probability": "probability",
    "net_charge_ph7_4": "elementary_charge",
    "hydrophobic_ratio_modlamp": "fraction",
    "hydrophobic_moment_eisenberg": "dimensionless",
    "maximum_hydrophobic_run": "residues",
    "guruprasad_instability_index": "index",
}

_AUTORESEARCH_GENERATOR_SEMAPHORE = asyncio.Semaphore(1)
_TEMPORAL_PAYLOAD_REFERENCE_SCHEMA = "ampgent.autoresearch-payload-reference.1"


def _temporal_payload_reference(
    stored: Any,
    *,
    role: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Return a small Temporal payload while the full science bytes remain in CAS."""

    return {
        "schema_version": _TEMPORAL_PAYLOAD_REFERENCE_SCHEMA,
        "payload_role": role,
        "storage_uri": str(stored.uri),
        "artifact_sha256": str(stored.sha256),
        "size_bytes": int(stored.size_bytes),
        **summary,
    }


async def _load_temporal_payload_reference(
    reference: dict[str, Any],
    *,
    role: str,
) -> dict[str, Any]:
    if reference.get("schema_version") != _TEMPORAL_PAYLOAD_REFERENCE_SCHEMA:
        raise ValueError("AutoResearch Temporal payload reference schema is invalid")
    if str(reference.get("payload_role") or "") != role:
        raise ValueError("AutoResearch Temporal payload reference role drifted")
    expected_sha256 = str(reference.get("artifact_sha256") or "")
    storage_uri = str(reference.get("storage_uri") or "")
    if len(expected_sha256) != 64 or not storage_uri:
        raise ValueError("AutoResearch Temporal payload reference is incomplete")
    raw = await asyncio.to_thread(ContentAddressedObjectStore().get_bytes, storage_uri)
    if sha256_bytes(raw) != expected_sha256:
        raise ValueError("AutoResearch Temporal payload CAS bytes drifted")
    if int(reference.get("size_bytes", len(raw))) != len(raw):
        raise ValueError("AutoResearch Temporal payload CAS size drifted")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("AutoResearch Temporal payload CAS JSON is invalid") from error
    if not isinstance(payload, dict):
        raise ValueError("AutoResearch Temporal payload CAS root is not an object")
    return payload


def _workflow_request_from_run(run: ExperimentRun) -> dict[str, Any]:
    spec = run.spec_json if isinstance(run.spec_json, dict) else {}
    request = spec.get("workflow_request")
    if not isinstance(request, dict):
        raise ValueError("AutoResearch run lacks its authoritative workflow request")
    return request


async def _load_run_workflow_request(run_id: uuid.UUID) -> dict[str, Any]:
    async with SessionFactory() as session:
        run = await session.get(ExperimentRun, run_id)
        if run is None:
            raise ValueError("AutoResearch run is missing")
        return _workflow_request_from_run(run)


async def _hydrate_planner_request(request: dict[str, Any]) -> dict[str, Any]:
    if not bool(request.get("hydrate_from_run_spec")):
        return request
    run_id = uuid.UUID(str(request["run_id"]))
    workflow_request = await _load_run_workflow_request(run_id)
    if str(workflow_request.get("run_id") or "") != str(run_id):
        raise ValueError("AutoResearch planner run identity differs from its run spec")
    if str(workflow_request.get("branch_key") or "") != str(request.get("branch_key") or ""):
        raise ValueError("AutoResearch planner branch identity differs from its run spec")
    provider = workflow_request.get("planner_provider")
    executor = workflow_request.get("action_executor")
    if not isinstance(provider, dict) or not isinstance(executor, dict):
        raise ValueError("AutoResearch planner provider or executor is not frozen")
    return {
        **request,
        "archive_policy": workflow_request["archive_policy"],
        "continuation_policy": workflow_request["continuation_policy"],
        "planner_contract": provider.get("planner_contract") or {},
        "execution_contract": workflow_request["execution_contract"],
        "operator_release_sha256": str(
            executor.get("operator_release_sha256")
            or executor["operator_environment_sha256"]
        ),
        "control_environment_sha256": workflow_request["control_environment_sha256"],
        "target_sequence_sha256": executor["target_sequence_sha256"],
    }


async def _resolve_planner_result_reference(reference: dict[str, Any]) -> dict[str, Any]:
    payload = await _load_temporal_payload_reference(reference, role="planner_result")
    plan = payload.get("plan")
    snapshot = payload.get("snapshot")
    if not isinstance(plan, dict) or not isinstance(snapshot, dict):
        raise ValueError("AutoResearch planner CAS payload is incomplete")
    run_id = str(reference.get("run_id") or "")
    iteration_no = int(reference.get("iteration_no", -1))
    branch_key = str(reference.get("branch_key") or "")
    if (
        str(payload.get("run_id")) != run_id
        or int(payload.get("iteration_no", -1)) != iteration_no
        or str(payload.get("branch_key")) != branch_key
    ):
        raise ValueError("AutoResearch planner CAS reference identity drifted")
    actions = plan.get("actions")
    if not isinstance(actions, list) or len(actions) != int(reference.get("action_count", -1)):
        raise ValueError("AutoResearch planner CAS action count drifted")
    archive_sha256 = str(snapshot.get("archive_sha256") or "")
    if archive_sha256 != str(reference.get("archive_sha256") or ""):
        raise ValueError("AutoResearch planner CAS archive identity drifted")
    prompt = _canonical_json(
        {
            "branch_key": branch_key,
            "iteration_no": iteration_no,
            "archive_sha256": archive_sha256,
            "gold_target": plan["gold_target"],
            "gold_candidate_count": plan["gold_candidate_count"],
            "strategy_contract": (
                "retain_conflicting_endpoints_and_family_novelty_without_weighted_score"
            ),
        }
    )
    result = {
        "schema_version": "ampgent.autoresearch-planner-result.1",
        "agent_decision": {
            "agent_name": "autoresearch-multi-front-rule-planner",
            "agent_version": "1",
            "model_name": None,
            "prompt_text": prompt,
            "response_text": _canonical_json(plan),
            "rationale_by_action_sha256": plan["rationale_by_action_sha256"],
            "planner_tool_call_id": str(reference["planner_tool_call_id"]),
        },
        "actions": actions,
        "planner_receipt": {
            "tool_call_id": str(reference["planner_tool_call_id"]),
            "artifact_id": str(reference["artifact_id"]),
            "artifact_sha256": str(reference["artifact_sha256"]),
            "archive_sha256": archive_sha256,
            "strategies": plan["strategies"],
        },
    }
    return result


async def _resolve_action_plan_reference(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("schema_version") != _TEMPORAL_PAYLOAD_REFERENCE_SCHEMA:
        return plan
    if plan.get("payload_role") != "action_plan":
        raise ValueError("AutoResearch action plan reference role drifted")
    run_id = uuid.UUID(str(plan["run_id"]))
    iteration_no = int(plan["iteration_no"])
    expected_ids = [uuid.UUID(str(item)) for item in plan.get("action_ids") or []]
    async with SessionFactory() as session:
        decision = await session.get(AgentDecision, uuid.UUID(str(plan["agent_decision_id"])))
        if (
            decision is None
            or decision.run_id != run_id
            or decision.generation != iteration_no
        ):
            raise ValueError("AutoResearch action plan decision reference drifted")
        rows = list(
            await session.scalars(
                select(AutoResearchAction)
                .where(
                    AutoResearchAction.run_id == run_id,
                    AutoResearchAction.iteration_no == iteration_no,
                )
                .order_by(AutoResearchAction.action_ordinal, AutoResearchAction.id)
            )
        )
        decision_id = decision.id
        decision_structured = (
            dict(decision.structured_json)
            if isinstance(decision.structured_json, dict)
            else {}
        )
    if [item.id for item in rows] != expected_ids:
        raise ValueError("AutoResearch action plan row identities drifted")
    persisted: list[dict[str, Any]] = []
    for item in rows:
        action_spec = item.action_spec_json
        operations = action_spec.get("operations") if isinstance(action_spec, dict) else None
        if not isinstance(operations, list) or len(operations) != 1:
            raise ValueError("AutoResearch action plan operation is malformed")
        runtime_action = operations[0].get("payload")
        if not isinstance(runtime_action, dict):
            raise ValueError("AutoResearch action plan runtime action is missing")
        projection = build_typed_action_projection(
            runtime_action,
            iteration_no=iteration_no,
            action_ordinal=int(item.action_ordinal),
            rationale_text=item.rationale_text,
        )
        if (
            projection["action_spec"] != action_spec
            or projection["action_kind"] != item.action_kind
            or projection["random_seed"] != item.random_seed
        ):
            raise ValueError("AutoResearch action plan database projection drifted")
        persisted.append(
            {
                **projection,
                "action_id": str(item.id),
                "repository_action_sha256": item.action_sha256,
            }
        )
    reconstructed = {
        "schema_version": "ampgent.autoresearch-action-plan-receipt.1",
        "run_id": str(run_id),
        "iteration_no": iteration_no,
        "branch_key": str(plan["branch_key"]),
        "agent_decision_id": str(decision_id),
        "action_batch_sha256": str(plan["action_batch_sha256"]),
        "planner_receipt": decision_structured.get("planner_receipt"),
        "actions": persisted,
    }
    calculated = sha256_json(
        {
            "schema_version": "ampgent.autoresearch-action-batch.1",
            "run_id": str(run_id),
            "iteration_no": iteration_no,
            "decision_id": str(decision_id),
            "actions": [
                {
                    "action_id": item["action_id"],
                    "repository_action_sha256": item["repository_action_sha256"],
                    "runtime_action_sha256": item["runtime_action_sha256"],
                }
                for item in persisted
            ],
        }
    )
    if calculated != reconstructed["action_batch_sha256"]:
        raise ValueError("AutoResearch action plan batch identity drifted")
    return reconstructed


async def _resolve_generated_reference(generated: dict[str, Any]) -> dict[str, Any]:
    if generated.get("schema_version") != _TEMPORAL_PAYLOAD_REFERENCE_SCHEMA:
        return generated
    payload = await _load_temporal_payload_reference(generated, role="generated_action_batch")
    for key in ("run_id", "iteration_no", "action_batch_sha256", "result_count"):
        expected = len(payload.get("results") or []) if key == "result_count" else payload.get(key)
        if str(expected) != str(generated.get(key)):
            raise ValueError("AutoResearch generated-action CAS summary drifted")
    return payload


async def _resolve_children_reference(children: dict[str, Any]) -> dict[str, Any]:
    if children.get("schema_version") != _TEMPORAL_PAYLOAD_REFERENCE_SCHEMA:
        return children
    payload = await _load_temporal_payload_reference(children, role="children_receipt")
    for key in ("run_id", "iteration_no", "candidate_count", "generator_tool_call_id"):
        if str(payload.get(key)) != str(children.get(key)):
            raise ValueError("AutoResearch children CAS summary drifted")
    return payload


def _acquire_autoresearch_generator_lock(path: Path, owner: dict[str, Any]) -> BinaryIO:
    """Take a process-lifetime advisory lock shared by generator pollers."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("AutoResearch generator GPU lock path is a symlink")
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        handle.truncate()
        handle.write((json.dumps(owner, sort_keys=True) + "\n").encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
        return handle
    except BaseException:
        handle.close()
        raise


def _release_autoresearch_generator_lock(handle: BinaryIO) -> None:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _repository_action_kind(action: Any) -> str:
    if isinstance(action, MaskedSubstitutionAction):
        return "point_edit"
    if isinstance(action, ControlledCrossoverAction):
        return "controlled_mix"
    if isinstance(action, DeNovoAction):
        return "de_novo"
    if isinstance(action, PepMLMTargetedAction):
        return {
            "masked_substitution": "point_edit",
            "controlled_crossover": "controlled_mix",
            "de_novo": "de_novo",
        }[action.proposal_mode]
    raise TypeError(f"unsupported AutoResearch action: {type(action).__name__}")


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _action_source_rows(action: Any) -> list[dict[str, Any]]:
    """Project a frozen executable action onto the typed lineage contract."""

    if isinstance(action, MaskedSubstitutionAction):
        spans = [
            {
                "child": [item.position_zero_based + 1, item.position_zero_based + 1],
                "source": [item.position_zero_based + 1, item.position_zero_based + 1],
            }
            for item in action.substitutions
        ]
        return [
            {
                "candidate_id": action.parent_candidate_id,
                "parent_candidate_id": action.parent_candidate_id,
                "relation_role": "primary_parent",
                "source_ordinal": 1,
                "source_spans": spans,
                "metadata": {"runtime_action_sha256": action.action_sha256},
            }
        ]
    if isinstance(action, ControlledCrossoverAction):
        child_offset = 1
        spans_by_role: dict[str, list[dict[str, list[int]]]] = {
            "backbone": [],
            "donor": [],
        }
        for fragment in action.fragments:
            length = fragment.source_end_exclusive - fragment.source_start_zero_based
            role = "backbone" if fragment.source_role == "primary_parent" else "donor"
            spans_by_role[role].append(
                {
                    "child": [child_offset, child_offset + length - 1],
                    "source": [
                        fragment.source_start_zero_based + 1,
                        fragment.source_end_exclusive,
                    ],
                }
            )
            child_offset += length
        return [
            {
                "candidate_id": action.parent_candidate_id,
                "parent_candidate_id": action.parent_candidate_id,
                "relation_role": "backbone",
                "source_ordinal": 1,
                "source_spans": spans_by_role["backbone"],
                "metadata": {"runtime_action_sha256": action.action_sha256},
            },
            {
                "candidate_id": action.donor_candidate_id,
                "parent_candidate_id": action.donor_candidate_id,
                "relation_role": "donor",
                "source_ordinal": 2,
                "source_spans": spans_by_role["donor"],
                "metadata": {"runtime_action_sha256": action.action_sha256},
            },
        ]
    if isinstance(action, DeNovoAction):
        return []
    if isinstance(action, PepMLMTargetedAction):
        if action.proposal_mode == "de_novo":
            return []
        assert action.parent_candidate_id is not None
        if action.proposal_mode == "masked_substitution":
            spans = [
                {"child": [position, position], "source": [position, position]}
                for position in action.mutation_positions_one_based
            ]
            return [
                {
                    "candidate_id": action.parent_candidate_id,
                    "parent_candidate_id": action.parent_candidate_id,
                    "relation_role": "primary_parent",
                    "source_ordinal": 1,
                    "source_spans": spans,
                    "metadata": {
                        "runtime_action_sha256": action.action_sha256,
                        "pepmlm_proposal_mode": action.proposal_mode,
                    },
                }
            ]
        assert action.donor_candidate_id is not None
        assert action.crossover is not None
        window = action.crossover
        donor_length = window.donor_end - window.donor_start + 1
        return [
            {
                "candidate_id": action.parent_candidate_id,
                "parent_candidate_id": action.parent_candidate_id,
                "relation_role": "backbone",
                "source_ordinal": 1,
                "source_spans": [],
                "metadata": {
                    "runtime_action_sha256": action.action_sha256,
                    "replaced_primary_span": [window.primary_start, window.primary_end],
                    "pepmlm_proposal_mode": action.proposal_mode,
                },
            },
            {
                "candidate_id": action.donor_candidate_id,
                "parent_candidate_id": action.donor_candidate_id,
                "relation_role": "donor",
                "source_ordinal": 2,
                "source_spans": [
                    {
                        "child": [
                            window.primary_start,
                            window.primary_start + donor_length - 1,
                        ],
                        "source": [window.donor_start, window.donor_end],
                    }
                ],
                "metadata": {
                    "runtime_action_sha256": action.action_sha256,
                    "pepmlm_proposal_mode": action.proposal_mode,
                },
            },
        ]
    raise TypeError(f"unsupported AutoResearch action: {type(action).__name__}")


def build_typed_action_projection(
    payload: dict[str, Any],
    *,
    iteration_no: int,
    action_ordinal: int,
    rationale_text: str,
) -> dict[str, Any]:
    """Return the exact repository payload for one executable action."""

    action = parse_evolution_action(payload)
    if action.generation != iteration_no + 1:
        raise ValueError("AutoResearch action generation must follow its iteration")
    sources = _action_source_rows(action)
    return {
        "iteration_no": iteration_no,
        "branch_key": action.branch_key,
        "action_ordinal": action_ordinal,
        "action_kind": _repository_action_kind(action),
        "random_seed": action.seed,
        "rationale_text": rationale_text.strip(),
        "expected_objectives": list(action.expected_improvement_metrics),
        "forbidden_changes": [f"protect:{name}" for name in action.protected_metrics],
        "action_spec": {
            "schema_version": "ampgent.autoresearch-repository-action.1",
            "runtime_action_sha256": action.action_sha256,
            "operations": [
                {
                    "op": action.action_type,
                    "payload": action.model_dump(mode="json"),
                }
            ],
            "sources": [
                {
                    "candidate_id": row["candidate_id"],
                    "relation_role": row["relation_role"],
                    "source_ordinal": row["source_ordinal"],
                    "source_spans": row["source_spans"],
                    "metadata": row["metadata"],
                }
                for row in sources
            ],
        },
        "lineage_sources": [
            {
                "parent_candidate_id": row["parent_candidate_id"],
                "relation_role": row["relation_role"],
                "source_ordinal": row["source_ordinal"],
                "source_spans": row["source_spans"],
                "metadata": row["metadata"],
            }
            for row in sources
        ],
        "runtime_action": action.model_dump(mode="json"),
        "runtime_action_sha256": action.action_sha256,
    }


async def _idempotent_agent_decision(
    session: Any,
    repository: ExperimentRepository,
    *,
    run_id: uuid.UUID,
    generation: int,
    decision_type: str,
    agent_name: str,
    agent_version: str,
    model_name: str | None,
    prompt_text: str,
    response_text: str,
    structured: dict[str, Any],
) -> AgentDecision:
    existing = list(
        await session.scalars(
            select(AgentDecision).where(
                AgentDecision.run_id == run_id,
                AgentDecision.generation == generation,
                AgentDecision.decision_type == decision_type,
            )
        )
    )
    if len(existing) > 1:
        raise ValueError("AutoResearch decision identity is duplicated")
    if existing:
        decision = existing[0]
        identity = {
            "agent_name": agent_name,
            "agent_version": agent_version,
            "model_name": model_name,
            "prompt_sha256": sha256_text(prompt_text),
            "response_sha256": sha256_text(response_text),
            "structured_json": structured,
            "status": "succeeded",
        }
        if not all(getattr(decision, key) == value for key, value in identity.items()):
            raise ValueError("AutoResearch decision retry payload drifted")
        return decision
    return await repository.record_agent_decision(
        run_id,
        generation,
        decision_type,
        agent_name,
        agent_version,
        prompt_text,
        response_text,
        structured,
        model_name=model_name,
    )


@activity.defn(name="persist_autoresearch_action_plan")
async def persist_autoresearch_action_plan(request: dict[str, Any]) -> dict[str, Any]:
    """Freeze the Agent decision and every executable action before execution."""

    planner_reference = request.get("planner_result_reference")
    if planner_reference is not None:
        if not isinstance(planner_reference, dict):
            raise ValueError("AutoResearch planner result reference must be an object")
        resolved = await _resolve_planner_result_reference(planner_reference)
        request = {
            **request,
            "agent_decision": resolved["agent_decision"],
            "actions": resolved["actions"],
            "planner_receipt": resolved["planner_receipt"],
        }
    run_id = uuid.UUID(str(request["run_id"]))
    iteration_no = int(request["iteration_no"])
    decision_payload = request["agent_decision"]
    action_payloads = request["actions"]
    if not isinstance(action_payloads, list) or not action_payloads:
        raise ValueError("AutoResearch action plan must contain actions")
    if len(action_payloads) != len({_canonical_json(item) for item in action_payloads}):
        raise ValueError("AutoResearch action plan contains duplicate actions")
    rationales = decision_payload.get("rationale_by_action_sha256") or {}
    projections = []
    for ordinal, payload in enumerate(action_payloads, start=1):
        parsed = parse_evolution_action(payload)
        rationale = str(rationales.get(parsed.action_sha256) or "").strip()
        if not rationale:
            raise ValueError("each AutoResearch action requires an Agent rationale")
        projections.append(
            build_typed_action_projection(
                payload,
                iteration_no=iteration_no,
                action_ordinal=ordinal,
                rationale_text=rationale,
            )
        )
    branch_keys = {item["branch_key"] for item in projections}
    if branch_keys != {str(request["branch_key"])}:
        raise ValueError("AutoResearch action plan mixes branches")
    structured = {
        "schema_version": "ampgent.autoresearch-agent-action-decision.1",
        "run_id": str(run_id),
        "iteration_no": iteration_no,
        "branch_key": str(request["branch_key"]),
        "actions": [item["runtime_action"] for item in projections],
        "rationale_by_action_sha256": rationales,
        "planner_receipt": request.get("planner_receipt"),
    }
    response_text = str(decision_payload.get("response_text") or _canonical_json(structured))
    async with SessionFactory() as session, session.begin():
        run = await session.get(ExperimentRun, run_id)
        if run is None or run.status != RunStatus.RUNNING:
            raise ValueError("AutoResearch actions require a new running run")
        repository = ExperimentRepository(session)
        decision = await _idempotent_agent_decision(
            session,
            repository,
            run_id=run_id,
            generation=iteration_no,
            decision_type="autoresearch_action_batch",
            agent_name=str(decision_payload["agent_name"]),
            agent_version=str(decision_payload["agent_version"]),
            model_name=decision_payload.get("model_name"),
            prompt_text=str(decision_payload["prompt_text"]),
            response_text=response_text,
            structured=structured,
        )
        planner_tool_call_id = decision_payload.get("planner_tool_call_id")
        if planner_tool_call_id is not None:
            await repository.record_agent_tool_edge(
                decision.id,
                uuid.UUID(str(planner_tool_call_id)),
                "input",
                "plans_from_multi_front_evidence",
            )
        persisted = []
        for item in projections:
            row = await repository.record_autoresearch_action(
                run_id=run_id,
                agent_decision_id=decision.id,
                iteration_no=item["iteration_no"],
                branch_key=item["branch_key"],
                action_ordinal=item["action_ordinal"],
                action_kind=item["action_kind"],
                random_seed=item["random_seed"],
                rationale_text=item["rationale_text"],
                expected_objectives=item["expected_objectives"],
                forbidden_changes=item["forbidden_changes"],
                action_spec=item["action_spec"],
            )
            persisted.append(
                {
                    **item,
                    "action_id": str(row.id),
                    "repository_action_sha256": row.action_sha256,
                }
            )
    action_batch_sha256 = sha256_json(
        {
            "schema_version": "ampgent.autoresearch-action-batch.1",
            "run_id": str(run_id),
            "iteration_no": iteration_no,
            "decision_id": str(decision.id),
            "actions": [
                {
                    "action_id": item["action_id"],
                    "repository_action_sha256": item["repository_action_sha256"],
                    "runtime_action_sha256": item["runtime_action_sha256"],
                }
                for item in persisted
            ],
        }
    )
    result = {
        "schema_version": "ampgent.autoresearch-action-plan-receipt.1",
        "run_id": str(run_id),
        "iteration_no": iteration_no,
        "branch_key": str(request["branch_key"]),
        "agent_decision_id": str(decision.id),
        "action_batch_sha256": action_batch_sha256,
        "planner_receipt": request.get("planner_receipt"),
        "actions": persisted,
    }
    if request.get("temporal_payload_mode") != "reference_v1":
        return result
    return {
        "schema_version": _TEMPORAL_PAYLOAD_REFERENCE_SCHEMA,
        "payload_role": "action_plan",
        "run_id": str(run_id),
        "iteration_no": iteration_no,
        "branch_key": str(request["branch_key"]),
        "agent_decision_id": str(decision.id),
        "action_batch_sha256": action_batch_sha256,
        "action_count": len(persisted),
        "action_ids": [item["action_id"] for item in persisted],
    }


def _compile_pepmlm_action(
    *,
    action: PepMLMTargetedAction,
    persisted_action_id: str,
    sources_by_id: dict[str, CandidateEvidence],
) -> dict[str, Any]:
    """Compile the typed action into the frozen PepMLM CLI action schema."""

    payload: dict[str, Any] = {
        "action_kind": action.proposal_mode,
        "action_id": persisted_action_id,
        "seed": action.seed,
        "top_k": action.top_k,
        "temperature": action.temperature,
        "expected_improvement_axes": list(action.expected_improvement_metrics),
        "protected_axes": list(action.protected_metrics),
    }
    if action.proposal_mode == "de_novo":
        assert action.peptide_length is not None
        payload["peptide_length"] = action.peptide_length
        return payload
    assert action.parent_candidate_id is not None
    assert action.parent_sequence_sha256 is not None
    parent = sources_by_id.get(action.parent_candidate_id)
    if parent is None or parent.sequence_sha256 != action.parent_sequence_sha256:
        raise ValueError("PepMLM primary parent identity drifted")
    payload.update(
        {
            "primary_parent_id": action.parent_candidate_id,
            "primary_parent_sequence": parent.sequence,
            "mutation_positions": list(action.mutation_positions_one_based),
        }
    )
    if action.proposal_mode == "controlled_crossover":
        assert action.donor_candidate_id is not None
        assert action.donor_sequence_sha256 is not None
        assert action.crossover is not None
        donor = sources_by_id.get(action.donor_candidate_id)
        if donor is None or donor.sequence_sha256 != action.donor_sequence_sha256:
            raise ValueError("PepMLM donor parent identity drifted")
        payload.update(
            {
                "donor_candidate_id": action.donor_candidate_id,
                "donor_sequence": donor.sequence,
                "crossover": action.crossover.model_dump(mode="json"),
            }
        )
    return payload


async def _execute_autoresearch_action_batch_unlocked(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Materialize a frozen action batch without consulting mutable policy."""

    plan = await _resolve_action_plan_reference(request["action_plan"])
    executor = request.get("executor") or {}
    if bool(request.get("executor_from_run_spec")):
        workflow_request = await _load_run_workflow_request(uuid.UUID(str(plan["run_id"])))
        executor = workflow_request.get("action_executor") or {}
    environment_sha256 = str(
        executor.get("operator_environment_sha256")
        or request.get("operator_environment_sha256")
        or ""
    )
    if len(environment_sha256) != 64 or set(environment_sha256) - set("0123456789abcdef"):
        raise ValueError("AutoResearch action executor environment is not frozen")
    run_id = uuid.UUID(str(plan["run_id"]))
    actions = [parse_evolution_action(item["runtime_action"]) for item in plan["actions"]]
    source_ids = {
        uuid.UUID(str(row["parent_candidate_id"]))
        for item in plan["actions"]
        for row in item["lineage_sources"]
    }
    async with SessionFactory() as session:
        sources = (
            list(await session.scalars(select(Candidate).where(Candidate.id.in_(source_ids))))
            if source_ids
            else []
        )
        if {item.id for item in sources} != source_ids:
            raise ValueError("AutoResearch action source cohort is incomplete")
        by_id = {
            str(item.id): CandidateEvidence(
                candidate_id=str(item.id),
                sequence=item.sequence,
                sequence_sha256=item.sequence_sha256,
                family_key="execution-only",
                metrics={},
            )
            for item in sources
        }
    results = []
    pepmlm_requests: list[tuple[dict[str, Any], PepMLMTargetedAction]] = []
    for persisted, action in zip(plan["actions"], actions, strict=True):
        if persisted["runtime_action_sha256"] != action.action_sha256:
            raise ValueError("persisted and executable action identities differ")
        if isinstance(action, PepMLMTargetedAction):
            pepmlm_requests.append(
                (
                    _compile_pepmlm_action(
                        action=action,
                        persisted_action_id=str(persisted["action_id"]),
                        sources_by_id=by_id,
                    ),
                    action,
                )
            )
            continue
        sequence = apply_evolution_action(action, by_id)
        results.append(
            {
                "action_id": persisted["action_id"],
                "repository_action_sha256": persisted["repository_action_sha256"],
                "runtime_action_sha256": action.action_sha256,
                "sequence": sequence,
                "sequence_sha256": sha256_text(sequence),
                "operator_id": action.operator_id,
                "operator_release_sha256": action.operator_release_sha256,
                "seed": action.seed,
                "executor_action_sha256": action.action_sha256,
            }
        )
    pepmlm_output: dict[str, Any] | None = None
    if pepmlm_requests:
        target_sequence = "".join(str(executor.get("target_sequence") or "").split()).upper()
        if not target_sequence:
            raise ValueError("PepMLM-targeted actions require the frozen target sequence")
        target_sequence_sha256 = sha256_text(target_sequence)
        if any(
            action.target_sequence_sha256 != target_sequence_sha256 for _, action in pepmlm_requests
        ):
            raise ValueError("PepMLM target sequence differs from the frozen action")
        settings = get_settings()
        await _verify_pepmlm_release(
            settings.pepmlm_model_path,
            settings.pepmlm_weights_sha256,
        )
        attempt_contracts = {action.max_attempts for _, action in pepmlm_requests}
        if len(attempt_contracts) != 1:
            raise ValueError("one PepMLM action batch requires one retry contract")
        action_max_attempts = next(iter(attempt_contracts))
        cli_request = {
            "target_sequence": target_sequence,
            "seed": min(action.seed for _, action in pepmlm_requests),
            "model": settings.pepmlm_model_path,
            "revision": settings.pepmlm_model_revision,
            "top_k": 5,
            "temperature": 1.0,
            "action_max_attempts": action_max_attempts,
            "action_plans": [payload for payload, _ in pepmlm_requests],
        }
        work_dir = (
            Path(settings.work_root)
            / "autoresearch-pepmlm-cache"
            / str(run_id)
            / f"iteration-{int(plan['iteration_no']):04d}"
        )
        pepmlm_output = await _run_json_cli(
            "pepagent.model_workers.pepmlm_cli",
            cli_request,
            work_dir,
        )
        raw_candidates = list(pepmlm_output.get("candidates") or [])
        by_action_id = {str(item["action_id"]): item for item in raw_candidates}
        expected_action_ids = {str(payload["action_id"]) for payload, _ in pepmlm_requests}
        if set(by_action_id) != expected_action_ids:
            raise ValueError("PepMLM output does not cover the targeted action batch")
        for payload, action in pepmlm_requests:
            item = by_action_id[str(payload["action_id"])]
            sequence = validate_action_child(action, by_id, str(item["sequence"]))
            results.append(
                {
                    "action_id": str(payload["action_id"]),
                    "repository_action_sha256": next(
                        str(row["repository_action_sha256"])
                        for row in plan["actions"]
                        if str(row["action_id"]) == str(payload["action_id"])
                    ),
                    "runtime_action_sha256": action.action_sha256,
                    "executor_action_sha256": str(item["action_sha256"]),
                    "sequence": sequence,
                    "sequence_sha256": sha256_text(sequence),
                    "operator_id": action.operator_id,
                    "operator_release_sha256": action.operator_release_sha256,
                    "seed": action.seed,
                    "sampling_seed": int(item["sampling_seed"]),
                    "sampling_attempt": int(item["sampling_attempt"]),
                    "conditional_nll": item.get("conditional_nll"),
                    "conditional_ppl": item.get("conditional_ppl"),
                    "proposal_mode": item.get("proposal_mode"),
                }
            )
    results.sort(key=lambda item: str(item["action_id"]))
    payload = {
        "schema_version": "ampgent.autoresearch-materialized-action-batch.2",
        "run_id": str(run_id),
        "iteration_no": int(plan["iteration_no"]),
        "action_batch_sha256": plan["action_batch_sha256"],
        "parent_controls": [
            {
                "id": str(item.id),
                "sequence": item.sequence,
                "sequence_sha256": item.sequence_sha256,
                "generation": item.generation,
            }
            for item in sorted(sources, key=lambda candidate: str(candidate.id))
        ],
        "results": results,
        "pepmlm_output": pepmlm_output,
    }
    result = {
        **payload,
        "result_sha256": sha256_json(payload),
        "provenance": {
            "tool_name": (
                "autoresearch-hybrid-action-executor"
                if pepmlm_requests
                else "autoresearch-frozen-action-executor"
            ),
            "tool_version": "2",
            "environment_sha256": environment_sha256,
            "model_uri": (
                get_settings().pepmlm_model_path if pepmlm_requests else executor.get("model_uri")
            ),
            "weights_sha256": (
                get_settings().pepmlm_weights_sha256
                if pepmlm_requests
                else executor.get("weights_sha256")
            ),
            "attempt": activity.info().attempt,
        },
    }
    if request.get("temporal_payload_mode") != "reference_v1":
        return result
    stored = await _store_json(result)
    return _temporal_payload_reference(
        stored,
        role="generated_action_batch",
        summary={
            "run_id": str(run_id),
            "iteration_no": int(plan["iteration_no"]),
            "action_batch_sha256": str(plan["action_batch_sha256"]),
            "result_count": len(results),
        },
    )


@activity.defn(name="execute_autoresearch_action_batch")
async def execute_autoresearch_action_batch(request: dict[str, Any]) -> dict[str, Any]:
    """Run one action batch under in-process and cross-process GPU exclusion."""

    plan = request.get("action_plan") or {}
    owner = {
        "schema_version": "ampgent.autoresearch-generator-gpu-lock.1",
        "pid": os.getpid(),
        "run_id": str(plan.get("run_id") or ""),
        "iteration_no": int(plan.get("iteration_no", -1)),
    }
    work_root = await asyncio.to_thread(Path(get_settings().work_root).resolve)
    lock_path = work_root / ".locks" / "autoresearch-generator-gpu.lock"
    async with _AUTORESEARCH_GENERATOR_SEMAPHORE:
        handle = await asyncio.to_thread(
            _acquire_autoresearch_generator_lock,
            lock_path,
            owner,
        )
        try:
            return await _execute_autoresearch_action_batch_unlocked(request)
        finally:
            await asyncio.to_thread(_release_autoresearch_generator_lock, handle)


def _candidate_was_materialized_by_action(
    candidate: Candidate,
    *,
    action_id: uuid.UUID,
    requested_generation: int,
) -> bool:
    """Distinguish an activity retry from a distinct duplicate proposal."""

    metadata = candidate.metadata_json if isinstance(candidate.metadata_json, dict) else {}
    return (
        candidate.generation == requested_generation
        and str(metadata.get("autoresearch_action_id") or "") == str(action_id)
    )


def _duplicate_rejection_reason(candidate: Candidate, *, requested_generation: int) -> str:
    if candidate.generation != requested_generation:
        return "sequence_already_materialized_in_another_generation"
    return "sequence_already_materialized_by_another_action"


@activity.defn(name="persist_autoresearch_children")
async def persist_autoresearch_children(request: dict[str, Any]) -> dict[str, Any]:
    """Persist materialized children and the complete multi-parent lineage."""

    plan = await _resolve_action_plan_reference(request["action_plan"])
    generated = await _resolve_generated_reference(request["generated"])
    run_id = uuid.UUID(str(plan["run_id"]))
    iteration_no = int(plan["iteration_no"])
    if generated["action_batch_sha256"] != plan["action_batch_sha256"]:
        raise ValueError("generated AutoResearch batch differs from the frozen plan")
    results_by_action = {str(item["action_id"]): item for item in generated["results"]}
    if set(results_by_action) != {str(item["action_id"]) for item in plan["actions"]}:
        raise ValueError("generated AutoResearch results do not cover the action batch")
    provenance = generated["provenance"]
    stored = await _store_json(generated)
    async with SessionFactory() as session, session.begin():
        # Serialize overlapping Temporal attempts for this run so an expired
        # attempt cannot race its retry on the unique sequence constraint.
        run = await session.get(ExperimentRun, run_id, with_for_update=True)
        if run is None or run.status != RunStatus.RUNNING:
            raise ValueError("AutoResearch children require a new running run")
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            str(provenance["tool_name"]),
            str(provenance["tool_version"]),
            str(provenance["environment_sha256"]),
            {
                "stage": "autoresearch.action_execution",
                "iteration_no": iteration_no,
                "action_batch_sha256": plan["action_batch_sha256"],
            },
            {
                "action_count": len(plan["actions"]),
                "deduplicate_before_score_all": True,
            },
            generated,
            weights_sha256=provenance.get("weights_sha256"),
            model_uri=provenance.get("model_uri"),
            attempt=int(provenance["attempt"]),
            logical_stage="autoresearch_action_execution",
            display_category="design",
        )
        generator_tool_call_id = call.id
        await _register_artifact(
            session,
            call.id,
            asdict(stored),
            "autoresearch_materialized_action_batch",
            {
                "iteration_no": iteration_no,
                "action_batch_sha256": plan["action_batch_sha256"],
            },
        )
        decision_id = uuid.UUID(str(plan["agent_decision_id"]))
        await repository.record_agent_tool_edge(
            decision_id,
            call.id,
            "output",
            "materializes_autoresearch_actions",
        )
        children = []
        rejected_duplicates = []
        accepted_source_ids: set[uuid.UUID] = set()
        requested_generation = iteration_no + 1
        for item in plan["actions"]:
            action_id = uuid.UUID(str(item["action_id"]))
            action = await session.get(AutoResearchAction, action_id)
            if action is None or action.run_id != run_id:
                raise ValueError("AutoResearch action row is missing or cross-run")
            result = results_by_action[str(action_id)]
            runtime_action = parse_evolution_action(item["runtime_action"])
            sources = _action_source_rows(runtime_action)
            anchor = next(
                (
                    uuid.UUID(str(row["parent_candidate_id"]))
                    for row in sources
                    if row["relation_role"] in {"primary_parent", "backbone"}
                ),
                None,
            )
            sequence = "".join(str(result["sequence"]).split()).upper()
            sequence_sha256 = sha256_text(sequence)
            if str(result.get("sequence_sha256") or sequence_sha256) != sequence_sha256:
                raise ValueError("AutoResearch generated sequence hash drifted")
            child = await session.scalar(
                select(Candidate).where(
                    Candidate.run_id == run_id,
                    Candidate.sequence_sha256 == sequence_sha256,
                )
            )
            if child is None:
                child = await repository.add_candidate(
                    run_id,
                    sequence,
                    generation=requested_generation,
                    proposal_rank=requested_generation * 1_000_000
                    + int(action.action_ordinal),
                    generator_call_id=call.id,
                    parent_id=anchor,
                    metadata={
                        "autoresearch_action_id": str(action.id),
                        "repository_action_sha256": action.action_sha256,
                        "runtime_action_sha256": item["runtime_action_sha256"],
                        "executor_action_sha256": result.get("executor_action_sha256"),
                        "branch_key": action.branch_key,
                    },
                    actor="autoresearch-action-executor",
                )
            if child.sequence != sequence or child.sequence_sha256 != sequence_sha256:
                raise ValueError("AutoResearch duplicate candidate sequence identity drifted")
            if not _candidate_was_materialized_by_action(
                child,
                action_id=action.id,
                requested_generation=requested_generation,
            ):
                reason = _duplicate_rejection_reason(
                    child,
                    requested_generation=requested_generation,
                )
                occurrence_metadata = {
                    "status": "rejected_duplicate",
                    "reason": reason,
                    "materialized_new_candidate": False,
                    "scientific_output_reused": False,
                    "excluded_from_unique_child_cohort": True,
                    "action_id": str(action.id),
                    "existing_candidate_id": str(child.id),
                    "existing_generation": int(child.generation),
                    "requested_generation": requested_generation,
                    "repository_action_sha256": action.action_sha256,
                    "runtime_action_sha256": item["runtime_action_sha256"],
                    "executor_action_sha256": result.get("executor_action_sha256"),
                }
                occurrence = await repository.record_candidate_occurrence(
                    run_id=run_id,
                    tool_call_id=call.id,
                    parent_candidate_id=anchor,
                    occurrence_rank=int(action.action_ordinal),
                    occurrence_kind=action.action_kind,
                    opaque_arm_label=action.branch_key,
                    sequence=sequence,
                    candidate_id=child.id,
                    metadata=occurrence_metadata,
                )
                event_idempotency_key = sha256_json(
                    {
                        "event": "autoresearch.action.rejected_duplicate",
                        "run_id": str(run_id),
                        "iteration_no": iteration_no,
                        "action_id": str(action.id),
                        "tool_call_id": str(call.id),
                        "occurrence_rank": int(action.action_ordinal),
                    }
                )
                await repository.append_event(
                    "autoresearch_action",
                    action.id,
                    "autoresearch.action.rejected_duplicate",
                    "autoresearch-action-executor",
                    {
                        **occurrence_metadata,
                        "run_id": str(run_id),
                        "iteration_no": iteration_no,
                        "generator_tool_call_id": str(call.id),
                        "occurrence_id": str(occurrence.id),
                        "sequence_sha256": sequence_sha256,
                        "event_idempotency_key": event_idempotency_key,
                    },
                    idempotency_key=event_idempotency_key,
                )
                rejected_duplicates.append(
                    {
                        **occurrence_metadata,
                        "occurrence_id": str(occurrence.id),
                        "sequence": sequence,
                        "sequence_sha256": sequence_sha256,
                    }
                )
                continue
            accepted_source_ids.update(
                uuid.UUID(str(row["parent_candidate_id"]))
                for row in sources
                if row.get("parent_candidate_id") is not None
            )
            edges = await repository.record_candidate_lineage(
                action_id=action.id,
                child_candidate_id=child.id,
                sources=item["lineage_sources"],
            )
            await repository.record_candidate_occurrence(
                run_id=run_id,
                tool_call_id=call.id,
                parent_candidate_id=anchor,
                occurrence_rank=int(action.action_ordinal),
                occurrence_kind=action.action_kind,
                opaque_arm_label=action.branch_key,
                sequence=child.sequence,
                candidate_id=child.id,
                metadata={
                    "action_id": str(action.id),
                    "edge_sha256s": [edge.edge_sha256 for edge in edges],
                },
            )
            children.append(
                {
                    "id": str(child.id),
                    "sequence": child.sequence,
                    "sequence_sha256": child.sequence_sha256,
                    "generation": child.generation,
                    "action_id": str(action.id),
                    "repository_action_sha256": action.action_sha256,
                    "runtime_action_sha256": item["runtime_action_sha256"],
                    "executor_action_sha256": result.get("executor_action_sha256"),
                    "lineage_edge_sha256s": [edge.edge_sha256 for edge in edges],
                }
            )
        if not children:
            if len(rejected_duplicates) != len(plan["actions"]):
                raise ValueError(
                    "AutoResearch produced no unique children without duplicate evidence"
                )
            stop_reason = "no_unique_children_after_duplicate_rejection"
            event_idempotency_key = sha256_json(
                {
                    "event": "autoresearch.iteration.noop",
                    "run_id": str(run_id),
                    "iteration_no": iteration_no,
                    "action_batch_sha256": plan["action_batch_sha256"],
                }
            )
            await repository.append_event(
                "run",
                run_id,
                "autoresearch.iteration.noop",
                "autoresearch-action-executor",
                {
                    "run_id": str(run_id),
                    "iteration_no": iteration_no,
                    "action_batch_sha256": plan["action_batch_sha256"],
                    "status": "iteration_noop",
                    "stop_reason": stop_reason,
                    "proposed_child_count": len(plan["actions"]),
                    "unique_child_count": 0,
                    "rejected_duplicate_count": len(rejected_duplicates),
                    "rejected_action_ids": sorted(
                        item["action_id"] for item in rejected_duplicates
                    ),
                    "event_idempotency_key": event_idempotency_key,
                },
                idempotency_key=event_idempotency_key,
            )
        else:
            stop_reason = None
    generated_parent_controls = list(generated.get("parent_controls") or [])
    parent_control_ids = {
        uuid.UUID(str(item["id"])) for item in generated_parent_controls
    }
    if not accepted_source_ids <= parent_control_ids:
        raise ValueError("AutoResearch unique child controls are incomplete")
    parent_controls = [
        item
        for item in generated_parent_controls
        if uuid.UUID(str(item["id"])) in accepted_source_ids
    ]
    score_all_candidates = [
        *parent_controls,
        *children,
    ]
    if len({item["id"] for item in score_all_candidates}) != len(score_all_candidates):
        raise ValueError("AutoResearch score-all parent/child cohort is duplicated")
    result = {
        "schema_version": "ampgent.autoresearch-children-receipt.2",
        "run_id": str(run_id),
        "iteration_no": iteration_no,
        "generator_tool_call_id": str(generator_tool_call_id),
        "generator_output_sha256": stored.sha256,
        "proposed_child_count": len(plan["actions"]),
        "candidate_count": len(children),
        "candidates": children,
        "rejected_duplicate_count": len(rejected_duplicates),
        "rejected_duplicates": rejected_duplicates,
        "iteration_noop": not children,
        "stop_reason": stop_reason,
        "parent_control_count": len(parent_controls),
        "parent_controls": parent_controls,
        "score_all_candidate_count": len(score_all_candidates),
        "score_all_candidates": score_all_candidates,
    }
    if request.get("temporal_payload_mode") != "reference_v1":
        return result
    receipt_stored = await _store_json(result)
    async with SessionFactory() as session, session.begin():
        receipt_artifact = await _register_artifact(
            session,
            generator_tool_call_id,
            asdict(receipt_stored),
            "autoresearch_children_receipt",
            {
                "iteration_no": iteration_no,
                "candidate_count": len(children),
                "score_all_candidate_count": len(score_all_candidates),
            },
        )
    reference = _temporal_payload_reference(
        receipt_stored,
        role="children_receipt",
        summary={
            "run_id": str(run_id),
            "iteration_no": iteration_no,
            "generator_tool_call_id": str(generator_tool_call_id),
            "artifact_id": str(receipt_artifact.id),
            "proposed_child_count": len(plan["actions"]),
            "candidate_count": len(children),
            "candidate_ids": [item["id"] for item in children],
            "rejected_duplicate_count": len(rejected_duplicates),
            "iteration_noop": not children,
            "stop_reason": stop_reason,
            "parent_control_count": len(parent_controls),
            "parent_control_ids": [item["id"] for item in parent_controls],
            "score_all_candidate_count": len(score_all_candidates),
            "score_all_candidate_ids": [item["id"] for item in score_all_candidates],
        },
    )
    if not children:
        reference["rejected_duplicates"] = rejected_duplicates
    return reference


def _tool_contract(call: ToolCall) -> tuple[Any, ...]:
    return (
        call.tool_name,
        call.tool_version,
        call.model_uri,
        call.weights_sha256,
        call.environment_sha256,
    )


def _label_is_non_toxin(value: str | None) -> bool:
    normalized = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    return normalized in {"non-toxin", "non-toxic", "nontoxic"}


def _label_is_low_hemolysis(value: str | None) -> bool:
    return str(value or "").strip().lower() == "low"


def _select_complete_evidence(
    *,
    candidates: list[Candidate],
    evaluations: list[Evaluation],
    calls: dict[uuid.UUID, ToolCall],
    required_metrics: set[str],
) -> list[CandidateEvidence]:
    """Project the latest complete 12-metric rows into planner/archive evidence."""

    selected: dict[uuid.UUID, dict[str, Evaluation]] = {}
    for candidate in candidates:
        by_metric: dict[str, list[Evaluation]] = defaultdict(list)
        for row in evaluations:
            if (
                row.candidate_id == candidate.id
                and row.metric_name in required_metrics
                and row.status == EvaluationStatus.SUCCEEDED
            ):
                by_metric[row.metric_name].append(row)
        if all(by_metric.get(metric_name) for metric_name in required_metrics):
            selected[candidate.id] = {
                metric_name: sorted(by_metric[metric_name], key=lambda row: str(row.id))[-1]
                for metric_name in required_metrics
            }
    family_by_sequence = {
        item.sequence: item.family_key
        for item in cluster_sequence_families(
            candidate.sequence for candidate in candidates if candidate.id in selected
        )
    }
    evidence: list[CandidateEvidence] = []
    for candidate in candidates:
        rows = selected.get(candidate.id)
        if rows is None:
            continue
        metrics: dict[str, MetricObservation] = {}
        for metric_name, row in rows.items():
            if row.numeric_value is None:
                continue
            call = calls.get(row.tool_call_id)
            if call is None:
                raise ValueError("planner evaluation lacks its frozen ToolCall")
            direction = _METRIC_DIRECTIONS[metric_name]
            if direction not in {"minimize", "maximize"}:
                continue
            metrics[metric_name] = MetricObservation(
                numeric_value=float(row.numeric_value),
                direction=direction,
                unit=row.unit or "dimensionless",
                version="|".join(str(item or "none") for item in _tool_contract(call)),
                out_of_domain=bool(row.out_of_domain),
            )
        instability = rows["guruprasad_instability_index"].numeric_value
        eligible = (
            instability is not None
            and float(instability) < 50.0
            and _label_is_non_toxin(rows["toxinpred3_label"].text_value)
            and _label_is_low_hemolysis(rows["macrel_hemolysis_label"].text_value)
        )
        evidence.append(
            CandidateEvidence(
                candidate_id=str(candidate.id),
                sequence=candidate.sequence,
                sequence_sha256=candidate.sequence_sha256,
                family_key=family_by_sequence[candidate.sequence],
                metrics=metrics,
                archive_eligible=eligible,
            )
        )
    return evidence


def _bounded_bundle_path(root: Path, relative_path: str) -> Path:
    resolved_root = root.resolve(strict=True)
    normalized = safe_relative_score_bundle_path(relative_path)
    candidate = (resolved_root / Path(*PurePosixPath(normalized).parts)).resolve(strict=True)
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise ValueError("score bundle cache path escapes its bounded root") from error
    return candidate


def _bounded_bundle_reader(root: Path) -> Any:
    resolved_root = root.resolve(strict=True)

    def read_bytes(relative_path: str) -> bytes:
        return _bounded_bundle_path(resolved_root, relative_path).read_bytes()

    return read_bytes


def _heartbeat_seed_import(stage: str, completed: int = 0, total: int | None = None) -> None:
    activity.heartbeat(
        {
            "schema_version": "ampgent.autoresearch-seed-import-heartbeat.1",
            "stage": stage,
            "completed": completed,
            "total": total,
        }
    )


@activity.defn(name="persist_autoresearch_score_all_bundle")
async def persist_autoresearch_score_all_bundle(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Import a fully verified CAS raw/formal12 bundle as generation-zero evidence."""

    run_id = uuid.UUID(str(request["run_id"]))
    target_key = str(request["target_key"])
    root = Path(str(request["bundle_cache_root"]))
    receipt_relative_path = safe_relative_score_bundle_path(str(request["bundle_receipt_path"]))
    source_map_relative_path = safe_relative_score_bundle_path(
        str(request["source_map_receipt_path"])
    )
    disk = await asyncio.to_thread(lambda: shutil.disk_usage(root.resolve(strict=True)))
    cache_read_bytes = _bounded_bundle_reader(root)
    receipt_file = _bounded_bundle_path(root, receipt_relative_path)
    bundle_read_bytes = _bounded_bundle_reader(receipt_file.parent)
    receipt_member_path = receipt_file.name
    _heartbeat_seed_import("read_receipts")
    receipt_bytes = await asyncio.to_thread(cache_read_bytes, receipt_relative_path)
    try:
        receipt = json.loads(receipt_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("score bundle receipt is not valid UTF-8 JSON") from error
    receipt_sha256 = str(request["bundle_receipt_sha256"])
    if sha256_bytes(receipt_bytes) != receipt_sha256:
        raise OSError("score bundle receipt SHA-256 mismatch")
    source_map_bytes = await asyncio.to_thread(cache_read_bytes, source_map_relative_path)
    try:
        source_map_payload = json.loads(source_map_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("score source-map receipt is not valid UTF-8 JSON") from error
    source_map_sha256 = str(request["source_map_receipt_sha256"])
    source_map = await asyncio.to_thread(
        validate_score_source_map_receipt,
        receipt=source_map_payload,
        receipt_sha256=source_map_sha256,
        receipt_bytes=source_map_bytes,
        source_run_id=str(receipt.get("run_id") or ""),
        bundle_receipt_sha256=receipt_sha256,
    )
    _heartbeat_seed_import("validate_source_map")
    source_map_storage_uri = str(request["source_map_storage_uri"])
    if not source_map_storage_uri.startswith("ssh://") or (
        f"/{source_map_sha256}/" not in source_map_storage_uri
    ):
        raise ValueError("score source-map storage URI must be canonical remote SSH CAS")
    validated = await asyncio.to_thread(
        validate_score_all_bundle,
        bundle_receipt=receipt,
        bundle_receipt_sha256=receipt_sha256,
        bundle_receipt_bytes=receipt_bytes,
        bundle_receipt_relative_path=receipt_member_path,
        target_key=target_key,
        source_result_mappings=source_map.source_result_mappings,
        read_bytes=bundle_read_bytes,
    )
    _heartbeat_seed_import(
        "validate_bundle", len(validated.primary_rows), len(validated.primary_rows)
    )
    control_environment_sha256 = str(request["control_environment_sha256"])
    if len(control_environment_sha256) != 64 or set(control_environment_sha256) - set(
        "0123456789abcdef"
    ):
        raise ValueError("score bundle import environment identity is invalid")
    summary = {
        "schema_version": "ampgent.autoresearch-score-all-import.1",
        "source_run_id": validated.source_run_id,
        "target_key": validated.target_key,
        "bundle_receipt_sha256": validated.receipt_sha256,
        "manifest_sha256": validated.manifest_sha256,
        "manifest_file_count": len(validated.all_manifest_files),
        "source_map_receipt_sha256": source_map.receipt_sha256,
        "target_raw_count": len(validated.raw_rows),
        "target_formal12_row_count": len(validated.primary_rows),
        "target_strict_count": len(validated.strict_sequence_sha256s),
        "formal_metric_names": list(FORMAL_SCORE_COLUMNS),
        "cache_disk_free_bytes_observed": disk.free,
        "strict_subset_used_as_raw": False,
        "source_runtime_attestation_complete": (validated.runtime_attestation_complete),
    }
    async with SessionFactory() as session, session.begin():
        run = await session.get(ExperimentRun, run_id)
        if run is None or run.status != RunStatus.RUNNING:
            raise ValueError("score bundle import requires a new running run")
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            "autoresearch-cas-score-all-import",
            "1",
            control_environment_sha256,
            {
                "source_storage_uri": validated.storage_uri,
                "bundle_receipt_sha256": validated.receipt_sha256,
                "manifest_sha256": validated.manifest_sha256,
                "source_map_receipt_sha256": source_map.receipt_sha256,
                "target_key": target_key,
            },
            {
                "formal_metric_names": list(FORMAL_SCORE_COLUMNS),
                "full_manifest_readback": True,
                "strict_subset_used_as_raw": False,
                "source_runtime": validated.runtime,
                "source_result_mappings": source_map.source_result_mappings,
            },
            summary,
            attempt=activity.info().attempt,
            model_uri=validated.storage_uri,
            logical_stage="autoresearch_seed_score_all_import",
            display_category="metric",
        )
        artifact_specs = [
            (path, digest, "autoresearch_score_bundle_member")
            for path, digest in validated.all_manifest_files
        ]
        manifest_path = str(receipt["manifest"]["path"])
        if manifest_path not in {item[0] for item in artifact_specs}:
            artifact_specs.append(
                (
                    manifest_path,
                    validated.manifest_sha256,
                    "autoresearch_score_bundle_manifest",
                )
            )
        for artifact_ordinal, (path, digest, role) in enumerate(artifact_specs, start=1):
            payload = await asyncio.to_thread(bundle_read_bytes, path)
            media_type = (
                "text/csv; charset=utf-8"
                if path.lower().endswith(".csv")
                else "application/json"
                if path.lower().endswith(".json")
                else "text/plain; charset=utf-8"
            )
            await _register_artifact(
                session,
                call.id,
                {
                    "sha256": digest,
                    "size_bytes": len(payload),
                    "media_type": media_type,
                    "uri": f"{validated.storage_uri}{path}",
                },
                role,
                {
                    "source_run_id": validated.source_run_id,
                    "target_key": target_key,
                    "relative_path": path,
                    "bundle_receipt_sha256": validated.receipt_sha256,
                },
            )
            if artifact_ordinal % 16 == 0:
                _heartbeat_seed_import(
                    "register_bundle_artifacts", artifact_ordinal, len(artifact_specs)
                )
        await _register_artifact(
            session,
            call.id,
            {
                "sha256": validated.receipt_sha256,
                "size_bytes": len(receipt_bytes),
                "media_type": "application/json",
                "uri": f"{validated.storage_uri}{receipt_member_path}",
            },
            "autoresearch_score_bundle_receipt",
            {
                "source_run_id": validated.source_run_id,
                "target_key": target_key,
                "relative_cache_path": receipt_relative_path,
                "bundle_receipt_sha256": validated.receipt_sha256,
            },
        )
        await _register_artifact(
            session,
            call.id,
            {
                "sha256": source_map.receipt_sha256,
                "size_bytes": len(source_map_bytes),
                "media_type": "application/json",
                "uri": source_map_storage_uri,
            },
            "autoresearch_score_source_map_receipt",
            {
                "source_run_id": validated.source_run_id,
                "target_key": target_key,
                "bundle_receipt_sha256": validated.receipt_sha256,
                "relative_cache_path": source_map_relative_path,
            },
        )

        strict_sha = set(validated.strict_sequence_sha256s)
        candidate_by_sequence_sha: dict[str, Candidate] = {}
        primary_by_sequence_sha: dict[str, dict[str, str]] = {}
        for ordinal, row in enumerate(validated.primary_rows, start=1):
            digest = str(row["sequence_sha256"])
            prior = primary_by_sequence_sha.get(digest)
            if prior is not None and any(prior[name] != row[name] for name in FORMAL_SCORE_COLUMNS):
                raise ValueError("duplicate sequence has conflicting formal12 scores")
            primary_by_sequence_sha.setdefault(digest, row)
            if digest in candidate_by_sequence_sha:
                continue
            metadata = {
                "schema_version": "ampgent.autoresearch-cas-seed.1",
                "target_key": target_key,
                "source_run_id": validated.source_run_id,
                "source_candidate_id": row["candidate_id"],
                "source_result": row["source_result"],
                "source_result_sha256": row["source_result_sha256"],
                "source_action": {
                    "action_id": row["action_id"],
                    "action_kind": row["action_kind"],
                    "action_seed": row["action_seed"],
                    "action_sha256": row["action_sha256"],
                    "primary_parent_id": row["primary_parent_id"],
                    "donor_candidate_id": row["donor_candidate_id"],
                    "lineage": row["lineage"],
                },
                "bundle_receipt_sha256": validated.receipt_sha256,
                "strict_display_eligible": digest in strict_sha,
                "guruprasad_instability_ood": str(row[GURUPRASAD_OOD_COLUMN]).strip().lower()
                in {"1", "true", "yes", "y"},
                "source_runtime_attestation_complete": (validated.runtime_attestation_complete),
            }
            candidate = await repository.add_candidate(
                run_id,
                row["sequence"],
                generation=0,
                proposal_rank=ordinal,
                generator_call_id=call.id,
                metadata=metadata,
                actor="autoresearch-cas-score-all-import",
            )
            if candidate.sequence_sha256 != digest or candidate.generation != 0:
                raise ValueError("CAS seed candidate collides with incompatible run evidence")
            if candidate.metadata_json.get("bundle_receipt_sha256") != validated.receipt_sha256:
                raise ValueError("CAS seed candidate retry identity drifted")
            candidate_by_sequence_sha[digest] = candidate
            if ordinal % 64 == 0:
                _heartbeat_seed_import("persist_candidates", ordinal, len(validated.primary_rows))

        for occurrence_rank, row in enumerate(validated.raw_rows, start=1):
            candidate = candidate_by_sequence_sha[str(row["sequence_sha256"])]
            await repository.record_candidate_occurrence(
                run_id=run_id,
                tool_call_id=call.id,
                parent_candidate_id=None,
                occurrence_rank=occurrence_rank,
                occurrence_kind="de_novo",
                opaque_arm_label=target_key,
                sequence=row["sequence"],
                candidate_id=candidate.id,
                metadata={
                    "source_occurrence_kind": row["action_kind"],
                    "source_candidate_id": row["candidate_id"],
                    "source_action_id": row["action_id"],
                    "source_action_sha256": row["action_sha256"],
                    "source_action_plan_sha256": row["source_action_plan_sha256"],
                    "source_result": row["source_result"],
                    "source_result_sha256": row["source_result_sha256"],
                    "source_raw_rank": row["raw_rank"],
                    "source_lineage": row["lineage"],
                    "bundle_receipt_sha256": validated.receipt_sha256,
                },
            )
            if occurrence_rank % 64 == 0:
                _heartbeat_seed_import(
                    "persist_occurrences", occurrence_rank, len(validated.raw_rows)
                )

        evaluation_rows: list[dict[str, Any]] = []
        for digest, row in primary_by_sequence_sha.items():
            candidate = candidate_by_sequence_sha[digest]
            for metric_name in FORMAL_SCORE_COLUMNS:
                is_label = metric_name in {
                    "toxinpred3_label",
                    "macrel_hemolysis_label",
                }
                numeric_value = None if is_label else float(row[metric_name])
                text_value = row[metric_name] if is_label else None
                out_of_domain = metric_name == "guruprasad_instability_index" and (
                    str(row[GURUPRASAD_OOD_COLUMN]).strip().lower() in {"1", "true", "yes", "y"}
                )
                limitations = ["imported_from_fully_verified_cas_score_all_bundle"]
                if not validated.runtime_attestation_complete:
                    limitations.append("source_runtime_attestation_partial")
                if out_of_domain:
                    limitations.append("source_marks_guruprasad_instability_ood")
                evaluation_rows.append(
                    {
                        "candidate_id": candidate.id,
                        "metric_name": metric_name,
                        "numeric_value": numeric_value,
                        "unit": _IMPORTED_METRIC_UNITS[metric_name],
                        "raw": {
                            "schema_version": "ampgent.autoresearch-cas-score.1",
                            "source_run_id": validated.source_run_id,
                            "target_key": target_key,
                            "source_candidate_id": row["candidate_id"],
                            "source_result_sha256": row["source_result_sha256"],
                            "bundle_receipt_sha256": validated.receipt_sha256,
                            "manifest_sha256": validated.manifest_sha256,
                            "runtime": validated.runtime,
                        },
                        "text_value": text_value,
                        "out_of_domain": out_of_domain,
                        "limitations": limitations,
                    }
                )
        evaluation_count = 0
        for offset in range(0, len(evaluation_rows), 512):
            batch = evaluation_rows[offset : offset + 512]
            await repository.record_evaluations_bulk(call.id, batch)
            evaluation_count += len(batch)
            _heartbeat_seed_import("persist_evaluations", evaluation_count, len(evaluation_rows))
        if evaluation_count != len(candidate_by_sequence_sha) * 12:
            raise ValueError("CAS import did not persist candidate x 12 formal evaluations")
        durable_candidate_count = int(
            await session.scalar(
                select(func.count(Candidate.id)).where(
                    Candidate.run_id == run_id,
                    Candidate.generation == 0,
                )
            )
            or 0
        )
        durable_occurrence_count = int(
            await session.scalar(
                select(func.count(CandidateOccurrence.id)).where(
                    CandidateOccurrence.run_id == run_id,
                    CandidateOccurrence.tool_call_id == call.id,
                )
            )
            or 0
        )
        durable_evaluation_count = int(
            await session.scalar(
                select(func.count(Evaluation.id)).where(
                    Evaluation.candidate_id.in_(
                        [item.id for item in candidate_by_sequence_sha.values()]
                    ),
                    Evaluation.tool_call_id == call.id,
                )
            )
            or 0
        )
    if durable_occurrence_count != len(validated.raw_rows):
        raise ValueError("CAS import durable occurrence count drifted")
    if durable_evaluation_count != len(candidate_by_sequence_sha) * 12:
        raise ValueError("CAS import durable formal evaluation count drifted")
    return {
        **summary,
        "tool_call_id": str(call.id),
        "candidate_count": len(candidate_by_sequence_sha),
        "occurrence_count": durable_occurrence_count,
        "evaluation_count": durable_evaluation_count,
        "run_generation_zero_candidate_count": durable_candidate_count,
    }


@activity.defn(name="plan_autoresearch_actions")
async def plan_autoresearch_actions(request: dict[str, Any]) -> dict[str, Any]:
    """Plan one minimum executable batch from independent fronts and prior deltas."""

    if request.get("schema_version") != "ampgent.autoresearch-planner-request.1":
        raise ValueError("AutoResearch planner request schema is not frozen")
    request = await _hydrate_planner_request(request)
    run_id = uuid.UUID(str(request["run_id"]))
    iteration_no = int(request["iteration_no"])
    branch_key = str(request["branch_key"])
    contract = V38SequenceExecutionContract.model_validate(request["execution_contract"])
    required_metrics = set(contract.required_sequence_metrics)
    if len(required_metrics) != 12:
        raise ValueError("AutoResearch planner requires complete 12-metric evidence")
    archive_policy = MultiFrontArchivePolicy.model_validate(request["archive_policy"])
    continuation_policy = ContinuationPolicy.model_validate(request["continuation_policy"])
    if continuation_policy.minimum_high_quality_candidates < GOLD_CANDIDATE_TARGET:
        raise ValueError("AutoResearch target branches require at least 50 gold candidates")
    planner_contract = dict(request.get("planner_contract") or {})
    operator_release_sha256 = str(request["operator_release_sha256"])
    control_environment_sha256 = str(request["control_environment_sha256"])
    for name, value in (
        ("operator release", operator_release_sha256),
        ("control environment", control_environment_sha256),
    ):
        if len(value) != 64 or set(value) - set("0123456789abcdef"):
            raise ValueError(f"AutoResearch planner {name} identity is invalid")

    async with SessionFactory() as session:
        run = await session.get(ExperimentRun, run_id)
        if run is None or run.status != RunStatus.RUNNING:
            raise ValueError("AutoResearch planner requires a new running run")
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == run_id, Candidate.generation <= iteration_no)
                .order_by(Candidate.generation, Candidate.proposal_rank, Candidate.id)
            )
        )
        if not candidates:
            raise ValueError("AutoResearch planner requires persisted seed parents")
        candidate_ids = [item.id for item in candidates]
        evaluations = list(
            await session.scalars(
                select(Evaluation).where(Evaluation.candidate_id.in_(candidate_ids))
            )
        )
        calls = {
            item.id: item
            for item in await session.scalars(
                select(ToolCall).where(ToolCall.id.in_({row.tool_call_id for row in evaluations}))
            )
        }
        evidence = _select_complete_evidence(
            candidates=candidates,
            evaluations=evaluations,
            calls=calls,
            required_metrics=required_metrics,
        )
        if not evidence:
            raise ValueError("AutoResearch planner has no complete 12-metric parent")
        previous, _ = await _load_previous_snapshot(
            session,
            run_id=run_id,
            iteration_no=iteration_no,
        )
        snapshot = previous or build_multi_front_archive(
            evidence,
            archive_policy,
            generation=iteration_no,
        )
        deltas = list(
            await session.scalars(
                select(AutoResearchMetricDelta)
                .where(AutoResearchMetricDelta.child_candidate_id.in_(candidate_ids))
                .order_by(
                    AutoResearchMetricDelta.child_candidate_id,
                    AutoResearchMetricDelta.metric_name,
                    AutoResearchMetricDelta.id,
                )
            )
        )
        delta_evidence = tuple(
            PlannerDeltaEvidence(
                candidate_id=str(item.child_candidate_id),
                metric_name=item.metric_name,
                delta_sha256=item.delta_sha256,
                improved=bool(item.improved),
            )
            for item in deltas
        )
        plan = build_multifront_rule_action_plan(
            candidates=evidence,
            snapshot=snapshot,
            branch_key=branch_key,
            generation=iteration_no + 1,
            seed=int(planner_contract.get("seed", 104729 + iteration_no * 1009)),
            operator_release_sha256=operator_release_sha256,
            target_sequence_sha256=str(request["target_sequence_sha256"]),
            prior_deltas=delta_evidence,
            gold_target=max(
                GOLD_CANDIDATE_TARGET,
                int(continuation_policy.minimum_high_quality_candidates),
            ),
            de_novo_quota=float(planner_contract.get("de_novo_quota", 0.2)),
        )

    planner_payload = {
        "schema_version": "ampgent.autoresearch-rule-planner-evidence.1",
        "run_id": str(run_id),
        "iteration_no": iteration_no,
        "branch_key": branch_key,
        "snapshot": snapshot.model_dump(mode="json"),
        "prior_delta_sha256s": sorted(item.delta_sha256 for item in delta_evidence),
        "plan": plan,
    }
    stored = await _store_json(planner_payload)
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            "autoresearch-multi-front-rule-planner",
            "1",
            control_environment_sha256,
            {
                "iteration_no": iteration_no,
                "archive_sha256": snapshot.archive_sha256,
                "prior_delta_sha256s": planner_payload["prior_delta_sha256s"],
            },
            {
                "gold_target": plan["gold_target"],
                "gold_candidate_count": plan["gold_candidate_count"],
                "strategies": plan["strategies"],
                "no_weighted_total_score": True,
            },
            planner_payload,
            attempt=activity.info().attempt,
            logical_stage="autoresearch_planning",
            display_category="decision",
        )
        artifact = await _register_artifact(
            session,
            call.id,
            asdict(stored),
            "autoresearch_rule_plan",
            {
                "iteration_no": iteration_no,
                "archive_sha256": snapshot.archive_sha256,
            },
        )
    prompt = _canonical_json(
        {
            "branch_key": branch_key,
            "iteration_no": iteration_no,
            "archive_sha256": snapshot.archive_sha256,
            "gold_target": plan["gold_target"],
            "gold_candidate_count": plan["gold_candidate_count"],
            "strategy_contract": (
                "retain_conflicting_endpoints_and_family_novelty_without_weighted_score"
            ),
        }
    )
    result = {
        "schema_version": "ampgent.autoresearch-planner-result.1",
        "agent_decision": {
            "agent_name": "autoresearch-multi-front-rule-planner",
            "agent_version": "1",
            "model_name": None,
            "prompt_text": prompt,
            "response_text": _canonical_json(plan),
            "rationale_by_action_sha256": plan["rationale_by_action_sha256"],
            "planner_tool_call_id": str(call.id),
        },
        "actions": plan["actions"],
        "planner_receipt": {
            "tool_call_id": str(call.id),
            "artifact_id": str(artifact.id),
            "artifact_sha256": stored.sha256,
            "archive_sha256": snapshot.archive_sha256,
            "strategies": plan["strategies"],
        },
    }
    if request.get("temporal_payload_mode") != "reference_v1":
        return result
    return _temporal_payload_reference(
        stored,
        role="planner_result",
        summary={
            "run_id": str(run_id),
            "iteration_no": iteration_no,
            "branch_key": branch_key,
            "planner_tool_call_id": str(call.id),
            "artifact_id": str(artifact.id),
            "archive_sha256": snapshot.archive_sha256,
            "action_count": len(plan["actions"]),
        },
    )


def _build_replay_bundle(
    *,
    run_id: uuid.UUID,
    iteration_no: int,
    action_plan: dict[str, Any],
    children: dict[str, Any],
    metric_tool_call_ids: list[str],
    delta_receipts: list[dict[str, Any]],
    archive_update: dict[str, Any],
    archive_versions: dict[str, str],
    continuation_decision_id: uuid.UUID,
) -> dict[str, Any]:
    """Build the canonical replay payload shared by runtime and tests."""

    return {
        "schema_version": "ampgent.autoresearch-iteration-replay.1",
        "run_id": str(run_id),
        "iteration_no": iteration_no,
        "action_plan": {
            "agent_decision_id": action_plan["agent_decision_id"],
            "action_batch_sha256": action_plan["action_batch_sha256"],
            "planner_receipt": action_plan.get("planner_receipt"),
            "actions": [
                {
                    "action_id": item["action_id"],
                    "repository_action_sha256": item["repository_action_sha256"],
                    "runtime_action_sha256": item["runtime_action_sha256"],
                    "runtime_action": item["runtime_action"],
                }
                for item in action_plan["actions"]
            ],
        },
        "children": children["candidates"],
        "rejected_duplicates": children.get("rejected_duplicates") or [],
        "parent_controls": children.get("parent_controls") or [],
        "score_all": {
            "required_metric_count": 12,
            "candidate_count": int(
                children.get("score_all_candidate_count", children["candidate_count"])
            ),
            "child_candidate_count": int(children["candidate_count"]),
            "completed_evaluation_count": int(
                children.get("score_all_candidate_count", children["candidate_count"])
            )
            * 12,
            "metric_tool_call_ids": sorted(metric_tool_call_ids),
        },
        "metric_deltas": sorted(delta_receipts, key=lambda item: item["delta_sha256"]),
        "archive_update": archive_update,
        "archive_version_ids": dict(sorted(archive_versions.items())),
        "continuation_decision_id": str(continuation_decision_id),
    }


async def _load_previous_snapshot(
    session: Any,
    *,
    run_id: uuid.UUID,
    iteration_no: int,
) -> tuple[MultiFrontArchiveSnapshot | None, dict[str, AutoResearchArchiveVersion]]:
    versions: dict[str, AutoResearchArchiveVersion] = {}
    for name in ARCHIVE_NAMES:
        row = await session.scalar(
            select(AutoResearchArchiveVersion)
            .where(
                AutoResearchArchiveVersion.run_id == run_id,
                AutoResearchArchiveVersion.archive_name == name,
                AutoResearchArchiveVersion.iteration_no < iteration_no,
            )
            .order_by(AutoResearchArchiveVersion.iteration_no.desc())
            .limit(1)
        )
        if row is not None:
            versions[name] = row
    if not versions:
        return None, {}
    if set(versions) != set(ARCHIVE_NAMES):
        raise ValueError("previous AutoResearch archive is only partially persisted")
    artifacts = {
        row.snapshot_artifact_id: await session.get(Artifact, row.snapshot_artifact_id)
        for row in versions.values()
    }
    if len(artifacts) != 1 or any(item is None for item in artifacts.values()):
        raise ValueError("previous AutoResearch archive snapshot identity drifted")
    artifact = next(iter(artifacts.values()))
    assert artifact is not None
    payload = await asyncio.to_thread(ContentAddressedObjectStore().get_bytes, artifact.storage_uri)
    if sha256_bytes(payload) != artifact.sha256:
        raise OSError("previous AutoResearch archive snapshot hash mismatch")
    decoded = json.loads(payload.decode("utf-8"))
    snapshot_payload = decoded.get("snapshot")
    if not isinstance(snapshot_payload, dict):
        raise ValueError("previous AutoResearch archive snapshot payload is malformed")
    return parse_persisted_archive_snapshot(snapshot_payload), versions


@activity.defn(name="finalize_autoresearch_iteration")
async def finalize_autoresearch_iteration(request: dict[str, Any]) -> dict[str, Any]:
    """Close one generation with deltas, archive turnover, checkpoint, and replay."""

    run_id = uuid.UUID(str(request["run_id"]))
    iteration_no = int(request["iteration_no"])
    action_plan = await _resolve_action_plan_reference(request["action_plan"])
    children_receipt = await _resolve_children_reference(request["children"])
    child_ids = [uuid.UUID(str(item["id"])) for item in children_receipt["candidates"]]
    score_all_ids = [
        uuid.UUID(str(item["id"]))
        for item in children_receipt.get("score_all_candidates", children_receipt["candidates"])
    ]
    if not set(child_ids) <= set(score_all_ids):
        raise ValueError("AutoResearch score-all cohort omits a child")
    if bool(request.get("hydrate_from_run_spec")):
        workflow_request = await _load_run_workflow_request(run_id)
        request = {
            **request,
            "execution_contract": workflow_request["execution_contract"],
            "archive_policy": workflow_request["archive_policy"],
            "continuation_policy": workflow_request["continuation_policy"],
            "control_environment_sha256": workflow_request["control_environment_sha256"],
        }
    contract = V38SequenceExecutionContract.model_validate(request["execution_contract"])
    if len(contract.required_sequence_metrics) != 12:
        raise ValueError("AutoResearch finalization requires the frozen 12 metrics")
    archive_policy = MultiFrontArchivePolicy.model_validate(request["archive_policy"])
    continuation_policy = ContinuationPolicy.model_validate(request["continuation_policy"])
    metric_tool_call_ids = [uuid.UUID(str(item)) for item in request["metric_tool_call_ids"]]
    if len(metric_tool_call_ids) != len(contract.metric_plugins):
        raise ValueError("AutoResearch finalization requires all metric plugin calls")

    async with SessionFactory() as session, session.begin():
        run = await session.get(ExperimentRun, run_id)
        if run is None or run.status != RunStatus.RUNNING:
            raise ValueError("AutoResearch finalization requires a new running run")
        repository = ExperimentRepository(session)
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(
                    Candidate.run_id == run_id,
                    Candidate.generation <= iteration_no + 1,
                )
                .order_by(Candidate.generation, Candidate.proposal_rank, Candidate.id)
            )
        )
        if not set(child_ids) <= {item.id for item in candidates}:
            raise ValueError("AutoResearch child receipt differs from the database")
        candidate_ids = [item.id for item in candidates]
        evaluations = list(
            await session.scalars(
                select(Evaluation).where(Evaluation.candidate_id.in_(candidate_ids))
            )
        )
        calls = {
            item.id: item
            for item in await session.scalars(
                select(ToolCall).where(
                    ToolCall.id.in_({evaluation.tool_call_id for evaluation in evaluations})
                )
            )
        }
        required_metrics = set(contract.required_sequence_metrics)
        selected: dict[uuid.UUID, dict[str, Evaluation]] = {}
        for candidate in candidates:
            rows = [item for item in evaluations if item.candidate_id == candidate.id]
            by_metric: dict[str, list[Evaluation]] = {}
            for row in rows:
                if row.metric_name in required_metrics and row.status == EvaluationStatus.SUCCEEDED:
                    by_metric.setdefault(row.metric_name, []).append(row)
            projection: dict[str, Evaluation] = {}
            for metric_name in required_metrics:
                matches = by_metric.get(metric_name, [])
                if candidate.id in score_all_ids:
                    matches = [row for row in matches if row.tool_call_id in metric_tool_call_ids]
                if matches:
                    projection[metric_name] = sorted(matches, key=lambda row: str(row.id))[-1]
            if set(projection) == required_metrics:
                selected[candidate.id] = projection
        if not set(score_all_ids) <= set(selected):
            raise ValueError(
                "AutoResearch checkpoint cannot precede complete parent/child score-all"
            )

        family_by_sequence = {
            item.sequence: item.family_key
            for item in cluster_sequence_families(
                candidate.sequence for candidate in candidates if candidate.id in selected
            )
        }
        evidence: list[CandidateEvidence] = []
        for candidate in candidates:
            rows = selected.get(candidate.id)
            if rows is None:
                continue
            metrics: dict[str, MetricObservation] = {}
            for metric_name, row in rows.items():
                if row.numeric_value is None:
                    continue
                call = calls[row.tool_call_id]
                direction = _METRIC_DIRECTIONS[metric_name]
                if direction not in {"minimize", "maximize"}:
                    continue
                metrics[metric_name] = MetricObservation(
                    numeric_value=float(row.numeric_value),
                    direction=direction,
                    unit=row.unit or "dimensionless",
                    version="|".join(str(item or "none") for item in _tool_contract(call)),
                    out_of_domain=bool(row.out_of_domain),
                )
            instability = rows["guruprasad_instability_index"].numeric_value
            eligible = (
                instability is not None
                and float(instability) < 50.0
                and _label_is_non_toxin(rows["toxinpred3_label"].text_value)
                and _label_is_low_hemolysis(rows["macrel_hemolysis_label"].text_value)
            )
            evidence.append(
                CandidateEvidence(
                    candidate_id=str(candidate.id),
                    sequence=candidate.sequence,
                    sequence_sha256=candidate.sequence_sha256,
                    family_key=family_by_sequence[candidate.sequence],
                    metrics=metrics,
                    archive_eligible=eligible,
                )
            )

        previous, previous_versions = await _load_previous_snapshot(
            session,
            run_id=run_id,
            iteration_no=iteration_no,
        )
        if previous is None:
            baseline = [
                item
                for item in evidence
                if next(
                    candidate for candidate in candidates if str(candidate.id) == item.candidate_id
                ).generation
                <= iteration_no
            ]
            if not baseline:
                raise ValueError("initial AutoResearch archive requires scored seed parents")
            previous = build_multi_front_archive(
                baseline,
                archive_policy,
                generation=iteration_no,
            )
        update = update_multi_front_archive(
            previous,
            evidence,
            archive_policy,
            continuation_policy,
            generation=iteration_no + 1,
            prior_consecutive_stagnant_generations=int(
                request.get("prior_consecutive_stagnant_generations", 0)
            ),
        )

        delta_receipts = []
        actions_by_id = {uuid.UUID(str(item["action_id"])): item for item in action_plan["actions"]}
        lineage_rows = list(
            await session.scalars(
                select(CandidateLineageEdge).where(
                    CandidateLineageEdge.action_id.in_(actions_by_id),
                    CandidateLineageEdge.child_candidate_id.in_(child_ids),
                    CandidateLineageEdge.parent_candidate_id.is_not(None),
                )
            )
        )
        for edge in lineage_rows:
            assert edge.parent_candidate_id is not None
            child_metrics = selected[edge.child_candidate_id]
            parent_options = {
                row.metric_name: [
                    candidate_row
                    for candidate_row in evaluations
                    if candidate_row.candidate_id == edge.parent_candidate_id
                    and candidate_row.metric_name == row.metric_name
                    and candidate_row.status == EvaluationStatus.SUCCEEDED
                    and _tool_contract(calls[candidate_row.tool_call_id])
                    == _tool_contract(calls[row.tool_call_id])
                ]
                for row in child_metrics.values()
            }
            if any(not rows for rows in parent_options.values()):
                raise ValueError("parent-child metric delta lacks a matching frozen tool contract")
            for metric_name in sorted(required_metrics):
                child_evaluation = child_metrics[metric_name]
                parent_evaluation = sorted(
                    parent_options[metric_name], key=lambda row: str(row.id)
                )[-1]
                delta = await repository.record_autoresearch_metric_delta(
                    action_id=edge.action_id,
                    child_candidate_id=edge.child_candidate_id,
                    comparator_candidate_id=edge.parent_candidate_id,
                    metric_name=metric_name,
                    parent_evaluation_id=parent_evaluation.id,
                    child_evaluation_id=child_evaluation.id,
                    direction=_METRIC_DIRECTIONS[metric_name],
                )
                delta_receipts.append(
                    {
                        "delta_id": str(delta.id),
                        "delta_sha256": delta.delta_sha256,
                        "action_id": str(edge.action_id),
                        "child_candidate_id": str(edge.child_candidate_id),
                        "comparator_candidate_id": str(edge.parent_candidate_id),
                        "metric_name": metric_name,
                    }
                )

        snapshot_payload = {
            "schema_version": "ampgent.autoresearch-archive-update-artifact.1",
            "snapshot": update.current.model_dump(mode="json"),
            "update": update.model_dump(mode="json"),
        }
        stored_snapshot = await _store_json(snapshot_payload)
        archive_call = await repository.record_completed_tool_call(
            run_id,
            "autoresearch-multi-front-archive",
            "1",
            str(request["control_environment_sha256"]),
            {
                "iteration_no": iteration_no,
                "previous_archive_sha256": previous.archive_sha256,
                "candidate_ids": update.current.source_candidate_ids,
            },
            {"policy_sha256": archive_policy.sha256(), "no_weighted_total_score": True},
            snapshot_payload,
            logical_stage="autoresearch_archive",
            display_category="decision",
        )
        snapshot_artifact = await _register_artifact(
            session,
            archive_call.id,
            asdict(stored_snapshot),
            "autoresearch_multi_front_archive",
            {"iteration_no": iteration_no, "archive_sha256": update.current.archive_sha256},
        )
        action_by_child = {edge.child_candidate_id: edge.action_id for edge in lineage_rows}
        archive_version_ids: dict[str, str] = {}
        for archive_name in ARCHIVE_NAMES:
            previous_version = previous_versions.get(archive_name)
            previous_active = set()
            if previous_version is not None:
                previous_active = set(
                    await session.scalars(
                        select(AutoResearchArchiveMembership.candidate_id).where(
                            AutoResearchArchiveMembership.archive_version_id == previous_version.id,
                            AutoResearchArchiveMembership.is_active.is_(True),
                        )
                    )
                )
            current_ids = [
                uuid.UUID(value) for value in update.current.archive_members[archive_name]
            ]
            current_set = set(current_ids)
            members = []
            for ordinal, candidate_id in enumerate(current_ids, start=1):
                members.append(
                    {
                        "candidate_id": candidate_id,
                        "change_kind": "retain" if candidate_id in previous_active else "add",
                        "is_active": True,
                        "member_ordinal": ordinal,
                        "source_action_id": action_by_child.get(candidate_id),
                        "reason": update.current.member_reasons[archive_name][str(candidate_id)],
                        "witness_candidate_ids": [],
                    }
                )
            for candidate_id in sorted(previous_active - current_set, key=str):
                members.append(
                    {
                        "candidate_id": candidate_id,
                        "change_kind": "remove",
                        "is_active": False,
                        "member_ordinal": None,
                        "source_action_id": action_by_child.get(candidate_id),
                        "reason": "removed_by_rebuilt_multi_front_archive",
                        "witness_candidate_ids": [],
                    }
                )
            version = await repository.record_autoresearch_archive_version(
                run_id=run_id,
                iteration_no=iteration_no,
                branch_key=str(request["branch_key"]),
                archive_name=archive_name,
                previous_version_id=previous_version.id if previous_version else None,
                policy_sha256=archive_policy.sha256(),
                tool_call_id=archive_call.id,
                snapshot_artifact_id=snapshot_artifact.id,
                memberships=members,
                metadata={"archive_sha256": update.current.archive_sha256},
            )
            archive_version_ids[archive_name] = str(version.id)

        continuation_payload = update.continuation.model_dump(mode="json")
        continuation_decision = await _idempotent_agent_decision(
            session,
            repository,
            run_id=run_id,
            generation=iteration_no,
            decision_type="autoresearch_continuation",
            agent_name="autoresearch-policy-controller",
            agent_version="1",
            model_name=None,
            prompt_text=_canonical_json(
                {
                    "archive_before_sha256": previous.archive_sha256,
                    "archive_after_sha256": update.current.archive_sha256,
                    "continuation_policy": continuation_policy.model_dump(mode="json"),
                }
            ),
            response_text=_canonical_json(continuation_payload),
            structured=continuation_payload,
        )
        replay_bundle = _build_replay_bundle(
            run_id=run_id,
            iteration_no=iteration_no,
            action_plan=action_plan,
            children=children_receipt,
            metric_tool_call_ids=[str(item) for item in metric_tool_call_ids],
            delta_receipts=delta_receipts,
            archive_update=update.model_dump(mode="json"),
            archive_versions=archive_version_ids,
            continuation_decision_id=continuation_decision.id,
        )
        stored_replay = await _store_json(replay_bundle)
        replay_call = await repository.record_completed_tool_call(
            run_id,
            "autoresearch-replay-bundle",
            "1",
            str(request["control_environment_sha256"]),
            {
                "iteration_no": iteration_no,
                "action_batch_sha256": action_plan["action_batch_sha256"],
            },
            {"required_metric_count": 12, "replay_readback_required": True},
            replay_bundle,
            logical_stage="autoresearch_checkpoint",
            display_category="decision",
        )
        replay_artifact = await _register_artifact(
            session,
            replay_call.id,
            asdict(stored_replay),
            "autoresearch_iteration_replay",
            {"iteration_no": iteration_no},
        )
        readback = await asyncio.to_thread(
            ContentAddressedObjectStore().get_bytes, replay_artifact.storage_uri
        )
        if sha256_bytes(readback) != replay_artifact.sha256:
            raise OSError("AutoResearch replay artifact readback failed")
        if json.loads(readback.decode("utf-8")) != replay_bundle:
            raise OSError("AutoResearch replay artifact payload drifted")

        completed_evaluations = len(score_all_ids) * 12
        stage_payload = {
            "schema_version": "ampgent.autoresearch-stage-checkpoint.1",
            "run_id": str(run_id),
            "iteration_no": iteration_no,
            "action_batch_sha256": action_plan["action_batch_sha256"],
            "archive_before_sha256": previous.archive_sha256,
            "archive_after_sha256": update.current.archive_sha256,
            "completed_evaluations": completed_evaluations,
            "next_action": update.continuation.next_action,
            "replay_sha256": replay_artifact.sha256,
        }
        stage = await session.scalar(
            select(RunStageCheckpoint).where(
                RunStageCheckpoint.run_id == run_id,
                RunStageCheckpoint.stage_name == "autoresearch.iteration",
                RunStageCheckpoint.observation_no == iteration_no + 1,
            )
        )
        stage_receipt_sha256 = sha256_json(stage_payload)
        if stage is None:
            stage = RunStageCheckpoint(
                run_id=run_id,
                stage_name="autoresearch.iteration",
                stage_order=1,
                observation_no=iteration_no + 1,
                durable_count=completed_evaluations,
                expected_durable_count=completed_evaluations,
                stage_status="completed",
                controller_action=update.continuation.next_action,
                reasons_json=list(update.continuation.reasons),
                tasks_json=[update.continuation.next_action],
                receipt_sha256=stage_receipt_sha256,
                observed_at=datetime.now(UTC),
            )
            session.add(stage)
            await session.flush()
        elif stage.receipt_sha256 != stage_receipt_sha256:
            raise ValueError("AutoResearch stage checkpoint retry payload drifted")
        checkpoint = await repository.record_autoresearch_checkpoint(
            run_id=run_id,
            iteration_no=iteration_no,
            run_stage_checkpoint_id=stage.id,
            agent_decision_id=continuation_decision.id,
            action_batch_sha256=action_plan["action_batch_sha256"],
            archive_before_sha256=previous.archive_sha256,
            archive_after_sha256=update.current.archive_sha256,
            score_all_candidate_count=len(score_all_ids),
            score_all_required_metric_count=12,
            score_all_completed_evaluation_count=completed_evaluations,
            next_controller_action=update.continuation.next_action,
            replay_artifact_id=replay_artifact.id,
            metadata={
                "archive_version_ids": archive_version_ids,
                "metric_delta_count": len(delta_receipts),
                "rejected_duplicate_count": int(
                    children_receipt.get("rejected_duplicate_count", 0)
                ),
            },
        )
    return {
        "schema_version": "ampgent.autoresearch-iteration-checkpoint-receipt.1",
        "run_id": str(run_id),
        "iteration_no": iteration_no,
        "checkpoint_id": str(checkpoint.id),
        "checkpoint_receipt_sha256": checkpoint.receipt_sha256,
        "replay_sha256": replay_artifact.sha256,
        "archive_before_sha256": previous.archive_sha256,
        "archive_after_sha256": update.current.archive_sha256,
        "archive_version_ids": archive_version_ids,
        "metric_delta_count": len(delta_receipts),
        "score_all_completed_evaluation_count": len(score_all_ids) * 12,
        "durable_counts": {
            "action_count": len(action_plan["actions"]),
            "candidate_count": len(child_ids),
            "rejected_duplicate_count": int(
                children_receipt.get("rejected_duplicate_count", 0)
            ),
            "evaluation_count": len(score_all_ids) * 12,
            "metric_delta_count": len(delta_receipts),
            "archive_version_count": len(archive_version_ids),
            "checkpoint_count": 1,
            "replay_count": 1,
        },
        "continuation": update.continuation.model_dump(mode="json"),
    }


__all__ = [
    "_build_replay_bundle",
    "_compile_pepmlm_action",
    "build_typed_action_projection",
    "execute_autoresearch_action_batch",
    "finalize_autoresearch_iteration",
    "plan_autoresearch_actions",
    "persist_autoresearch_action_plan",
    "persist_autoresearch_children",
    "persist_autoresearch_score_all_bundle",
]
