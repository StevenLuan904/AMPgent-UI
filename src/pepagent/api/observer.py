from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import and_, distinct, exists, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from pepagent.db.models import (
    AgentDecision,
    Artifact,
    Candidate,
    CandidateOccurrence,
    Evaluation,
    EvidenceArtifact,
    ExperimentRun,
    ExperimentRunTargetBranch,
    LifecycleEvent,
    MultiTargetStructureEvidenceRecord,
    RunStageCheckpoint,
    Target,
    ToolCall,
)
from pepagent.db.session import get_session
from pepagent.storage.object_store import ContentAddressedObjectStore

router = APIRouter(prefix="/v1/observer", tags=["observer"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]

NODE_TOOL_RULES: dict[str, tuple[str, ...]] = {
    "amp_designer": ("amp_designer",),
    "ampgan": ("ampgan",),
    "hydramp": ("hydramp",),
    "mic": ("mic_potency",),
    "amp_read": ("amp_read",),
    "hemolysis": ("hemolysis",),
    "toxicity": ("toxicity",),
    "developability": ("developability",),
    "admission": ("maturity-admission",),
    "boltz": ("boltz",),
    "rosetta": ("pyrosetta", "rosetta"),
    "portfolio": ("portfolio", "review"),
}

HISTORICAL_EXACT_REPLAY = "historical_exact_replay"


def _historical_exact_replay_exists(candidate: Any = Candidate) -> Any:
    """Return the authoritative, read-only cross-run replay predicate."""

    prior = aliased(Candidate)
    return and_(
        candidate.generation > 0,
        exists(
            select(1).where(
                prior.run_id != candidate.run_id,
                prior.sequence_sha256 == candidate.sequence_sha256,
                or_(
                    prior.created_at < candidate.created_at,
                    and_(prior.created_at == candidate.created_at, prior.id < candidate.id),
                ),
            )
        )
    )


def _display_eligible(candidate: Any = Candidate) -> Any:
    return not_(_historical_exact_replay_exists(candidate))


def _display_population(
    candidate_record_count: int,
    excluded_candidate_count: int,
) -> dict[str, Any]:
    """Describe the single candidate population used by every display aggregate."""

    return {
        "candidate_count": candidate_record_count - excluded_candidate_count,
        "candidate_record_count": candidate_record_count,
        "excluded_candidate_count": excluded_candidate_count,
        "exclusion_reason": HISTORICAL_EXACT_REPLAY,
    }


async def _excluded_candidate_ids(
    session: AsyncSession,
    run_id: uuid.UUID,
) -> set[uuid.UUID]:
    return set(
        await session.scalars(
            select(Candidate.id).where(
                Candidate.run_id == run_id,
                _historical_exact_replay_exists(Candidate),
            )
        )
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _run_kind(run: ExperimentRun) -> str:
    return str((run.spec_json or {}).get("run_kind") or "standard")


def _display_name(run: ExperimentRun) -> str:
    spec = run.spec_json or {}
    schema = str(spec.get("schema_version") or "")
    if schema.startswith("v38") or _run_kind(run).startswith("multitarget_sequence_first"):
        return "Sequence-first · multitarget"
    return str(spec.get("name") or spec.get("objective") or _run_kind(run)).replace("_", " ")


def _run_identity_payload(run: ExperimentRun) -> dict[str, uuid.UUID | str | None]:
    """Return one run's durable PostgreSQL and Temporal identity without joins."""
    return {
        "id": run.id,
        # Kept for clients written against the original Observer contract.
        "workflow_id": run.temporal_workflow_id,
        "temporal_workflow_id": run.temporal_workflow_id,
        "temporal_run_id": run.temporal_run_id,
    }


def _admission_payload(decisions: list[AgentDecision]) -> dict[str, Any]:
    for decision in reversed(decisions):
        structured = decision.structured_json or {}
        admission = structured.get("admission")
        if isinstance(admission, dict):
            return admission
    return {}


def _stage_status(progress: int, total: int, run_status: str) -> str:
    if total > 0 and progress >= total:
        return "completed"
    if progress > 0 and run_status in {"running", "created", "submitted"}:
        return "running"
    if progress > 0:
        return "stopped"
    if run_status in {"failed", "cancelled"}:
        return "stopped"
    return "pending"


def _call_matches_node(node_id: str, tool_name: str) -> bool:
    normalized = tool_name.lower()
    if node_id == "mic" and "amp_read" in normalized:
        return False
    return any(fragment in normalized for fragment in NODE_TOOL_RULES.get(node_id, ()))


def _compact_value(value: Any, depth: int = 0) -> Any:
    if depth >= 2:
        return "…"
    if isinstance(value, str):
        if len(value) <= 180:
            return value
        alphabet = set(value.upper())
        if alphabet <= set("ACDEFGHIKLMNPQRSTVWYBXZJUO-"):
            return {
                "type": "sequence",
                "length": len(value),
                "preview": f"{value[:18]}…{value[-8:]}",
            }
        return f"{value[:160]}…"
    if isinstance(value, dict):
        return {str(key): _compact_value(item, depth + 1) for key, item in list(value.items())[:14]}
    if isinstance(value, list):
        return [_compact_value(item, depth + 1) for item in value[:12]]
    return value


@router.get("/runs")
async def list_observer_runs(
    session: SessionDep,
    limit: int = Query(default=24, ge=1, le=100),
    include_control: bool = False,
) -> dict[str, Any]:
    fetch_limit = min(200, limit * 4 if not include_control else limit)
    rows = list(
        await session.scalars(
            select(ExperimentRun).order_by(ExperimentRun.created_at.desc()).limit(fetch_limit)
        )
    )
    if not include_control:
        rows = [row for row in rows if "control" not in _run_kind(row)]
    rows = rows[:limit]
    run_ids = [row.id for row in rows]

    counts: dict[str, dict[uuid.UUID, int]] = {}
    for label, model in (("tool_calls", ToolCall),):
        if not run_ids:
            counts[label] = {}
            continue
        grouped = await session.execute(
            select(model.run_id, func.count())
            .where(model.run_id.in_(run_ids))
            .group_by(model.run_id)
        )
        counts[label] = {run_id: int(count) for run_id, count in grouped}

    if run_ids:
        structure_groups = await session.execute(
            select(MultiTargetStructureEvidenceRecord.run_id, func.count())
            .join(Candidate, Candidate.id == MultiTargetStructureEvidenceRecord.candidate_id)
            .where(
                MultiTargetStructureEvidenceRecord.run_id.in_(run_ids),
                _display_eligible(Candidate),
            )
            .group_by(MultiTargetStructureEvidenceRecord.run_id)
        )
        counts["structure_records"] = {
            run_id: int(count) for run_id, count in structure_groups
        }
    else:
        counts["structure_records"] = {}

    if run_ids:
        candidate_groups = await session.execute(
            select(
                Candidate.run_id,
                func.count().filter(_display_eligible(Candidate)),
                func.count(),
                func.count().filter(_historical_exact_replay_exists(Candidate)),
            )
            .where(Candidate.run_id.in_(run_ids))
            .group_by(Candidate.run_id)
        )
        candidate_counts = {
            run_id: (int(display_count), int(record_count), int(excluded_count))
            for run_id, display_count, record_count, excluded_count in candidate_groups
        }
    else:
        candidate_counts = {}

    return {
        "source": "postgresql",
        "read_only": True,
        "runs": [
            {
                **_run_identity_payload(row),
                "name": _display_name(row),
                "kind": _run_kind(row),
                "schema_version": (row.spec_json or {}).get("schema_version"),
                "status": row.status,
                "created_at": _iso(row.created_at),
                "started_at": _iso(row.started_at),
                "finished_at": _iso(row.finished_at),
                "candidate_count": candidate_counts.get(row.id, (0, 0, 0))[0],
                "candidate_record_count": candidate_counts.get(row.id, (0, 0, 0))[1],
                "excluded_candidate_count": candidate_counts.get(row.id, (0, 0, 0))[2],
                "tool_call_count": counts["tool_calls"].get(row.id, 0),
                "structure_record_count": counts["structure_records"].get(row.id, 0),
            }
            for row in rows
        ],
    }


@router.get("/runs/{run_id}")
async def get_observer_run(run_id: uuid.UUID, session: SessionDep) -> dict[str, Any]:
    run = await session.get(ExperimentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    candidate_record_count = int(
        (
            await session.scalar(
                select(func.count()).select_from(Candidate).where(Candidate.run_id == run_id)
            )
        )
        or 0
    )
    excluded_candidate_ids = await _excluded_candidate_ids(session, run_id)
    excluded_candidate_count = len(excluded_candidate_ids)
    display_population = _display_population(
        candidate_record_count,
        excluded_candidate_count,
    )
    candidate_count = display_population["candidate_count"]
    occurrence_count = int(
        (
            await session.scalar(
                select(func.count())
                .select_from(CandidateOccurrence)
                .where(CandidateOccurrence.run_id == run_id)
            )
        )
        or 0
    )
    evaluation_count, metric_count = (
        await session.execute(
            select(func.count(Evaluation.id), func.count(distinct(Evaluation.metric_name)))
            .join(Candidate, Evaluation.candidate_id == Candidate.id)
            .where(Candidate.run_id == run_id, _display_eligible(Candidate))
        )
    ).one()
    evaluation_count = int(evaluation_count or 0)
    metric_count = int(metric_count or 0)

    metric_rows = list(
        await session.execute(
            select(
                Evaluation.metric_name,
                func.avg(Evaluation.numeric_value),
                func.min(Evaluation.numeric_value),
                func.max(Evaluation.numeric_value),
                func.count(Evaluation.id),
            )
            .join(Candidate, Evaluation.candidate_id == Candidate.id)
            .where(Candidate.run_id == run_id, _display_eligible(Candidate))
            .group_by(Evaluation.metric_name)
        )
    )
    metric_stats = {
        metric_name: {
            "mean": float(mean_value) if mean_value is not None else None,
            "min": float(min_value) if min_value is not None else None,
            "max": float(max_value) if max_value is not None else None,
            "count": int(row_count),
        }
        for metric_name, mean_value, min_value, max_value, row_count in metric_rows
    }
    metric_text_rows = list(
        await session.execute(
            select(Evaluation.metric_name, Evaluation.text_value, func.count())
            .join(Candidate, Evaluation.candidate_id == Candidate.id)
            .where(
                Candidate.run_id == run_id,
                _display_eligible(Candidate),
                Evaluation.text_value.is_not(None),
            )
            .group_by(Evaluation.metric_name, Evaluation.text_value)
        )
    )
    metric_text_counts: dict[str, dict[str, int]] = defaultdict(dict)
    for metric_name, text_value, row_count in metric_text_rows:
        if text_value:
            metric_text_counts[metric_name][text_value] = int(row_count)

    tool_groups = list(
        await session.execute(
            select(ToolCall.tool_name, ToolCall.status, func.count())
            .where(ToolCall.run_id == run_id)
            .group_by(ToolCall.tool_name, ToolCall.status)
            .order_by(ToolCall.tool_name, ToolCall.status)
        )
    )
    tool_summary: dict[str, dict[str, int]] = defaultdict(dict)
    for name, call_status, count in tool_groups:
        tool_summary[name][call_status] = int(count)

    occurrence_groups = list(
        await session.execute(
            select(ToolCall.tool_name, func.count(CandidateOccurrence.id))
            .join(CandidateOccurrence, CandidateOccurrence.tool_call_id == ToolCall.id)
            .where(ToolCall.run_id == run_id)
            .group_by(ToolCall.tool_name)
        )
    )
    occurrence_by_tool = {name: int(count) for name, count in occurrence_groups}

    def tool_progress(fragment: str) -> tuple[int, int, str]:
        matching = {
            name: statuses for name, statuses in tool_summary.items() if fragment in name.lower()
        }
        total = sum(sum(statuses.values()) for statuses in matching.values())
        completed = sum(
            sum(count for status_name, count in statuses.items() if status_name == "succeeded")
            for statuses in matching.values()
        )
        if completed and completed == total:
            return completed, total, "completed"
        return completed, total, _stage_status(completed, total or 1, run.status)

    decisions = list(
        await session.scalars(
            select(AgentDecision)
            .where(AgentDecision.run_id == run_id)
            .order_by(AgentDecision.created_at)
        )
    )
    admission = _admission_payload(decisions)
    mature_ids = [
        candidate_id
        for value in admission.get("mature_core_candidate_ids", [])
        if (candidate_id := uuid.UUID(value)) not in excluded_candidate_ids
    ]
    exploration_ids = [
        candidate_id
        for value in admission.get("exploration_candidate_ids", [])
        if (candidate_id := uuid.UUID(value)) not in excluded_candidate_ids
    ]
    admitted_ids = mature_ids + exploration_ids

    branch_rows = list(
        await session.execute(
            select(ExperimentRunTargetBranch, Target)
            .join(Target, ExperimentRunTargetBranch.target_id == Target.id)
            .where(ExperimentRunTargetBranch.run_id == run_id)
            .order_by(ExperimentRunTargetBranch.branch_order)
        )
    )
    branches = [
        {
            "order": branch.branch_order,
            "key": branch.branch_key,
            "role": branch.panel_role,
            "status": branch.status,
            "target_id": target.id,
            "target_name": target.name,
            "organism": target.organism,
            "accession": target.accession,
            "sequence": target.sequence,
            "sequence_length": len(target.sequence),
            "evidence_namespace": branch.evidence_namespace,
            "coordinate_sha256": branch.coordinate_sha256,
        }
        for branch, target in branch_rows
    ]

    structure_groups = list(
        await session.execute(
            select(
                MultiTargetStructureEvidenceRecord.evidence_kind,
                MultiTargetStructureEvidenceRecord.control_lane,
                func.count(),
            )
            .join(Candidate, Candidate.id == MultiTargetStructureEvidenceRecord.candidate_id)
            .where(
                MultiTargetStructureEvidenceRecord.run_id == run_id,
                _display_eligible(Candidate),
            )
            .group_by(
                MultiTargetStructureEvidenceRecord.evidence_kind,
                MultiTargetStructureEvidenceRecord.control_lane,
            )
        )
    )
    structure_counts: dict[str, dict[str, int]] = defaultdict(dict)
    for kind, lane, count in structure_groups:
        structure_counts[kind][lane] = int(count)
    boltz_count = sum(structure_counts.get("boltz_pose", {}).values())
    rosetta_count = sum(structure_counts.get("rosetta_decoy", {}).values())

    expected_structure_tasks = len(admitted_ids) * len(branches) * 2 * 3
    expected_rosetta_decoys = expected_structure_tasks * 16
    checkpoints = list(
        await session.scalars(
            select(RunStageCheckpoint)
            .where(RunStageCheckpoint.run_id == run_id)
            .order_by(RunStageCheckpoint.stage_order, RunStageCheckpoint.observed_at.desc())
        )
    )
    latest_checkpoint: dict[str, RunStageCheckpoint] = {}
    for checkpoint in checkpoints:
        latest_checkpoint.setdefault(checkpoint.stage_name, checkpoint)

    events = list(
        await session.scalars(
            select(LifecycleEvent)
            .where(
                LifecycleEvent.aggregate_type == "run",
                LifecycleEvent.aggregate_id == run_id,
            )
            .order_by(LifecycleEvent.sequence_no.desc())
            .limit(32)
        )
    )

    candidate_payloads: list[dict[str, Any]] = []
    preview_ids = admitted_ids[:12]
    if not preview_ids:
        preview_ids = list(
            await session.scalars(
                select(Candidate.id)
                .where(Candidate.run_id == run_id, _display_eligible(Candidate))
                .order_by(Candidate.proposal_rank.nullslast(), Candidate.created_at)
                .limit(12)
            )
        )
    candidates = (
        list(await session.scalars(select(Candidate).where(Candidate.id.in_(preview_ids))))
        if preview_ids
        else []
    )
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    evaluation_rows = (
        list(
            await session.scalars(
                select(Evaluation)
                .where(Evaluation.candidate_id.in_(preview_ids))
                .order_by(Evaluation.metric_name)
            )
        )
        if preview_ids
        else []
    )
    evaluations_by_candidate: dict[uuid.UUID, list[Evaluation]] = defaultdict(list)
    for evaluation in evaluation_rows:
        evaluations_by_candidate[evaluation.candidate_id].append(evaluation)

    decision_by_id = {
        str(item.get("candidate_id")): item
        for item in admission.get("decisions", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }
    for candidate_id in preview_ids:
        candidate = candidates_by_id.get(candidate_id)
        if candidate is None:
            continue
        candidate_decision = decision_by_id.get(str(candidate_id), {})
        candidate_payloads.append(
            {
                "id": candidate.id,
                "sequence": candidate.sequence,
                "length": len(candidate.sequence),
                "proposal_rank": candidate.proposal_rank,
                "cohort": "mature_core" if candidate.id in mature_ids else "exploration",
                "pareto_front": candidate_decision.get("pareto_front"),
                "reasons": candidate_decision.get("reasons", []),
                "display_eligible": True,
                "exclusion_reason": None,
                "metrics": [
                    {
                        "name": metric.metric_name,
                        "value": metric.numeric_value,
                        "text": metric.text_value,
                        "unit": metric.unit,
                        "status": metric.status,
                        "out_of_domain": metric.out_of_domain,
                    }
                    for metric in evaluations_by_candidate[candidate.id]
                ],
            }
        )

    viewers: dict[str, dict[str, Any] | None] = {"boltz": None, "rosetta": None}
    for stage_id, evidence_kind in (("boltz", "boltz_pose"), ("rosetta", "rosetta_decoy")):
        structure_artifact = await session.execute(
            select(MultiTargetStructureEvidenceRecord, Artifact, Candidate, Target)
            .join(
                Artifact,
                Artifact.sha256 == MultiTargetStructureEvidenceRecord.output_artifact_sha256,
            )
            .join(Candidate, Candidate.id == MultiTargetStructureEvidenceRecord.candidate_id)
            .join(Target, Target.id == MultiTargetStructureEvidenceRecord.target_id)
            .where(
                MultiTargetStructureEvidenceRecord.run_id == run_id,
                MultiTargetStructureEvidenceRecord.evidence_kind == evidence_kind,
                _display_eligible(Candidate),
            )
            .order_by(MultiTargetStructureEvidenceRecord.created_at.desc())
            .limit(1)
        )
        structure_row = structure_artifact.first()
        if structure_row is None:
            continue
        record, artifact, candidate, target = structure_row
        viewers[stage_id] = {
            "candidate_id": candidate.id,
            "sequence": candidate.sequence,
            "target_id": target.id,
            "target_name": target.name,
            "lane": record.control_lane,
            "seed": record.boltz_seed,
            "artifact_sha256": artifact.sha256,
            "media_type": artifact.media_type,
            "artifact_url": f"/v1/observer/artifacts/{artifact.sha256}",
        }
    viewer = viewers["boltz"]

    amp_designer = tool_progress("amp_designer")
    ampgan = tool_progress("ampgan")
    hydramp = tool_progress("hydramp")
    mic = tool_progress("mic_potency")
    amp_read = tool_progress("amp_read")
    hemolysis = tool_progress("hemolysis")
    toxicity = tool_progress("toxicity")
    developability = tool_progress("developability")
    graph_nodes = [
        {
            "id": "target_data",
            "label": "靶点数据集",
            "kind": "data",
            "group": "inputs",
            "status": "completed" if branches else "pending",
            "current": len(branches),
            "total": 2,
            "provenance": "database",
        },
        {
            "id": "knowledge",
            "label": "知识卡片",
            "kind": "data",
            "group": "inputs",
            "status": "pending",
            "current": 0,
            "total": 0,
            "provenance": "missing",
        },
        {
            "id": "amp_designer",
            "label": "AMP Designer",
            "kind": "model",
            "group": "design",
            "status": amp_designer[2],
            "current": amp_designer[0],
            "total": amp_designer[1],
            "provenance": "database",
        },
        {
            "id": "ampgan",
            "label": "AMPGAN v2",
            "kind": "model",
            "group": "design",
            "status": ampgan[2],
            "current": ampgan[0],
            "total": ampgan[1],
            "provenance": "database",
        },
        {
            "id": "hydramp",
            "label": "HydrAMP",
            "kind": "model",
            "group": "design",
            "status": hydramp[2],
            "current": hydramp[0],
            "total": hydramp[1],
            "provenance": "database",
        },
        {
            "id": "candidate_pool",
            "label": "候选池",
            "kind": "data",
            "group": "design",
            "status": "completed" if occurrence_count else "pending",
            "current": candidate_count,
            "total": candidate_count,
            "provenance": "database",
        },
        {
            "id": "mic",
            "label": "最小抑菌浓度预测",
            "kind": "model",
            "group": "evaluation",
            "status": mic[2],
            "current": mic[0],
            "total": mic[1],
            "provenance": "database",
        },
        {
            "id": "amp_read",
            "label": "AMP read",
            "kind": "model",
            "group": "evaluation",
            "status": amp_read[2],
            "current": amp_read[0],
            "total": amp_read[1],
            "provenance": "database",
        },
        {
            "id": "hemolysis",
            "label": "溶血风险",
            "kind": "model",
            "group": "evaluation",
            "status": hemolysis[2],
            "current": hemolysis[0],
            "total": hemolysis[1],
            "provenance": "database",
        },
        {
            "id": "toxicity",
            "label": "毒性风险",
            "kind": "model",
            "group": "evaluation",
            "status": toxicity[2],
            "current": toxicity[0],
            "total": toxicity[1],
            "provenance": "database",
        },
        {
            "id": "developability",
            "label": "可开发性",
            "kind": "model",
            "group": "evaluation",
            "status": developability[2],
            "current": developability[0],
            "total": developability[1],
            "provenance": "database",
        },
        {
            "id": "admission",
            "label": "候选决策",
            "kind": "decision",
            "group": "decision",
            "status": "completed" if admission else _stage_status(0, 1, run.status),
            "current": len(admitted_ids),
            "total": candidate_count,
            "provenance": "database",
        },
        {
            "id": "targets",
            "label": "靶点分支",
            "kind": "data",
            "group": "structure",
            "status": "completed" if branches else _stage_status(0, 2, run.status),
            "current": len(branches),
            "total": 2,
            "provenance": "database",
        },
        {
            "id": "boltz",
            "label": "Boltz 2复合物",
            "kind": "structure",
            "group": "structure",
            "status": _stage_status(boltz_count, expected_structure_tasks, run.status),
            "current": boltz_count,
            "total": expected_structure_tasks,
            "provenance": "derived",
        },
        {
            "id": "rosetta",
            "label": "Rosetta界面能",
            "kind": "structure",
            "group": "structure",
            "status": _stage_status(rosetta_count, expected_rosetta_decoys, run.status),
            "current": rosetta_count,
            "total": expected_rosetta_decoys,
            "provenance": "derived",
        },
        {
            "id": "portfolio",
            "label": "科学评审",
            "kind": "review",
            "group": "review",
            "status": "completed"
            if any("portfolio" in d.decision_type for d in decisions)
            else _stage_status(0, 1, run.status),
            "current": sum(1 for d in decisions if "portfolio" in d.decision_type),
            "total": 1,
            "provenance": "database",
        },
    ]

    def occurrence_total(fragment: str) -> int:
        return sum(
            count
            for tool_name, count in occurrence_by_tool.items()
            if fragment in tool_name.lower()
        )

    def metric_mean(metric_name: str) -> float | None:
        value = metric_stats.get(metric_name, {}).get("mean")
        return float(value) if value is not None else None

    def mic_value(metric_name: str) -> float | None:
        value = metric_mean(metric_name)
        return 10**value if value is not None else None

    def rate(part: int, whole: int) -> str:
        return f"{part / whole * 100:.1f}%" if whole else "—"

    def insight(
        grade: str,
        verdict: str,
        reason: str,
        *facts: tuple[str, str],
        source: str = "observer_summary",
    ) -> dict[str, Any]:
        return {
            "grade": grade,
            "verdict": verdict,
            "reason": reason,
            "facts": [{"label": label, "value": value} for label, value in facts],
            "source": source,
        }

    mic_mean = mic_value("llamp_log10_mic_um")
    amp_read_mean = mic_value("amp_read_log10_mic_um")
    hemolysis_high = metric_text_counts.get("macrel_hemolysis_label", {}).get("high", 0)
    hemolysis_low = metric_text_counts.get("macrel_hemolysis_label", {}).get("low", 0)
    toxin_count = metric_text_counts.get("toxinpred3_label", {}).get("Toxin", 0)
    non_toxin_count = metric_text_counts.get("toxinpred3_label", {}).get("Non-Toxin", 0)
    net_charge = metric_mean("net_charge_ph7_4")
    hydrophobic_ratio = metric_mean("hydrophobic_ratio_modlamp")
    duplicate_count = max(0, occurrence_count - candidate_record_count)
    node_insights = {
        "target_data": insight(
            "good" if branches else "neutral",
            "输入已冻结" if branches else "输入缺失",
            f"{len(branches)}个靶点已冻结",
            ("靶点", str(len(branches))),
            ("状态", "身份与坐标已固定"),
        ),
        "knowledge": insight(
            "bad",
            "证据缺口",
            "缺少知识卡记录",
            ("已读取", "0 张"),
            ("需要", "版本与内容记录"),
        ),
        "amp_designer": insight(
            "good" if amp_designer[2] == "completed" else "fair",
            "生成完成" if amp_designer[2] == "completed" else "生成未完成",
            f"生成{occurrence_total('amp_designer')}条提案",
            ("提案", str(occurrence_total("amp_designer"))),
            ("运行", f"{amp_designer[0]}/{amp_designer[1]}"),
        ),
        "ampgan": insight(
            "good" if ampgan[2] == "completed" else "fair",
            "生成完成" if ampgan[2] == "completed" else "生成未完成",
            f"生成{occurrence_total('ampgan')}条提案",
            ("提案", str(occurrence_total("ampgan"))),
            ("运行", f"{ampgan[0]}/{ampgan[1]}"),
        ),
        "hydramp": insight(
            "good" if hydramp[2] == "completed" else "fair",
            "生成完成" if hydramp[2] == "completed" else "生成未完成",
            f"生成{occurrence_total('hydramp')}条提案",
            ("提案", str(occurrence_total("hydramp"))),
            ("运行", f"{hydramp[0]}/{hydramp[1]}"),
        ),
        "candidate_pool": insight(
            "good",
            "去重完成",
            f"可展示集合保留{candidate_count}条",
            ("可展示序列", str(candidate_count)),
            ("重复提案", str(duplicate_count)),
            ("历史精确重放排除", str(excluded_candidate_count)),
        ),
        "mic": insight(
            "okay",
            "活性预测",
            f"均值{mic_mean:.1f}微摩尔" if mic_mean is not None else "暂无数值结果",
            ("预测最小抑菌浓度", f"{mic_mean:.1f} 微摩尔" if mic_mean is not None else "—"),
            (
                "覆盖",
                f"{metric_stats.get('llamp_log10_mic_um', {}).get('count', 0)}/{candidate_count}",
            ),
        ),
        "amp_read": insight(
            "okay",
            "交叉预测",
            f"均值{amp_read_mean:.1f}微摩尔" if amp_read_mean is not None else "暂无数值结果",
            (
                "预测最小抑菌浓度",
                f"{amp_read_mean:.1f} 微摩尔" if amp_read_mean is not None else "—",
            ),
            (
                "覆盖",
                f"{metric_stats.get('amp_read_log10_mic_um', {}).get('count', 0)}"
                f"/{candidate_count}",
            ),
        ),
        "hemolysis": insight(
            "bad",
            "高风险富集",
            f"高风险占{rate(hemolysis_high, candidate_count)}",
            ("高风险", f"{hemolysis_high} · {rate(hemolysis_high, candidate_count)}"),
            ("低风险", str(hemolysis_low)),
        ),
        "toxicity": insight(
            "fair",
            "毒性警报",
            f"预测有毒占{rate(toxin_count, candidate_count)}",
            ("预测有毒", f"{toxin_count} · {rate(toxin_count, candidate_count)}"),
            ("预测无毒", str(non_toxin_count)),
        ),
        "developability": insight(
            "okay",
            "理化概况",
            f"平均净电荷{net_charge:+.2f}" if net_charge is not None else "暂无数值结果",
            ("酸碱度7.4下净电荷", f"{net_charge:+.2f}" if net_charge is not None else "—"),
            ("疏水比例", f"{hydrophobic_ratio:.2f}" if hydrophobic_ratio is not None else "—"),
        ),
        "admission": insight(
            "fair",
            "小比例入选",
            f"入选率{rate(len(admitted_ids), candidate_count)}",
            ("成熟核心", str(len(mature_ids))),
            ("探索组", str(len(exploration_ids))),
            source="persisted_decision",
        ),
        "targets": insight(
            "good" if branches else "neutral",
            "靶点分派" if branches else "尚未分派",
            f"分派到{len(branches)}个靶点",
            ("原位通道", str(len(branches))),
            ("对照通道", str(len(branches))),
        ),
        "boltz": insight(
            "neutral"
            if expected_structure_tasks == 0
            else "bad"
            if boltz_count < expected_structure_tasks
            else "good",
            "未开始"
            if expected_structure_tasks == 0
            else "采样覆盖不足"
            if boltz_count < expected_structure_tasks
            else "采样完成",
            "尚未进入结构阶段"
            if expected_structure_tasks == 0
            else f"仅{boltz_count}/{expected_structure_tasks}个构象",
            ("完成度", rate(boltz_count, expected_structure_tasks)),
            (
                "原位 / 对照",
                f"{structure_counts.get('boltz_pose', {}).get('native', 0)} / "
                f"{structure_counts.get('boltz_pose', {}).get('wrong_pocket', 0)}",
            ),
        ),
        "rosetta": insight(
            "neutral"
            if expected_rosetta_decoys == 0
            else "bad"
            if rosetta_count < expected_rosetta_decoys
            else "good",
            "未开始"
            if expected_rosetta_decoys == 0
            else "精修覆盖不足"
            if rosetta_count < expected_rosetta_decoys
            else "精修完成",
            "尚未生成界面能"
            if expected_rosetta_decoys == 0
            else f"仅{rosetta_count}/{expected_rosetta_decoys}个样本",
            ("完成度", rate(rosetta_count, expected_rosetta_decoys)),
            (
                "原位 / 对照",
                f"{structure_counts.get('rosetta_decoy', {}).get('native', 0)} / "
                f"{structure_counts.get('rosetta_decoy', {}).get('wrong_pocket', 0)}",
            ),
        ),
        "portfolio": insight(
            "bad",
            "结论未形成",
            "尚无最终候选组合",
            ("组合决策", "0"),
            ("前置证据", "未完成"),
        ),
    }
    for node in graph_nodes:
        node["insight"] = node_insights[node["id"]]
    graph_edges = [
        {"source": "target_data", "target": "knowledge"},
        *(
            {"source": "knowledge", "target": target}
            for target in ("amp_designer", "ampgan", "hydramp")
        ),
        *(
            {"source": source, "target": "candidate_pool"}
            for source in ("amp_designer", "ampgan", "hydramp")
        ),
        *(
            {"source": "candidate_pool", "target": target}
            for target in ("mic", "amp_read", "hemolysis", "toxicity", "developability")
        ),
        *(
            {"source": source, "target": "admission"}
            for source in ("mic", "amp_read", "hemolysis", "toxicity", "developability")
        ),
        {"source": "admission", "target": "targets"},
        {"source": "targets", "target": "boltz"},
        {"source": "boltz", "target": "rosetta"},
        {"source": "rosetta", "target": "portfolio"},
    ]
    edge_semantics: dict[tuple[str, str], tuple[str | None, str]] = {
        ("target_data", "knowledge"): (
            "上下文绑定",
            "冻结的靶点身份与证据标识共同确定知识卡片所需的上下文。",
        ),
        ("admission", "targets"): (
            f"入选{len(admitted_ids)}条",
            "只有被确定性成熟度决策记录为入选的候选，才会进入冻结靶点面板。",
        ),
        ("targets", "boltz"): (
            "原位与对照",
            "每条入选候选按靶点、原位或错误口袋对照通道及冻结随机种子进行分派。",
        ),
        ("boltz", "rosetta"): (
            "构象到精修样本",
            "每个已持久化的Boltz构象都会成为Rosetta界面精修的可追溯输入。",
        ),
        ("rosetta", "portfolio"): (
            "证据门槛",
            "结构证据通过来源与完整性检查后进入科学评审。",
        ),
    }
    for edge in graph_edges:
        source = edge["source"]
        target = edge["target"]
        label, rationale = edge_semantics.get((source, target), (None, ""))
        if not rationale and source == "knowledge":
            rationale = "设计分支读取冻结上下文，并应引用已持久化的知识卡片版本。"
        elif not rationale and target == "candidate_pool":
            rationale = "生成记录按序列身份合并为待评估候选集合。"
        elif not rationale and source == "candidate_pool":
            rationale = "同一冻结候选队列被提交给该评估模型。"
        elif not rationale and target == "admission":
            rationale = "已持久化的模型输出共同形成确定性的多目标入选记录。"
        edge["label"] = label
        edge["rationale"] = rationale
        edge["provenance"] = "topology"

    excluded_candidate_payloads = []
    if excluded_candidate_ids:
        excluded_rows = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.id.in_(excluded_candidate_ids))
                .order_by(Candidate.proposal_rank.nullslast(), Candidate.created_at, Candidate.id)
            )
        )
        excluded_candidate_payloads = [
            {
                "id": candidate.id,
                "sequence_sha256": candidate.sequence_sha256,
                "generation": candidate.generation,
                "display_eligible": False,
                "exclusion_reason": HISTORICAL_EXACT_REPLAY,
            }
            for candidate in excluded_rows
        ]

    return {
        "source": "postgresql",
        "read_only": True,
        "updated_at": _iso(max((event.occurred_at for event in events), default=run.created_at)),
        "run": {
            **_run_identity_payload(run),
            "name": _display_name(run),
            "kind": _run_kind(run),
            "schema_version": (run.spec_json or {}).get("schema_version"),
            "status": run.status,
            "spec_sha256": run.spec_sha256,
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
        },
        "display_population": display_population,
        "counts": {
            "candidates": candidate_count,
            "candidate_records": candidate_record_count,
            "excluded_candidates": excluded_candidate_count,
            "occurrences": occurrence_count,
            "evaluations": evaluation_count,
            "metrics_per_candidate": metric_count,
            "admitted": len(admitted_ids),
            "mature_core": len(mature_ids),
            "exploration": len(exploration_ids),
            "boltz_poses": boltz_count,
            "rosetta_decoys": rosetta_count,
        },
        "branches": branches,
        "admission": {
            "structure_dispatch_allowed": admission.get("structure_dispatch_allowed"),
            "refinement_required": admission.get("refinement_required"),
            "unused_structure_slots": admission.get("unused_structure_slots"),
            "forced_fill_used": admission.get("forced_fill_used"),
        },
        "tool_summary": tool_summary,
        "structure_counts": structure_counts,
        "checkpoints": [
            {
                "stage": item.stage_name,
                "order": item.stage_order,
                "current": item.durable_count,
                "total": item.expected_durable_count,
                "status": item.stage_status,
                "action": item.controller_action,
                "reasons": item.reasons_json,
                "observed_at": _iso(item.observed_at),
            }
            for item in latest_checkpoint.values()
        ],
        "graph": {"nodes": graph_nodes, "edges": graph_edges},
        "candidates": candidate_payloads,
        "candidate_exclusions": excluded_candidate_payloads,
        "viewer": viewer,
        "viewers": viewers,
        "events": [
            {
                "sequence_no": event.sequence_no,
                "type": event.event_type,
                "actor": event.actor,
                "payload": event.payload_json,
                "occurred_at": _iso(event.occurred_at),
            }
            for event in events
        ],
    }


@router.get("/runs/{run_id}/nodes/{node_id}")
async def get_observer_node(
    run_id: uuid.UUID,
    node_id: str,
    session: SessionDep,
) -> dict[str, Any]:
    run = await session.get(ExperimentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    excluded_candidate_ids = await _excluded_candidate_ids(session, run_id)

    all_calls = list(
        await session.scalars(
            select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.queued_at.desc())
        )
    )
    matching_calls = [call for call in all_calls if _call_matches_node(node_id, call.tool_name)]
    if node_id == "candidate_pool":
        matching_calls = [
            call
            for call in all_calls
            if any(name in call.tool_name.lower() for name in ("amp_designer", "ampgan", "hydramp"))
        ]
    call_ids = [call.id for call in matching_calls]

    structure_context_rows = (
        list(
            await session.execute(
                select(
                    MultiTargetStructureEvidenceRecord.tool_call_id,
                    Candidate.sequence,
                    Target.name,
                    MultiTargetStructureEvidenceRecord.control_lane,
                    MultiTargetStructureEvidenceRecord.boltz_seed,
                    MultiTargetStructureEvidenceRecord.evidence_kind,
                    func.count(),
                )
                .join(
                    Candidate,
                    Candidate.id == MultiTargetStructureEvidenceRecord.candidate_id,
                )
                .join(Target, Target.id == MultiTargetStructureEvidenceRecord.target_id)
                .where(
                    MultiTargetStructureEvidenceRecord.tool_call_id.in_(call_ids),
                    _display_eligible(Candidate),
                )
                .group_by(
                    MultiTargetStructureEvidenceRecord.tool_call_id,
                    Candidate.sequence,
                    Target.name,
                    MultiTargetStructureEvidenceRecord.control_lane,
                    MultiTargetStructureEvidenceRecord.boltz_seed,
                    MultiTargetStructureEvidenceRecord.evidence_kind,
                )
            )
        )
        if call_ids
        else []
    )
    structure_context_by_call: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for call_id, sequence, target_name, lane, seed, kind, records in structure_context_rows:
        structure_context_by_call[call_id].append(
            {
                "candidate_sequence": sequence,
                "target": target_name,
                "lane": lane,
                "seed": seed,
                "kind": kind,
                "records": int(records),
            }
        )

    artifact_rows = (
        list(
            await session.execute(
                select(EvidenceArtifact, Artifact)
                .join(Artifact, Artifact.id == EvidenceArtifact.artifact_id)
                .where(EvidenceArtifact.tool_call_id.in_(call_ids))
                .order_by(EvidenceArtifact.tool_call_id, EvidenceArtifact.role)
            )
        )
        if call_ids
        else []
    )
    artifacts_by_call: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for link, artifact in artifact_rows:
        artifacts_by_call[link.tool_call_id].append(
            {
                "role": link.role,
                "sha256": artifact.sha256,
                "size_bytes": artifact.size_bytes,
                "media_type": artifact.media_type,
                "url": f"/v1/observer/artifacts/{artifact.sha256}",
            }
        )

    evaluation_rows = (
        list(
            await session.scalars(
                select(Evaluation)
                .where(
                    Evaluation.tool_call_id.in_(call_ids),
                    Evaluation.candidate_id.not_in(excluded_candidate_ids),
                )
                .order_by(Evaluation.metric_name)
            )
        )
        if call_ids
        else []
    )
    metrics: dict[str, dict[str, Any]] = {}
    grouped_metrics: dict[str, list[Evaluation]] = defaultdict(list)
    for evaluation in evaluation_rows:
        grouped_metrics[evaluation.metric_name].append(evaluation)
    for metric_name, rows in grouped_metrics.items():
        numeric_values = [row.numeric_value for row in rows if row.numeric_value is not None]
        metrics[metric_name] = {
            "count": len(rows),
            "numeric_count": len(numeric_values),
            "mean": sum(numeric_values) / len(numeric_values) if numeric_values else None,
            "min": min(numeric_values) if numeric_values else None,
            "max": max(numeric_values) if numeric_values else None,
            "unit": next((row.unit for row in rows if row.unit), None),
            "out_of_domain": sum(1 for row in rows if row.out_of_domain),
            "text_counts": dict(
                sorted(
                    {
                        text_value: sum(1 for row in rows if row.text_value == text_value)
                        for text_value in {row.text_value for row in rows if row.text_value}
                    }.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            ),
            "status_counts": dict(
                sorted(
                    {
                        status_name: sum(1 for row in rows if row.status == status_name)
                        for status_name in {row.status for row in rows}
                    }.items()
                )
            ),
        }

    decisions = list(
        await session.scalars(
            select(AgentDecision)
            .where(AgentDecision.run_id == run_id)
            .order_by(AgentDecision.created_at.desc())
        )
    )
    admission = _admission_payload(decisions)
    admission_decisions = [
        item
        for item in admission.get("decisions", [])
        if isinstance(item, dict)
        and (
            not item.get("candidate_id")
            or uuid.UUID(str(item["candidate_id"])) not in excluded_candidate_ids
        )
    ]
    reason_counts: dict[str, int] = defaultdict(int)
    status_counts: dict[str, int] = defaultdict(int)
    for item in admission_decisions:
        status_counts[str(item.get("status") or "unknown")] += 1
        for reason in item.get("reasons", []):
            reason_counts[str(reason)] += 1

    candidate_record_count = int(
        (
            await session.scalar(
                select(func.count()).select_from(Candidate).where(Candidate.run_id == run_id)
            )
        )
        or 0
    )
    candidate_count = candidate_record_count - len(excluded_candidate_ids)
    occurrence_count = int(
        (
            await session.scalar(
                select(func.count())
                .select_from(CandidateOccurrence)
                .where(CandidateOccurrence.run_id == run_id)
            )
        )
        or 0
    )
    admitted_count = sum(
        uuid.UUID(value) not in excluded_candidate_ids
        for value in (
            *admission.get("mature_core_candidate_ids", []),
            *admission.get("exploration_candidate_ids", []),
        )
    )

    structure_rows = list(
        await session.execute(
            select(
                MultiTargetStructureEvidenceRecord.evidence_kind,
                MultiTargetStructureEvidenceRecord.control_lane,
                Target.name,
                func.count(),
                func.count(distinct(MultiTargetStructureEvidenceRecord.boltz_seed)),
            )
            .join(Candidate, Candidate.id == MultiTargetStructureEvidenceRecord.candidate_id)
            .join(Target, Target.id == MultiTargetStructureEvidenceRecord.target_id)
            .where(
                MultiTargetStructureEvidenceRecord.run_id == run_id,
                _display_eligible(Candidate),
            )
            .group_by(
                MultiTargetStructureEvidenceRecord.evidence_kind,
                MultiTargetStructureEvidenceRecord.control_lane,
                Target.name,
            )
            .order_by(Target.name, MultiTargetStructureEvidenceRecord.control_lane)
        )
    )

    narrative: list[str] = []
    if node_id == "target_data":
        narrative = [
            "冻结靶点面板定义下游设计与结构计算的生物学上下文。",
            "靶点身份与结构文件已持久化。",
        ]
    elif node_id == "knowledge":
        narrative = [
            "知识卡片读取记录：0。",
            "数据需求：卡片版本、内容标识与读取时间。",
        ]
    elif node_id == "candidate_pool":
        duplicate_count = max(0, occurrence_count - candidate_record_count)
        narrative = [
            f"三个设计分支共写入 {occurrence_count:,} 条提案，合并为 "
            f"{candidate_count:,} 条可展示候选序列。",
            f"进入模型评估前，按序列一致性去除了 {duplicate_count:,} 条重复记录。",
            f"另有 {len(excluded_candidate_ids):,} 条历史精确重放仅保留审计，不进入展示聚合。",
        ]
    elif node_id == "admission":
        core_count = sum(
            uuid.UUID(value) not in excluded_candidate_ids
            for value in admission.get("mature_core_candidate_ids", [])
        )
        exploration_count = sum(
            uuid.UUID(value) not in excluded_candidate_ids
            for value in admission.get("exploration_candidate_ids", [])
        )
        narrative = [
            f"确定性入选规则审查了 {len(admission_decisions):,} 条候选，"
            f"其中 {admitted_count:,} 条进入结构阶段。",
            f"入选集合：{core_count:,} 条成熟核心，{exploration_count:,} 条探索组。",
            "安全阈值：冻结规则。理由代码来源：持久化决策记录。",
        ]
    elif node_id == "boltz":
        pose_count = sum(row[3] for row in structure_rows if row[0] == "boltz_pose")
        narrative = [
            f"Boltz 已写入 {pose_count:,} 个复合物构象，覆盖冻结靶点、对照通道与随机种子。",
            "证据类型：计算结构预测。",
        ]
    elif node_id == "rosetta":
        decoy_count = sum(row[3] for row in structure_rows if row[0] == "rosetta_decoy")
        narrative = [
            f"Rosetta 已写入 {decoy_count:,} 个界面精修样本，并关联到对应的Boltz任务。",
            "跨轮次比较条件：计算流程一致。",
        ]
    elif node_id == "portfolio":
        narrative = [
            "评审输入：上游证据与候选组合决策。",
            "最终候选组合：待写入。",
        ]
    elif matching_calls:
        narrative = [
            f"该节点包含 {len(matching_calls):,} 次已持久化工具运行。",
            f"这些运行生成了 {len(evaluation_rows):,} 条评估记录，覆盖 {len(metrics):,} 个指标。",
            "证据类型：模型计算结果。",
        ]

    decision_payloads = []
    if node_id in {"admission", "portfolio"}:
        decision_payloads = [
            {
                "type": decision.decision_type,
                "agent_name": decision.agent_name,
                "agent_version": decision.agent_version,
                "model_name": decision.model_name,
                "status": decision.status,
                "created_at": _iso(decision.created_at),
                "prompt_sha256": decision.prompt_sha256,
                "response_sha256": decision.response_sha256,
                "policy": _compact_value((decision.structured_json or {}).get("policy", {})),
            }
            for decision in decisions
            if node_id == "admission" or "portfolio" in decision.decision_type
        ]

    return {
        "source": "postgresql",
        "read_only": True,
        "node_id": node_id,
        "display_population": _display_population(
            candidate_record_count,
            len(excluded_candidate_ids),
        ),
        "narrative": narrative,
        "calls": [
            {
                "id": call.id,
                "tool_name": call.tool_name,
                "tool_version": call.tool_version,
                "status": call.status,
                "attempt": call.attempt,
                "queued_at": _iso(call.queued_at),
                "started_at": _iso(call.started_at),
                "finished_at": _iso(call.finished_at),
                "duration_seconds": (
                    (call.finished_at - call.started_at).total_seconds()
                    if call.finished_at is not None and call.started_at is not None
                    else None
                ),
                "random_seed": call.random_seed,
                "model_uri": call.model_uri,
                "weights_sha256": call.weights_sha256,
                "environment_sha256": call.environment_sha256,
                "input_sha256": call.input_sha256,
                "output_sha256": call.output_sha256,
                "inputs": _compact_value(call.input_json),
                "parameters": _compact_value(call.parameters_json),
                "error": _compact_value(call.error_json),
                "structure_context": structure_context_by_call.get(call.id, []),
                "artifacts": artifacts_by_call.get(call.id, []),
            }
            for call in matching_calls[:40]
        ],
        "metrics": metrics,
        "reasoning": {
            "decisions": decision_payloads,
            "status_counts": dict(sorted(status_counts.items())),
            "reason_counts": dict(
                sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:16]
            ),
            "considered": len(admission_decisions),
            "admitted": admitted_count,
        },
        "structure_results": [
            {
                "kind": kind,
                "lane": lane,
                "target": target_name,
                "records": int(record_count),
                "seeds": int(seed_count),
            }
            for kind, lane, target_name, record_count, seed_count in structure_rows
            if (node_id == "boltz" and kind == "boltz_pose")
            or (node_id == "rosetta" and kind == "rosetta_decoy")
        ],
    }


@router.get("/artifacts/{sha256}")
async def get_observer_artifact(sha256: str, session: SessionDep) -> Response:
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256.lower()):
        raise HTTPException(status_code=400, detail="invalid artifact sha256")
    artifact = await session.scalar(select(Artifact).where(Artifact.sha256 == sha256.lower()))
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    payload = await asyncio.to_thread(ContentAddressedObjectStore().get_bytes, artifact.storage_uri)
    return Response(
        content=payload,
        media_type=artifact.media_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "ETag": f'"{artifact.sha256}"',
            "X-Content-SHA256": artifact.sha256,
            "Content-Disposition": f'inline; filename="{artifact.sha256}"',
        },
    )
