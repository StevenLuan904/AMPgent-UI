from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import (
    AgentDecision,
    Candidate,
    CandidateOccurrence,
    Evaluation,
    EvidenceArtifact,
    ExperimentRun,
    LifecycleEvent,
    MultiTargetStructureEvidenceRecord,
    RunStageCheckpoint,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.provenance.hashing import sha256_json

ObserverStageName = Literal[
    "knowledge",
    "generation",
    "sequence_metrics",
    "admission",
    "refinement",
    "structure_boltz",
    "structure_rosetta",
    "final_portfolio",
    "replay",
]
DisplayCategory = Literal[
    "knowledge", "design", "evaluation", "decision", "structure", "review"
]

OBSERVER_STAGES: tuple[tuple[ObserverStageName, DisplayCategory], ...] = (
    ("knowledge", "knowledge"),
    ("generation", "design"),
    ("sequence_metrics", "evaluation"),
    ("admission", "decision"),
    ("refinement", "design"),
    ("structure_boltz", "structure"),
    ("structure_rosetta", "structure"),
    ("final_portfolio", "decision"),
    ("replay", "review"),
)

ACTIVITY_STAGE_BINDINGS: dict[str, ObserverStageName] = {
    "mark_run_started": "knowledge",
    "generate_v38_sequence_cell": "generation",
    "persist_v38_score_all_generation": "generation",
    "evaluate_v38_sequence_metric": "sequence_metrics",
    "persist_v38_sequence_metric": "sequence_metrics",
    "evaluate_v38_sequence_admission": "admission",
    "persist_v38_sequence_admission": "admission",
    "refine_v38_sequences_with_knowledge": "refinement",
    "persist_v38_refinement_children": "refinement",
    "plan_v38_multitarget_structure": "structure_boltz",
    "predict_v38_multitarget_structure": "structure_boltz",
    "persist_v38_multitarget_boltz": "structure_boltz",
    "score_v38_multitarget_rosetta": "structure_rosetta",
    "persist_v38_multitarget_rosetta": "structure_rosetta",
    "persist_v38_final_portfolio_replay": "final_portfolio",
}


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObserverStageSpec(FrozenModel):
    stage_name: ObserverStageName
    stage_order: int = Field(ge=0)
    display_category: DisplayCategory
    expected_durable_count: int = Field(ge=0)
    expected_kind: Literal["fixed", "maximum", "conditional"]
    parent_stage_names: tuple[ObserverStageName, ...] = ()


class FormalWorkflowTopology(FrozenModel):
    schema_version: Literal["v38.formal-workflow-topology.1"] = (
        "v38.formal-workflow-topology.1"
    )
    topology_version: Literal["v38.sequence-first-multitarget.1"] = (
        "v38.sequence-first-multitarget.1"
    )
    stages: tuple[ObserverStageSpec, ...]
    activity_stage_bindings: dict[str, ObserverStageName]
    task_queues: dict[str, str]
    generator_cells: int = Field(gt=0)
    raw_occurrences: int = Field(gt=0)
    required_sequence_metric_count: int = Field(gt=0)
    maximum_refinement_rounds: int = Field(ge=0)
    target_count: int = Field(gt=0)
    control_lane_count: int = Field(gt=0)
    boltz_seed_count: int = Field(gt=0)
    rosetta_decoys_per_pose: int = Field(gt=0)
    knowledge_context_pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_tree(self) -> FormalWorkflowTopology:
        if [(item.stage_name, item.display_category) for item in self.stages] != list(
            OBSERVER_STAGES
        ):
            raise ValueError("observer stage topology must be fixed and complete")
        if [item.stage_order for item in self.stages] != list(range(len(self.stages))):
            raise ValueError("observer stage order must be contiguous")
        return self


class ActivityLifecyclePayload(FrozenModel):
    schema_version: Literal["v38.activity-lifecycle.1"] = "v38.activity-lifecycle.1"
    run_id: UUID
    activity_id: str = Field(min_length=1)
    activity_type: str = Field(min_length=1)
    tool_call_id: UUID | None = None
    logical_stage: ObserverStageName
    display_category: DisplayCategory
    attempt: int = Field(ge=1)
    status: Literal["started", "progress", "succeeded", "failed", "cancelled"]
    completed: int = Field(ge=0)
    expected: int = Field(ge=0)
    worker_role: str = Field(min_length=1)
    task_queue: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_progress(self) -> ActivityLifecyclePayload:
        if self.expected and self.completed > self.expected:
            raise ValueError("activity progress exceeds expected work")
        return self


class KnowledgeCardReadPayload(FrozenModel):
    schema_version: Literal["v38.knowledge-card-read.1"] = "v38.knowledge-card-read.1"
    run_id: UUID
    card_key: str = Field(min_length=1)
    card_version: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_kind: Literal["context_pack", "passage_evidence"]
    source_uri: str | None = None
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    read_at: datetime
    status: Literal["frozen_for_run", "adopted", "rejected"]

    @model_validator(mode="after")
    def validate_source(self) -> KnowledgeCardReadPayload:
        if self.read_at.tzinfo is None:
            raise ValueError("knowledge read timestamp must be timezone-aware")
        if not self.source_uri and not self.artifact_sha256:
            raise ValueError("knowledge read requires a source URI or artifact SHA")
        return self


class ObserverTransientSnapshot(FrozenModel):
    schema_version: Literal["v38.observer-transient.1"] = "v38.observer-transient.1"
    run_id: UUID
    updated_at: datetime
    ttl_seconds: int = Field(gt=0, le=86400)
    source: str = Field(min_length=1)
    transient: dict[str, Any]

    @model_validator(mode="after")
    def validate_transient(self) -> ObserverTransientSnapshot:
        if self.updated_at.tzinfo is None:
            raise ValueError("observer snapshot timestamp must be timezone-aware")
        _reject_secret_keys(self.transient)
        return self


def _reject_secret_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(word in lowered for word in ("password", "secret", "token", "credential")):
                raise ValueError("observer snapshots cannot contain credentials")
            _reject_secret_keys(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _reject_secret_keys(item)


def build_formal_workflow_topology(request_template: dict[str, Any]) -> FormalWorkflowTopology:
    contract = request_template["execution_contract"]
    cells = contract["cells"]
    raw_occurrences = int(contract["expected_raw_occurrences"])
    required_metrics = contract["required_sequence_metrics"]
    plan = request_template["multitarget_plan_template"]
    target_count = len(plan["target_branches"])
    seeds = tuple(int(item) for item in request_template["boltz_seeds"])
    decoys = int(request_template.get("rosetta_decoys_per_pose", 16))
    structure_budget = max(
        int(item.get("structure_budget", 48)) for item in plan["target_branches"]
    )
    max_boltz = structure_budget * target_count * 2 * len(seeds)
    max_rosetta = max_boltz * decoys
    max_refinement = int(
        request_template.get("maximum_refinement_rounds")
        or request_template.get("refinement_provider", {}).get("maximum_rounds", 3)
        or 3
    )
    expected = {
        "knowledge": 1,
        "generation": raw_occurrences,
        "sequence_metrics": raw_occurrences * len(required_metrics),
        "admission": 1,
        "refinement": max_refinement,
        "structure_boltz": max_boltz,
        "structure_rosetta": max_rosetta,
        "final_portfolio": 1,
        "replay": 1,
    }
    conditional = {"refinement", "structure_boltz", "structure_rosetta"}
    stages = tuple(
        ObserverStageSpec(
            stage_name=name,
            stage_order=index,
            display_category=category,
            expected_durable_count=expected[name],
            expected_kind=(
                "conditional"
                if name in conditional
                else "maximum"
                if name == "sequence_metrics"
                else "fixed"
            ),
            parent_stage_names=(() if index == 0 else (OBSERVER_STAGES[index - 1][0],)),
        )
        for index, (name, category) in enumerate(OBSERVER_STAGES)
    )
    task_queues = {
        str(key): str(value) for key, value in request_template["task_queues"].items()
    }
    refinement_queue = request_template.get("refinement_provider", {}).get("task_queue")
    if refinement_queue:
        task_queues["refinement_provider"] = str(refinement_queue)
    return FormalWorkflowTopology(
        stages=stages,
        activity_stage_bindings=dict(ACTIVITY_STAGE_BINDINGS),
        task_queues=task_queues,
        generator_cells=len(cells),
        raw_occurrences=raw_occurrences,
        required_sequence_metric_count=len(required_metrics),
        maximum_refinement_rounds=max_refinement,
        target_count=target_count,
        control_lane_count=2,
        boltz_seed_count=len(seeds),
        rosetta_decoys_per_pose=decoys,
        knowledge_context_pack_sha256=str(request_template["knowledge_context_pack_sha256"]),
    )


def write_transient_snapshot(
    snapshot: ObserverTransientSnapshot, *, root: Path = Path("var/observer")
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{snapshot.run_id}.json"
    temporary = root / f".{snapshot.run_id}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(snapshot.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(destination)
    return destination


def display_category_for_stage(stage: ObserverStageName) -> DisplayCategory:
    return dict(OBSERVER_STAGES)[stage]


async def append_typed_lifecycle_event(
    session: AsyncSession,
    payload: ActivityLifecyclePayload | KnowledgeCardReadPayload,
) -> LifecycleEvent:
    repository = ExperimentRepository(session)
    event_type = (
        f"activity.{payload.status}"
        if isinstance(payload, ActivityLifecyclePayload)
        else "knowledge_card.read"
    )
    return await repository.append_event(
        "run",
        payload.run_id,
        event_type,
        "v38-workflow-observer-writer",
        payload.model_dump(mode="json"),
    )


def build_candidate_decision_projection(payload: dict[str, Any]) -> dict[str, Any]:
    admission = payload["admission"]
    decisions = admission["decisions"]
    selected = [
        *admission["mature_core_candidate_ids"],
        *admission["exploration_candidate_ids"],
    ]
    selected_set = {str(item) for item in selected}
    rejected_set = {str(item) for item in admission["rejected_candidate_ids"]}
    considered = [str(item["candidate_id"]) for item in decisions]
    return {
        "schema_version": "v38.candidate-decision-observer.1",
        "policy_id": "v38.sequence-maturity-policy",
        "policy_version": "1",
        "policy_sha256": sha256_json(payload["policy"]),
        "input_evidence_sha256": payload["candidate_evidence_sha256"],
        "considered_candidate_ids": considered,
        "selected_candidate_ids": [str(item) for item in selected],
        "rejected_candidate_ids": [str(item) for item in admission["rejected_candidate_ids"]],
        "deferred_candidate_ids": [
            item for item in considered if item not in selected_set | rejected_set
        ],
        "reason_codes_by_candidate": {
            str(item["candidate_id"]): list(item["reasons"]) for item in decisions
        },
    }


def _checkpoint_lock_id(run_id: UUID) -> int:
    digest = bytes.fromhex(sha256_json({"domain": "observer-checkpoint", "run_id": str(run_id)}))
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def persist_observer_checkpoints(
    session: AsyncSession,
    *,
    run_id: UUID,
    topology: FormalWorkflowTopology,
    observed_at: datetime | None = None,
) -> tuple[RunStageCheckpoint, ...]:
    run = await session.get(ExperimentRun, run_id)
    if run is None:
        raise ValueError("observer checkpoint run does not exist")
    if run.spec_json.get("workflow_topology_schema_version") != topology.schema_version:
        return ()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _checkpoint_lock_id(run_id)},
    )
    candidate_count = int(
        await session.scalar(
            select(func.count(Candidate.id)).where(Candidate.run_id == run_id)
        )
        or 0
    )
    occurrence_count = int(
        await session.scalar(
            select(func.count(CandidateOccurrence.id)).where(
                CandidateOccurrence.run_id == run_id,
                CandidateOccurrence.occurrence_kind == "de_novo",
            )
        )
        or 0
    )
    evaluation_count = int(
        await session.scalar(
            select(func.count(Evaluation.id))
            .join(Candidate, Evaluation.candidate_id == Candidate.id)
            .where(Candidate.run_id == run_id)
        )
        or 0
    )
    admission_rows = list(
        await session.scalars(
            select(AgentDecision)
            .where(
                AgentDecision.run_id == run_id,
                AgentDecision.decision_type == "v38_sequence_maturity_admission",
            )
            .order_by(AgentDecision.generation.desc())
        )
    )
    refinement_count = int(
        await session.scalar(
            select(func.count(ToolCall.id)).where(
                ToolCall.run_id == run_id,
                ToolCall.tool_name == "v38-knowledge-traced-refinement",
            )
        )
        or 0
    )
    structure_counts = dict(
        (
            await session.execute(
                select(
                    MultiTargetStructureEvidenceRecord.evidence_kind,
                    func.count(MultiTargetStructureEvidenceRecord.id),
                )
                .where(MultiTargetStructureEvidenceRecord.run_id == run_id)
                .group_by(MultiTargetStructureEvidenceRecord.evidence_kind)
            )
        ).all()
    )
    final_count = int(
        await session.scalar(
            select(func.count(AgentDecision.id)).where(
                AgentDecision.run_id == run_id,
                AgentDecision.decision_type == "v38_final_multiview_portfolio",
            )
        )
        or 0
    )
    replay_count = int(
        await session.scalar(
            select(func.count(EvidenceArtifact.artifact_id))
            .join(ToolCall, EvidenceArtifact.tool_call_id == ToolCall.id)
            .where(
                ToolCall.run_id == run_id,
                EvidenceArtifact.role == "v38_final_portfolio_and_replay",
            )
        )
        or 0
    )
    knowledge_count = int(
        await session.scalar(
            select(func.count(LifecycleEvent.id)).where(
                LifecycleEvent.aggregate_type == "run",
                LifecycleEvent.aggregate_id == run_id,
                LifecycleEvent.event_type == "knowledge_card.read",
            )
        )
        or 0
    )
    counts = {
        "knowledge": knowledge_count,
        "generation": occurrence_count,
        "sequence_metrics": evaluation_count,
        "admission": len(admission_rows),
        "refinement": refinement_count,
        "structure_boltz": int(structure_counts.get("boltz_pose", 0)),
        "structure_rosetta": int(structure_counts.get("rosetta_decoy", 0)),
        "final_portfolio": final_count,
        "replay": replay_count,
    }
    expected = {item.stage_name: item.expected_durable_count for item in topology.stages}
    if occurrence_count >= topology.raw_occurrences:
        expected["sequence_metrics"] = candidate_count * topology.required_sequence_metric_count
    if admission_rows:
        admission = admission_rows[0].structured_json["admission"]
        if not admission["refinement_required"]:
            expected["refinement"] = refinement_count
        selected = len(admission["mature_core_candidate_ids"]) + len(
            admission["exploration_candidate_ids"]
        )
        if admission["structure_dispatch_allowed"]:
            expected["structure_boltz"] = (
                selected
                * topology.target_count
                * topology.control_lane_count
                * topology.boltz_seed_count
            )
            expected["structure_rosetta"] = (
                expected["structure_boltz"] * topology.rosetta_decoys_per_pose
            )
        elif not admission["refinement_required"]:
            for stage in (
                "structure_boltz",
                "structure_rosetta",
                "final_portfolio",
                "replay",
            ):
                expected[stage] = 0
    active_index = next(
        (
            index
            for index, item in enumerate(topology.stages)
            if expected[item.stage_name] > 0 and counts[item.stage_name] < expected[item.stage_name]
        ),
        len(topology.stages),
    )
    now = observed_at or datetime.now(UTC)
    created: list[RunStageCheckpoint] = []
    for index, spec in enumerate(topology.stages):
        durable = counts[spec.stage_name]
        target = expected[spec.stage_name]
        if target == 0:
            status, action = "skipped", "advance_stage"
            reasons, tasks = ["conditional_stage_not_required"], []
        elif durable >= target:
            status, action = "completed", "advance_stage"
            reasons, tasks = ["durable_target_reached"], []
        elif run.status in {"failed", "cancelled"}:
            status, action = "failed", "preserve_terminal_evidence"
            reasons, tasks = ["run_terminal_before_stage_completion"], ["do_not_backfill"]
        elif index == active_index:
            status, action = "active", "continue_stage"
            reasons, tasks = ["durable_target_incomplete"], [f"continue_{spec.stage_name}"]
        else:
            status, action = "pending", "await_parent_stage"
            reasons, tasks = ["parent_stage_incomplete"], []
        latest = await session.scalar(
            select(RunStageCheckpoint)
            .where(
                RunStageCheckpoint.run_id == run_id,
                RunStageCheckpoint.stage_name == spec.stage_name,
            )
            .order_by(RunStageCheckpoint.observation_no.desc())
            .limit(1)
        )
        signature = {
            "durable_count": durable,
            "expected_durable_count": target,
            "stage_status": status,
            "controller_action": action,
            "reasons": reasons,
            "tasks": tasks,
        }
        if latest is not None and signature == {
            "durable_count": latest.durable_count,
            "expected_durable_count": latest.expected_durable_count,
            "stage_status": latest.stage_status,
            "controller_action": latest.controller_action,
            "reasons": latest.reasons_json,
            "tasks": latest.tasks_json,
        }:
            continue
        receipt = {
            "schema_version": "v38.observer-stage-checkpoint.1",
            "run_id": str(run_id),
            "stage_name": spec.stage_name,
            "stage_order": spec.stage_order,
            "observation_no": (latest.observation_no + 1 if latest else 1),
            **signature,
            "observed_at": now.isoformat(),
        }
        row = RunStageCheckpoint(
            run_id=run_id,
            stage_name=spec.stage_name,
            stage_order=spec.stage_order,
            observation_no=receipt["observation_no"],
            durable_count=durable,
            expected_durable_count=target,
            stage_status=status,
            controller_action=action,
            reasons_json=reasons,
            tasks_json=tasks,
            receipt_sha256=sha256_json(receipt),
            observed_at=now,
        )
        session.add(row)
        created.append(row)
    await session.flush()
    return tuple(created)
