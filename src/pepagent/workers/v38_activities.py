from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from pepagent.db.models import (
    AgentDecision,
    Candidate,
    CandidateOccurrence,
    Evaluation,
    ExperimentRun,
    ExperimentRunTargetBranch,
    LifecycleEvent,
    MultiTargetStructureEvidenceRecord,
    RunStageCheckpoint,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_bytes, sha256_json, sha256_text
from pepagent.sequence_family import cluster_sequence_families
from pepagent.seven_branch_design import (
    SEQUENCE_METRICS,
    BranchDeliveryCandidate,
    BranchProgress,
    DesignBranch,
    SevenBranchRoundBinding,
    delivery_eligible_candidate_ids,
    next_branch_action,
    plan_branch_top_up,
    select_branch_delivery,
)
from pepagent.storage.object_store import ContentAddressedObjectStore
from pepagent.v38_final_portfolio import (
    StructureScoreEvidence,
    build_v38_final_portfolio,
)
from pepagent.v38_persistence import (
    GeneratorCellToolBinding,
    persist_multitarget_structure_evidence,
    persist_score_all_proposal_cohort,
)
from pepagent.v38_science_execution import (
    V38_METRIC_OBSERVATIONS,
    RawProposal,
    RefinementChildProposal,
    ScoreAllProposalCohort,
    V38SequenceExecutionContract,
    build_score_all_proposal_cohort,
)
from pepagent.v38_sequence_first_multitarget import (
    KnowledgeUseTrace,
    MetricObservation,
    MultiTargetBoltzEvidence,
    MultiTargetExecutionPlan,
    MultiTargetRosettaEvidence,
    MultiTargetStructureTask,
    RosettaDecoyEvidence,
    SequenceCandidateEvidence,
    SequenceCohortAdmission,
    SequenceRefinementPlan,
    TargetBranchSpec,
    admit_sequence_cohort,
    build_default_v38_maturity_policy,
    build_multitarget_structure_tasks,
    build_parallel_target_dispatch,
    build_sequence_refinement_plan,
    compute_leave_one_objective_out_rank_stability,
)
from pepagent.workers.activities import (
    _register_artifact,
    _select_boltz_structure_artifact,
    _store_json,
    predict_boltz2_complex,
    score_rosetta_complex,
)
from pepagent.workers.v37_activities import _select_v37_declared_observations
from pepagent.workflow_observer_contract import (
    ActivityLifecyclePayload,
    FormalWorkflowTopology,
    KnowledgeCardReadPayload,
    append_typed_lifecycle_event,
    build_candidate_decision_projection,
    persist_observer_checkpoints,
)


def _heartbeat_metric_persistence(stage: str) -> None:
    """Heartbeat in production while keeping activity functions unit-testable."""

    try:
        activity.heartbeat({"stage": stage})
    except RuntimeError as error:
        if str(error) != "Not in activity context":
            raise


@activity.defn(name="load_seven_branch_target_score_cohort")
async def load_seven_branch_target_score_cohort(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Read only fully score-all candidates from one target-specific child run."""

    run_id = uuid.UUID(str(request["run_id"]))
    binding = SevenBranchRoundBinding.model_validate(request["seven_branch_round"])
    if binding.branch_kind != "target_specific":
        raise ValueError("target score cohort requires a target-specific branch")
    async with SessionFactory() as session:
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == run_id)
                .order_by(Candidate.proposal_rank, Candidate.id)
            )
        )
        candidate_ids = [item.id for item in candidates]
        rows = list(
            await session.scalars(
                select(Evaluation).where(
                    Evaluation.candidate_id.in_(candidate_ids),
                    Evaluation.metric_name.in_(SEQUENCE_METRICS),
                )
            )
        ) if candidate_ids else []
    metrics_by_candidate: dict[uuid.UUID, set[str]] = {
        item.id: set() for item in candidates
    }
    for row in rows:
        if row.status == "succeeded":
            metrics_by_candidate[row.candidate_id].add(row.metric_name)
    incomplete = [
        str(candidate_id)
        for candidate_id, metric_names in metrics_by_candidate.items()
        if metric_names != set(SEQUENCE_METRICS)
    ]
    if incomplete:
        raise ValueError(
            "seven-branch target scoring cannot precede complete sequence score-all"
        )
    return {
        "schema_version": "ampgent.seven-branch-target-cohort.1",
        "run_id": str(run_id),
        "branch_key": binding.branch_key,
        "target_key": binding.target_key,
        "target_sequence_sha256": binding.target_sequence_sha256,
        "candidate_count": len(candidates),
        "peptides": [
            {
                "candidate_id": str(item.id),
                "sequence": item.sequence,
                "sequence_sha256": item.sequence_sha256,
            }
            for item in candidates
        ],
    }


@activity.defn(name="persist_seven_branch_round_progress")
async def persist_seven_branch_round_progress(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Persist a controller checkpoint from child-run durable evidence, not workflow claims."""

    controller_run_id = uuid.UUID(str(request["controller_run_id"]))
    round_run_id = uuid.UUID(str(request["round_run_id"]))
    binding = SevenBranchRoundBinding.model_validate(request["seven_branch_round"])
    child_result = request["child_result"]
    async with SessionFactory() as session, session.begin():
        raw_count = int(
            await session.scalar(
                select(func.count(CandidateOccurrence.id)).where(
                    CandidateOccurrence.run_id == round_run_id
                )
            )
            or 0
        )
        candidates = list(
            await session.scalars(
                select(Candidate).where(Candidate.run_id == round_run_id)
            )
        )
        candidate_ids = [item.id for item in candidates]
        evaluations = list(
            await session.scalars(
                select(Evaluation).where(Evaluation.candidate_id.in_(candidate_ids))
            )
        ) if candidate_ids else []
        sequence_metrics: dict[uuid.UUID, set[str]] = {
            item.id: set() for item in candidates
        }
        target_metrics: dict[uuid.UUID, set[str]] = {
            item.id: set() for item in candidates
        }
        for row in evaluations:
            if row.status != "succeeded":
                continue
            if row.metric_name in SEQUENCE_METRICS:
                sequence_metrics[row.candidate_id].add(row.metric_name)
            if row.metric_name in {"conditional_nll", "conditional_ppl"} and (
                row.raw_json.get("target", {}).get("sequence_sha256")
                == binding.target_sequence_sha256
            ):
                target_metrics[row.candidate_id].add(row.metric_name)
        fully_scored_count = sum(
            metrics == set(SEQUENCE_METRICS) for metrics in sequence_metrics.values()
        )
        target_scored_count = (
            sum(
                metrics == {"conditional_nll", "conditional_ppl"}
                for metrics in target_metrics.values()
            )
            if binding.branch_kind == "target_specific"
            else 0
        )
        admission = child_result.get("admission") or {}
        qualified_ids = set(admission.get("mature_core_candidate_ids", [])) | set(
            admission.get("exploration_candidate_ids", [])
        )
        family_count = len(
            {
                item.family_key
                for item in cluster_sequence_families(
                    item.sequence for item in candidates
                )
            }
        )
        progress = BranchProgress(
            branch_key=binding.branch_key,
            raw_count=raw_count,
            valid_unique_count=len(candidates),
            fully_scored_count=fully_scored_count,
            target_sequence_scored_count=target_scored_count,
            qualified_count=len(qualified_ids),
            delivered_count=0,
            family_count=family_count,
        )
        branch = DesignBranch.model_validate(request["branch"])
        controller_action = next_branch_action(branch, progress)
        expected_scored = len(candidates) if candidates else 1
        durable_count = (
            target_scored_count
            if binding.branch_kind == "target_specific"
            else fully_scored_count
        )
        checkpoint_payload = {
            "schema_version": "ampgent.seven-branch-checkpoint.1",
            "controller_run_id": str(controller_run_id),
            "round_run_id": str(round_run_id),
            "binding": binding.model_dump(mode="json"),
            "progress": progress.model_dump(mode="json"),
            "controller_action": controller_action,
            "schedule_sha256": request["schedule_sha256"],
        }
        receipt_sha256 = sha256_json(checkpoint_payload)
        observation_no = int(request["observation_no"])
        existing = await session.scalar(
            select(RunStageCheckpoint).where(
                RunStageCheckpoint.run_id == controller_run_id,
                RunStageCheckpoint.stage_name == "seven_branch_design",
                RunStageCheckpoint.observation_no == observation_no,
            )
        )
        if existing is not None:
            if existing.receipt_sha256 != receipt_sha256:
                raise ValueError("seven-branch checkpoint retry identity drifted")
        else:
            session.add(
                RunStageCheckpoint(
                    run_id=controller_run_id,
                    stage_name="seven_branch_design",
                    stage_order=2,
                    observation_no=observation_no,
                    durable_count=durable_count,
                    expected_durable_count=expected_scored,
                    stage_status=(
                        "completed" if durable_count == len(candidates) else "running"
                    ),
                    controller_action=controller_action,
                    reasons_json=[
                        f"branch={binding.branch_key}",
                        f"qualified={len(qualified_ids)}",
                        f"families={family_count}",
                    ],
                    tasks_json=[controller_action],
                    receipt_sha256=receipt_sha256,
                    observed_at=datetime.now(UTC),
                )
            )
        repository = ExperimentRepository(session)
        await repository.append_event(
            "run",
            controller_run_id,
            "seven_branch.round_observed",
            "seven-branch-controller",
            checkpoint_payload,
        )
    return {
        "progress": progress.model_dump(mode="json"),
        "controller_action": controller_action,
        "receipt_sha256": receipt_sha256,
    }


@activity.defn(name="persist_seven_branch_cumulative_selection")
async def persist_seven_branch_cumulative_selection(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Build one branch-local delivery view over immutable round evidence."""

    controller_run_id = uuid.UUID(str(request["controller_run_id"]))
    source_run_ids = tuple(
        uuid.UUID(str(item)) for item in request["source_run_ids"]
    )
    if not source_run_ids or len(source_run_ids) != len(set(source_run_ids)):
        raise ValueError("cumulative branch selection requires unique source runs")
    branch = DesignBranch.model_validate(request["branch"])
    context_sha = str(request["knowledge_context_pack_sha256"])
    if len(context_sha) != 64:
        raise ValueError("cumulative branch selection context SHA is invalid")
    async with SessionFactory() as session:
        controller = await session.get(ExperimentRun, controller_run_id)
        sources = list(
            await session.scalars(
                select(ExperimentRun).where(ExperimentRun.id.in_(source_run_ids))
            )
        )
        if controller is None or len(sources) != len(source_run_ids):
            raise ValueError("cumulative branch selection run lineage is incomplete")
        if any(
            item.spec_json.get("branch_key") != branch.branch_key
            or item.spec_json.get("design_contract_sha256")
            != request["design_contract_sha256"]
            for item in sources
        ):
            raise ValueError("cumulative branch selection mixes branch contracts")
        admission_payload, _ = await _build_v38_sequence_admission_payload(
            session=session,
            run_id=controller_run_id,
            refinement_round=max(
                int(item.spec_json.get("round_ordinal", 0)) for item in sources
            ),
            knowledge_context_pack_sha256=context_sha,
            source_run_ids=source_run_ids,
        )
        admission = admission_payload["admission"]
        mature_ids = {
            uuid.UUID(str(item)) for item in admission["mature_core_candidate_ids"]
        }
        qualified_ids = set(delivery_eligible_candidate_ids(admission))
        decision_by_id = {
            uuid.UUID(str(item["candidate_id"])): item
            for item in admission["decisions"]
        }
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id.in_(source_run_ids))
                .order_by(Candidate.id)
            )
        )
        source_order = {item: index for index, item in enumerate(source_run_ids)}
        candidates.sort(key=lambda item: (source_order[item.run_id], str(item.id)))
        unique_by_sequence: dict[str, Candidate] = {}
        for candidate in candidates:
            unique_by_sequence.setdefault(candidate.sequence_sha256, candidate)
        unique_candidates = list(unique_by_sequence.values())
        candidate_by_id = {item.id: item for item in unique_candidates}
        admitted_candidates = [
            candidate_by_id[item]
            for item in sorted(qualified_ids, key=str)
            if item in candidate_by_id
        ]
        family_by_sequence = {
            item.sequence: item.family_key
            for item in cluster_sequence_families(
                candidate.sequence for candidate in admitted_candidates
            )
        }
        target_scores: dict[uuid.UUID, dict[str, float]] = {
            item.id: {} for item in admitted_candidates
        }
        if admitted_candidates:
            score_rows = list(
                await session.scalars(
                    select(Evaluation).where(
                        Evaluation.candidate_id.in_(list(target_scores)),
                        Evaluation.metric_name.in_(
                            ("conditional_nll", "conditional_ppl")
                        ),
                        Evaluation.status == "succeeded",
                    )
                )
            )
            for row in score_rows:
                if branch.target_sequence_sha256 is not None and (
                    row.raw_json.get("target", {}).get("sequence_sha256")
                    != branch.target_sequence_sha256
                ):
                    continue
                if row.numeric_value is not None:
                    target_scores[row.candidate_id][row.metric_name] = float(
                        row.numeric_value
                    )
        delivery_candidates = tuple(
            BranchDeliveryCandidate(
                candidate_id=item.id,
                sequence_sha256=item.sequence_sha256,
                family_key=family_by_sequence[item.sequence],
                admission_tier=(
                    "mature_core"
                    if item.id in mature_ids
                    else "promising_uncertain"
                ),
                sequence_pareto_front=decision_by_id[item.id].get("pareto_front"),
                target_conditional_nll=target_scores[item.id].get("conditional_nll"),
                target_conditional_ppl=target_scores[item.id].get("conditional_ppl"),
            )
            for item in admitted_candidates
        )
        selection = select_branch_delivery(branch, delivery_candidates)
        raw_count = int(
            await session.scalar(
                select(func.count(CandidateOccurrence.id)).where(
                    CandidateOccurrence.run_id.in_(source_run_ids)
                )
            )
            or 0
        )
        candidate_ids = [item.id for item in unique_candidates]
        evaluation_rows = list(
            await session.scalars(
                select(Evaluation).where(Evaluation.candidate_id.in_(candidate_ids))
            )
        ) if candidate_ids else []
        sequence_coverage = {item: set() for item in candidate_ids}
        target_coverage = {item: set() for item in candidate_ids}
        for row in evaluation_rows:
            if row.status != "succeeded":
                continue
            if row.metric_name in SEQUENCE_METRICS:
                sequence_coverage[row.candidate_id].add(row.metric_name)
            if row.metric_name in {"conditional_nll", "conditional_ppl"} and (
                branch.target_sequence_sha256 is not None
                and row.raw_json.get("target", {}).get("sequence_sha256")
                == branch.target_sequence_sha256
            ):
                target_coverage[row.candidate_id].add(row.metric_name)
        progress = BranchProgress(
            branch_key=branch.branch_key,
            raw_count=raw_count,
            valid_unique_count=len(unique_candidates),
            fully_scored_count=sum(
                item == set(SEQUENCE_METRICS) for item in sequence_coverage.values()
            ),
            target_sequence_scored_count=(
                sum(
                    item == {"conditional_nll", "conditional_ppl"}
                    for item in target_coverage.values()
                )
                if branch.target_sequence_interaction_required
                else 0
            ),
            qualified_count=len(qualified_ids),
            delivered_count=len(selection.selected_candidate_ids),
            family_count=len({item.family_key for item in delivery_candidates}),
        )
        top_up = plan_branch_top_up(
            branch,
            progress,
            next_round_ordinal=max(
                int(item.spec_json.get("round_ordinal", 0)) for item in sources
            )
            + 1,
        )
    payload = {
        "schema_version": "ampgent.seven-branch-cumulative-selection.1",
        "controller_run_id": str(controller_run_id),
        "branch": branch.model_dump(mode="json"),
        "source_run_ids": [str(item) for item in source_run_ids],
        "admission_sha256": sha256_json(admission_payload),
        "selection": selection.model_dump(mode="json"),
        "progress": progress.model_dump(mode="json"),
        "top_up_plan": top_up.model_dump(mode="json"),
    }
    artifact = await _store_json(payload)
    response = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    async with SessionFactory() as session, session.begin():
        existing = await session.scalar(
            select(AgentDecision).where(
                AgentDecision.run_id == controller_run_id,
                AgentDecision.generation == top_up.next_round_ordinal,
                AgentDecision.decision_type
                == f"seven_branch_delivery:{branch.branch_key}",
            )
        )
        if existing is not None:
            if existing.structured_json != payload:
                raise ValueError("existing seven-branch delivery selection drifted")
        else:
            existing = await ExperimentRepository(session).record_agent_decision(
                controller_run_id,
                top_up.next_round_ordinal,
                f"seven_branch_delivery:{branch.branch_key}",
                "deterministic-seven-branch-design-agent",
                str(request["worker_source_revision"]),
                (
                    "Deduplicate exact sequences, apply frozen admission, rank within "
                    "the branch and select families before filling repeated families."
                ),
                response,
                payload,
                model_name="deterministic://seven-branch-delivery-v1",
            )
        event_payload = {
            "schema_version": "ampgent.seven-branch-selection-receipt.1",
            "decision_id": str(existing.id),
            "artifact": asdict(artifact),
            "selection_sha256": selection.sha256(),
            "top_up_plan_sha256": top_up.sha256(),
        }
        receipt_sha = sha256_json(event_payload)
        prior_event = await session.scalar(
            select(LifecycleEvent).where(
                LifecycleEvent.aggregate_type == "run",
                LifecycleEvent.aggregate_id == controller_run_id,
                LifecycleEvent.event_type
                == "seven_branch.cumulative_selection_persisted",
                LifecycleEvent.payload_sha256 == receipt_sha,
            )
        )
        if prior_event is None:
            await ExperimentRepository(session).append_event(
                "run",
                controller_run_id,
                "seven_branch.cumulative_selection_persisted",
                "seven-branch-controller",
                event_payload,
            )
    return {
        "artifact": asdict(artifact),
        "progress": progress.model_dump(mode="json"),
        "selection": selection.model_dump(mode="json"),
        "top_up_plan": top_up.model_dump(mode="json"),
    }

V38_METRIC_RESULT_REFERENCE_SCHEMA = "v38.metric-result-reference.1"
V38_ADMISSION_REFERENCE_SCHEMA = "v38.sequence-admission-reference.1"
V39_CROSS_ROUND_ADMISSION_REFERENCE_SCHEMA = (
    "v39.cross-round-admission-reference.1"
)


async def _resolve_v39_round_admission(child_result: dict[str, Any]) -> dict[str, Any]:
    """Resolve full candidate IDs from the child's content-addressed admission."""

    summary = child_result.get("admission")
    reference = child_result.get("admission_reference")
    if not isinstance(summary, dict) or not isinstance(reference, dict):
        raise ValueError("v39 round result lacks durable admission evidence")
    payload = await _resolve_v38_admission(reference)
    admission = payload.get("admission")
    if not isinstance(admission, dict):
        raise ValueError("v39 round admission artifact lacks an admission payload")
    expected_counts = {
        "mature_core_count": len(admission.get("mature_core_candidate_ids", [])),
        "exploration_count": len(admission.get("exploration_candidate_ids", [])),
        "rejected_count": len(admission.get("rejected_candidate_ids", [])),
    }
    if any(summary.get(key) != value for key, value in expected_counts.items()):
        raise ValueError("v39 round admission summary differs from durable artifact")
    return admission


@activity.defn(name="persist_v39_exploration_round_yield")
async def persist_v39_exploration_round_yield(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Measure one completed round from DB state and persist an idempotent receipt."""

    controller_run_id = uuid.UUID(str(request["controller_run_id"]))
    round_run_id = uuid.UUID(str(request["round_run_id"]))
    prior_run_ids = tuple(
        uuid.UUID(str(item)) for item in request.get("prior_round_run_ids", [])
    )
    round_ordinal = int(request["round_ordinal"])
    child_result = request["child_result"]
    admission = await _resolve_v39_round_admission(child_result)
    mature_ids = {uuid.UUID(str(item)) for item in admission["mature_core_candidate_ids"]}
    exploration_ids = {
        uuid.UUID(str(item)) for item in admission["exploration_candidate_ids"]
    }

    async with SessionFactory() as session, session.begin():
        if await session.get(ExperimentRun, controller_run_id) is None:
            raise ValueError("v39 controller run does not exist")
        if await session.get(ExperimentRun, round_run_id) is None:
            raise ValueError("v39 round run does not exist")
        current_rows = tuple(
            (
                await session.execute(
                    select(
                        Candidate.id,
                        Candidate.sequence_sha256,
                        Candidate.metadata_json,
                    ).where(Candidate.run_id == round_run_id)
                )
            ).all()
        )
        prior_hashes: set[str] = set()
        if prior_run_ids:
            prior_hashes = set(
                (
                    await session.scalars(
                        select(Candidate.sequence_sha256).where(
                            Candidate.run_id.in_(prior_run_ids)
                        )
                    )
                ).all()
            )
        raw_occurrences = int(
            await session.scalar(
                select(func.count())
                .select_from(CandidateOccurrence)
                .where(CandidateOccurrence.run_id == round_run_id)
            )
            or 0
        )
        novel_ids = {
            candidate_id
            for candidate_id, sequence_sha256, _metadata in current_rows
            if sequence_sha256 not in prior_hashes
        }
        family_keys = {
            str(metadata.get("sequence_family_key"))
            for _candidate_id, _sequence_sha256, metadata in current_rows
            if isinstance(metadata, dict) and metadata.get("sequence_family_key")
        }
        observation = {
            "batch_ordinal": round_ordinal,
            "raw_occurrences": raw_occurrences,
            "valid_unique_sequences": len(current_rows),
            "historically_novel_sequences": len(novel_ids),
            "sequence_family_count": len(family_keys),
            "safety_admissible_sequences": len(mature_ids | exploration_ids),
            "activity_supported_sequences": len(mature_ids),
            "new_pareto_extensions": len(mature_ids & novel_ids),
        }
        payload = {
            "schema_version": "ampgent.sequence-space-round-yield.1",
            "controller_run_id": str(controller_run_id),
            "round_run_id": str(round_run_id),
            "round_ordinal": round_ordinal,
            "observation": observation,
            "sequence_family_observation_complete": bool(family_keys),
            "exploration_contract_sha256": request[
                "exploration_contract_sha256"
            ],
            "schedule_sha256": request["schedule_sha256"],
        }
        receipt_sha256 = sha256_json(payload)
        existing = await session.scalar(
            select(LifecycleEvent).where(
                LifecycleEvent.aggregate_type == "run",
                LifecycleEvent.aggregate_id == controller_run_id,
                LifecycleEvent.event_type == "v39.exploration_round_yield_observed",
                LifecycleEvent.payload_sha256 == receipt_sha256,
            )
        )
        if existing is None:
            await ExperimentRepository(session).append_event(
                "run",
                controller_run_id,
                "v39.exploration_round_yield_observed",
                "v39-sequence-space-controller",
                payload,
            )
    return {"observation": observation, "receipt_sha256": receipt_sha256}


@activity.defn(name="persist_v39_exploration_controller_action")
async def persist_v39_exploration_controller_action(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Persist the replayable action selected from one durable round yield."""

    controller_run_id = uuid.UUID(str(request["controller_run_id"]))
    round_ordinal = int(request["round_ordinal"])
    observation = request["observation"]
    payload = {
        "schema_version": "ampgent.sequence-space-controller-action.1",
        "controller_run_id": str(controller_run_id),
        "round_run_id": str(request["round_run_id"]),
        "round_ordinal": round_ordinal,
        "action": str(request["action"]),
        "observation": observation,
        "schedule_sha256": str(request["schedule_sha256"]),
    }
    receipt_sha256 = sha256_json(payload)
    async with SessionFactory() as session, session.begin():
        if await session.get(ExperimentRun, controller_run_id) is None:
            raise ValueError("v39 controller run does not exist")
        existing = await session.scalar(
            select(RunStageCheckpoint).where(
                RunStageCheckpoint.run_id == controller_run_id,
                RunStageCheckpoint.stage_name == "sequence_space_exploration",
                RunStageCheckpoint.observation_no == round_ordinal,
            )
        )
        if existing is not None:
            if existing.receipt_sha256 != receipt_sha256:
                raise ValueError("v39 controller checkpoint identity drifted")
        else:
            session.add(
                RunStageCheckpoint(
                    run_id=controller_run_id,
                    stage_name="sequence_space_exploration",
                    stage_order=2,
                    observation_no=round_ordinal,
                    durable_count=int(observation["raw_occurrences"]),
                    expected_durable_count=1800,
                    stage_status=(
                        "completed"
                        if int(observation["raw_occurrences"]) == 1800
                        else "failed"
                    ),
                    controller_action=str(request["action"]),
                    reasons_json=[
                        f"historically_novel={observation['historically_novel_sequences']}",
                        f"new_pareto_extensions={observation['new_pareto_extensions']}",
                    ],
                    tasks_json=["execute_next_pre_frozen_round"],
                    receipt_sha256=receipt_sha256,
                    observed_at=datetime.now(UTC),
                )
            )
    return {"receipt_sha256": receipt_sha256}


@activity.defn(name="persist_v38_external_activity_lifecycle")
async def persist_v38_external_activity_lifecycle(
    request: dict[str, Any],
) -> dict[str, Any]:
    payload = ActivityLifecyclePayload.model_validate(request["payload"])
    async with SessionFactory() as session, session.begin():
        run = await session.get(ExperimentRun, payload.run_id)
        if run is None:
            raise ValueError("external activity lifecycle run does not exist")
        topology = FormalWorkflowTopology.model_validate(
            run.spec_json["workflow_topology"]
        )
        event = await append_typed_lifecycle_event(session, payload)
        await persist_observer_checkpoints(
            session, run_id=payload.run_id, topology=topology
        )
    return {
        "event_id": str(event.id),
        "event_type": f"activity.{payload.status}",
        "payload_sha256": event.payload_sha256,
    }


def _validate_v38_structure_request(
    request: dict[str, Any],
) -> tuple[MultiTargetStructureTask, TargetBranchSpec, dict[str, Any]]:
    task = MultiTargetStructureTask.model_validate(request["structure_task"])
    branch = TargetBranchSpec.model_validate(request["target_branch"])
    candidate = request["candidate"]
    if not isinstance(candidate, dict) or str(candidate.get("id")) != str(task.candidate_id):
        raise ValueError("v38 structure candidate does not match the frozen task")
    if task.target_id != branch.target_id or task.target_key != branch.target_key:
        raise ValueError("v38 structure target does not match the frozen branch")
    expected_pocket = (
        branch.native_pocket_sha256
        if task.control_lane == "native"
        else branch.wrong_pocket_sha256
    )
    if task.pocket_sha256 != expected_pocket:
        raise ValueError("v38 structure task uses the wrong control pocket")
    target_sequence = str(request["target_sequence"])
    if sha256_text(target_sequence) != branch.target_sequence_sha256:
        raise ValueError("v38 structure target sequence SHA drifted")
    if request.get("pocket_definition_sha256") != task.pocket_sha256:
        raise ValueError("v38 structure pocket definition SHA drifted")
    return task, branch, candidate


def _v38_structure_work_scope(task: MultiTargetStructureTask) -> list[str]:
    return [str(task.target_id), task.control_lane, task.sha256()]


def build_v38_multitarget_task_plan(
    *,
    execution_plan: MultiTargetExecutionPlan,
    admission_payload: dict[str, Any],
    boltz_seeds: tuple[int, ...],
) -> dict[str, Any]:
    admission = SequenceCohortAdmission.model_validate(admission_payload["admission"])
    if admission.refinement_required or not admission.structure_dispatch_allowed:
        raise ValueError("v38 structure tasks require a concluded sequence admission")
    if execution_plan.shared_sequence_cohort_sha256 != admission_payload.get(
        "candidate_evidence_sha256"
    ):
        raise ValueError("v38 execution plan does not bind the admitted sequence cohort")
    admitted_ids = (
        *admission.mature_core_candidate_ids,
        *admission.exploration_candidate_ids,
    )
    if not admitted_ids:
        raise ValueError("v38 sequence admission produced no structure candidates")
    if len(admitted_ids) != len(set(admitted_ids)):
        raise ValueError("v38 sequence admission duplicated a structure candidate")
    dispatches = build_parallel_target_dispatch(
        execution_plan,
        mature_candidate_ids=admitted_ids,
    )
    tasks = build_multitarget_structure_tasks(
        execution_plan,
        dispatches=dispatches,
        boltz_seeds=boltz_seeds,
    )
    expected_count = (
        len(admitted_ids)
        * len(execution_plan.target_branches)
        * 2
        * len(boltz_seeds)
    )
    if len(tasks) != expected_count:
        raise ValueError("v38 multi-target structure task cardinality drifted")
    return {
        "schema_version": "v38.multitarget-structure-task-plan.1",
        "execution_plan_sha256": sha256_json(
            execution_plan.model_dump(mode="json")
        ),
        "candidate_evidence_sha256": admission_payload["candidate_evidence_sha256"],
        "admitted_candidate_ids": [str(item) for item in admitted_ids],
        "mature_core_count": len(admission.mature_core_candidate_ids),
        "exploration_count": len(admission.exploration_candidate_ids),
        "target_count": len(execution_plan.target_branches),
        "control_lanes": ["native", "wrong_pocket"],
        "boltz_seeds": list(boltz_seeds),
        "task_count": len(tasks),
        "tasks": [item.model_dump(mode="json") for item in tasks],
    }


@activity.defn(name="plan_v38_multitarget_structure")
async def plan_v38_multitarget_structure(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(str(request["run_id"]))
    reference = request["admission_reference"]
    admission_payload = await _resolve_v38_admission(reference)
    if admission_payload.get("run_id") != str(run_id):
        raise ValueError("v38 structure planning admission belongs to another run")
    template = request["multitarget_plan_template"]
    if not isinstance(template, dict) or set(template) != {
        "harness_release_id",
        "history_snapshot_sha256",
        "target_branches",
        "max_parallel_targets",
    }:
        raise ValueError("v38 multitarget plan template is invalid")
    execution_plan = MultiTargetExecutionPlan(
        **template,
        shared_sequence_cohort_sha256=admission_payload[
            "candidate_evidence_sha256"
        ],
        sequence_maturity_decision_sha256=reference["admission_sha256"],
    )
    planned = build_v38_multitarget_task_plan(
        execution_plan=execution_plan,
        admission_payload=admission_payload,
        boltz_seeds=tuple(int(item) for item in request["boltz_seeds"]),
    )
    candidate_ids = [uuid.UUID(item) for item in planned["admitted_candidate_ids"]]
    source_run_ids = tuple(
        uuid.UUID(str(item)) for item in admission_payload.get("source_run_ids", [])
    )
    async with SessionFactory() as session:
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.id.in_(candidate_ids))
                .order_by(Candidate.id)
            )
        )
        if source_run_ids:
            source_runs = list(
                await session.scalars(
                    select(ExperimentRun).where(ExperimentRun.id.in_(source_run_ids))
                )
            )
            if len(source_runs) != len(source_run_ids) or any(
                item.parent_run_id != run_id for item in source_runs
            ):
                raise ValueError("v39 structure sources are not controller children")
            candidate_scope_valid = all(
                item.run_id in source_run_ids for item in candidates
            )
        else:
            candidate_scope_valid = all(item.run_id == run_id for item in candidates)
    if (
        {item.id for item in candidates} != set(candidate_ids)
        or not candidate_scope_valid
    ):
        raise ValueError("v38 structure plan references a missing or cross-run candidate")
    planned["candidates"] = [
        {
            "id": str(item.id),
            "sequence": item.sequence,
            "sequence_sha256": item.sequence_sha256,
        }
        for item in candidates
    ]
    planned["task_plan_sha256"] = sha256_json(planned)
    return planned


@activity.defn(name="predict_v38_multitarget_structure")
async def predict_v38_multitarget_structure(request: dict[str, Any]) -> dict[str, Any]:
    task, _, candidate = _validate_v38_structure_request(request)
    if int(request.get("seed", task.boltz_seed)) != task.boltz_seed:
        raise ValueError("v38 Boltz seed differs from the frozen task")
    spec = dict(request["structure_spec"])
    spec["seed"] = task.boltz_seed
    spec["target"] = {
        "sequence": request["target_sequence"],
        "pocket_residues": list(request["pocket_residues"]),
    }
    result = await predict_boltz2_complex(
        {
            "run_id": request["run_id"],
            "candidate": candidate,
            "spec": spec,
            "seed": task.boltz_seed,
            "work_scope": _v38_structure_work_scope(task),
        }
    )
    if str(result["candidate"]["id"]) != str(task.candidate_id):
        raise ValueError("v38 Boltz result candidate identity drifted")
    if int(result["input"]["seed"]) != task.boltz_seed:
        raise ValueError("v38 Boltz result seed identity drifted")
    return {
        **result,
        "v38_structure_task": task.model_dump(mode="json"),
        "v38_structure_task_sha256": task.sha256(),
    }


@activity.defn(name="score_v38_multitarget_rosetta")
async def score_v38_multitarget_rosetta(request: dict[str, Any]) -> dict[str, Any]:
    task, _, candidate = _validate_v38_structure_request(request)
    structure = request["structure"]
    if structure.get("v38_structure_task_sha256") != task.sha256():
        raise ValueError("v38 Rosetta input is not the bound Boltz task")
    if str(structure.get("candidate", {}).get("id")) != str(task.candidate_id):
        raise ValueError("v38 Rosetta input candidate identity drifted")
    spec = dict(request["structure_spec"])
    spec["seed"] = task.boltz_seed
    spec["rosetta_nstruct"] = task.rosetta_decoys_per_pose
    result = await score_rosetta_complex(
        {
            "run_id": request["run_id"],
            "candidate": candidate,
            "structure": structure,
            "spec": spec,
            "seed": task.boltz_seed,
            "work_scope": _v38_structure_work_scope(task),
        }
    )
    if int(result["parameters"]["nstruct"]) != task.rosetta_decoys_per_pose:
        raise ValueError("v38 Rosetta result decoy budget drifted")
    return {
        **result,
        "v38_structure_task": task.model_dump(mode="json"),
        "v38_structure_task_sha256": task.sha256(),
    }


def build_v38_boltz_evidence(result: dict[str, Any]) -> MultiTargetBoltzEvidence:
    task = MultiTargetStructureTask.model_validate(result["v38_structure_task"])
    if result.get("v38_structure_task_sha256") != task.sha256():
        raise ValueError("v38 Boltz result task SHA drifted")
    coordinate = _select_boltz_structure_artifact(result)
    provenance = result["provenance"]
    return MultiTargetBoltzEvidence(
        task=task,
        task_sha256=task.sha256(),
        tool_call_id=uuid.UUID(result["tool_call_id"]),
        coordinate_artifact_sha256=coordinate["sha256"],
        raw_result_artifact_sha256=provenance["raw_output_artifact"]["sha256"],
        parameters_sha256=sha256_json(result["parameters"]),
    )


def build_v38_rosetta_evidence(
    result: dict[str, Any],
    boltz: MultiTargetBoltzEvidence,
) -> MultiTargetRosettaEvidence:
    task = MultiTargetStructureTask.model_validate(result["v38_structure_task"])
    if result.get("v38_structure_task_sha256") != task.sha256() or task != boltz.task:
        raise ValueError("v38 Rosetta result task differs from its Boltz evidence")
    raw_decoys = result.get("rosetta", {}).get("decoys")
    if not isinstance(raw_decoys, list):
        raise ValueError("v38 Rosetta result lacks exact decoy evidence")
    rosetta_result = result["rosetta"]
    source_coordinate = result.get("provenance", {}).get("source_coordinate_artifact", {})
    if source_coordinate.get("sha256") != boltz.coordinate_artifact_sha256:
        raise ValueError("v38 Rosetta source coordinate differs from its Boltz artifact")
    decoys = tuple(
        RosettaDecoyEvidence(
            decoy_ordinal=index,
            input_structure_sha256=item["input_sha256"],
            output_structure_sha256=item["output_sha256"],
            score_record_sha256=item["score_terms_sha256"],
            total_score=float(item["total_score"]),
        )
        for index, item in enumerate(raw_decoys)
    )
    return MultiTargetRosettaEvidence(
        task=task,
        task_sha256=task.sha256(),
        boltz_evidence_sha256=boltz.sha256(),
        boltz_coordinate_artifact_sha256=boltz.coordinate_artifact_sha256,
        converted_input_artifact_sha256=rosetta_result["input_sha256"],
        prepared_input_artifact_sha256=rosetta_result["prepared_input_sha256"],
        prepacked_input_artifact_sha256=rosetta_result["prepacked_input_sha256"],
        tool_call_id=uuid.UUID(result["tool_call_id"]),
        raw_result_artifact_sha256=result["provenance"]["raw_output_artifact"]["sha256"],
        decoys=decoys,
    )


def build_v38_structure_artifact_link(
    *,
    task: MultiTargetStructureTask,
    tool: str,
    artifact: dict[str, Any],
    index: int,
) -> tuple[str, dict[str, Any]]:
    suffix = PurePosixPath(str(artifact.get("path", ""))).suffix.lower()
    is_coordinate = artifact.get("media_type") in {
        "chemical/x-cif",
        "chemical/x-mmcif",
        "chemical/x-pdb",
    } or suffix in {".cif", ".mmcif", ".pdb"}
    role = "structure_coordinate" if is_coordinate else f"engine_output_{index}"
    return role, {
        "schema_version": "v38.structure-artifact-link.1",
        "tool": tool,
        "protocol": "v38-multitarget",
        "target_id": str(task.target_id),
        "target_key": task.target_key,
        "candidate_id": str(task.candidate_id),
        "control_lane": task.control_lane,
        "boltz_seed": task.boltz_seed,
        "structure_task_sha256": task.sha256(),
        "artifact_role": role,
        "relative_path": artifact["path"],
        "coordinate_format": suffix.lstrip(".") if is_coordinate else None,
    }


async def _register_v38_runtime_artifacts(
    session: AsyncSession,
    *,
    tool_call_id: uuid.UUID,
    provenance: dict[str, Any],
    tool: str,
    task: MultiTargetStructureTask,
) -> None:
    candidate_id = str(task.candidate_id)
    common_metadata = {
        "schema_version": "v38.structure-artifact-link.1",
        "tool": tool,
        "protocol": "v38-multitarget",
        "target_id": str(task.target_id),
        "target_key": task.target_key,
        "candidate_id": candidate_id,
        "control_lane": task.control_lane,
        "boltz_seed": task.boltz_seed,
        "structure_task_sha256": task.sha256(),
    }
    await _register_artifact(
        session,
        tool_call_id,
        provenance["raw_output_artifact"],
        "raw_output",
        {**common_metadata, "artifact_role": "raw_output"},
    )
    await _register_artifact(
        session,
        tool_call_id,
        provenance["environment_artifact"],
        "environment_manifest",
        {**common_metadata, "artifact_role": "environment_manifest"},
    )
    for index, artifact in enumerate(provenance["engine_artifacts"]):
        role, metadata = build_v38_structure_artifact_link(
            task=task, tool=tool, artifact=artifact, index=index
        )
        await _register_artifact(
            session,
            tool_call_id,
            artifact,
            role,
            metadata,
        )


@activity.defn(name="persist_v38_multitarget_boltz")
async def persist_v38_multitarget_boltz(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    result = request["structure_result"]
    task = MultiTargetStructureTask.model_validate(result["v38_structure_task"])
    if result.get("v38_structure_task_sha256") != task.sha256():
        raise ValueError("v38 Boltz persistence task SHA drifted")
    provenance = result["provenance"]
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            provenance["tool_name"],
            provenance["tool_version"],
            provenance["environment_sha256"],
            {**result["input"], "v38_structure_task_sha256": task.sha256()},
            result["parameters"],
            result["boltz2"],
            weights_sha256=provenance["weights_sha256"],
            model_uri=provenance["model_uri"],
            random_seed=task.boltz_seed,
            attempt=provenance["attempt"],
            logical_stage="structure_boltz",
            display_category="structure",
        )
        await _register_v38_runtime_artifacts(
            session,
            tool_call_id=call.id,
            provenance=provenance,
            tool="boltz2",
            task=task,
        )
        await _register_artifact(
            session,
            call.id,
            provenance["weight_manifest_artifact"],
            "weight_manifest",
            {"tool": "boltz2", "weights_sha256": provenance["weights_sha256"]},
        )
        result = {**result, "tool_call_id": str(call.id)}
        evidence = build_v38_boltz_evidence(result)
    return {"structure": result, "boltz_evidence": evidence.model_dump(mode="json")}


@activity.defn(name="persist_v38_multitarget_rosetta")
async def persist_v38_multitarget_rosetta(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    result = request["rosetta_result"]
    boltz = MultiTargetBoltzEvidence.model_validate(request["boltz_evidence"])
    task = MultiTargetStructureTask.model_validate(result["v38_structure_task"])
    if task != boltz.task or result.get("v38_structure_task_sha256") != task.sha256():
        raise ValueError("v38 Rosetta persistence is not bound to its Boltz task")
    provenance = result["provenance"]
    if uuid.UUID(provenance["parent_tool_call_id"]) != boltz.tool_call_id:
        raise ValueError("v38 Rosetta parent ToolCall differs from Boltz evidence")
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            provenance["tool_name"],
            provenance["tool_version"],
            provenance["environment_sha256"],
            {**result["input"], "v38_structure_task_sha256": task.sha256()},
            result["parameters"],
            result["rosetta"],
            weights_sha256=provenance["weights_sha256"],
            model_uri=provenance["model_uri"],
            random_seed=task.boltz_seed,
            attempt=provenance["attempt"],
            logical_stage="structure_rosetta",
            display_category="structure",
        )
        await repository.record_tool_dependency(call.id, boltz.tool_call_id, "refines")
        await _register_v38_runtime_artifacts(
            session,
            tool_call_id=call.id,
            provenance=provenance,
            tool="rosetta",
            task=task,
        )
        result = {**result, "tool_call_id": str(call.id)}
        rosetta = build_v38_rosetta_evidence(result, boltz)
        receipt = await persist_multitarget_structure_evidence(
            session,
            run_id=run_id,
            boltz=boltz,
            rosetta=rosetta,
        )
    return {
        "rosetta_evidence": rosetta.model_dump(mode="json"),
        "persistence_receipt": receipt.model_dump(mode="json"),
    }


def validate_v38_refinement_result(
    plan: SequenceRefinementPlan,
    result: dict[str, Any],
) -> tuple[RefinementChildProposal, ...]:
    raw = result.get("proposals")
    if not isinstance(raw, list):
        raise ValueError("v38 refinement result lacks proposals")
    proposals = tuple(RefinementChildProposal.model_validate(item) for item in raw)
    tasks = {task.parent_candidate_id: task for task in plan.tasks}
    expected = {parent_id: task.requested_children for parent_id, task in tasks.items()}
    observed = {parent_id: 0 for parent_id in expected}
    for proposal in proposals:
        if proposal.refinement_round != plan.refinement_round:
            raise ValueError("v38 refinement proposal round differs from plan")
        if proposal.parent_candidate_id not in observed:
            raise ValueError("v38 refinement proposal parent is not planned")
        task = tasks[proposal.parent_candidate_id]
        if "".join(proposal.parent_sequence.split()).upper() != task.parent_sequence:
            raise ValueError("v38 refinement proposal parent sequence drifted")
        if any(
            trace.provider_task_id != task.provider_task_id
            for trace in proposal.knowledge_traces
        ):
            raise ValueError("v38 refinement proposal cites another knowledge provider task")
        observed[proposal.parent_candidate_id] += 1
    if observed != expected:
        raise ValueError("v38 refinement result does not exactly cover planned children")
    return proposals


def build_v38_score_all_cohort_from_results(
    contract: V38SequenceExecutionContract,
    generated_cells: list[dict[str, Any]],
) -> ScoreAllProposalCohort:
    by_identity: dict[tuple[str, int], dict[str, Any]] = {}
    for generated in generated_cells:
        result = generated.get("result")
        if not isinstance(result, dict):
            raise ValueError("v38 generated cell lacks a result object")
        identity = (str(result.get("generator_id")), int(result.get("seed", -1)))
        if identity in by_identity:
            raise ValueError("v38 generated cell identity is duplicated")
        by_identity[identity] = generated
    expected = {(cell.generator_id, cell.seed) for cell in contract.cells}
    if set(by_identity) != expected:
        raise ValueError("v38 generated cells do not exactly cover the frozen contract")
    proposals: list[RawProposal] = []
    for cell in contract.cells:
        result = by_identity[(cell.generator_id, cell.seed)]["result"]
        records = result.get("records")
        if (
            int(result.get("raw_proposal_budget", -1)) != cell.requested_proposals
            or not isinstance(records, list)
            or len(records) != cell.requested_proposals
        ):
            raise ValueError("v38 generated cell count differs from the frozen contract")
        for expected_rank, record in enumerate(records, start=1):
            if not isinstance(record, dict) or int(record.get("raw_rank", -1)) != expected_rank:
                raise ValueError("v38 generated cell raw ranks are not contiguous")
            proposals.append(
                RawProposal(
                    generator_id=cell.generator_id,
                    seed=cell.seed,
                    raw_rank=expected_rank,
                    sequence=str(record.get("sequence", "")),
                )
            )
    return build_score_all_proposal_cohort(contract, proposals)


async def _resolve_v38_metric_result(reference: dict[str, Any]) -> dict[str, Any]:
    if reference.get("schema_version") != V38_METRIC_RESULT_REFERENCE_SCHEMA:
        raise ValueError("v38 metric result reference schema is invalid")
    artifact = reference.get("metric_result_artifact")
    if not isinstance(artifact, dict) or set(artifact) != {
        "sha256",
        "size_bytes",
        "uri",
        "media_type",
    }:
        raise ValueError("v38 metric result artifact reference is invalid")
    if artifact["media_type"] != "application/json":
        raise ValueError("v38 metric result artifact media type is invalid")
    raw = await asyncio.to_thread(
        lambda: ContentAddressedObjectStore().get_bytes(str(artifact["uri"]))
    )
    if len(raw) != int(artifact["size_bytes"]) or sha256_bytes(raw) != artifact["sha256"]:
        raise ValueError("v38 metric result artifact identity is invalid")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("v38 metric result artifact is not canonical JSON") from error
    if not isinstance(payload, dict) or sha256_json(payload) != reference.get(
        "metric_result_sha256"
    ):
        raise ValueError("v38 metric result payload identity is invalid")
    if artifact["sha256"] != reference["metric_result_sha256"]:
        raise ValueError("v38 metric result content hashes disagree")
    if (
        payload.get("result", {}).get("plugin", {}).get("name")
        != reference.get("plugin_name")
        or payload.get("activity_transition_receipt")
        != reference.get("activity_transition_receipt")
    ):
        raise ValueError("v38 metric compact receipt differs from payload")
    return payload


async def _resolve_v38_admission(reference: dict[str, Any]) -> dict[str, Any]:
    schema_version = reference.get("schema_version")
    if schema_version not in {
        V38_ADMISSION_REFERENCE_SCHEMA,
        V39_CROSS_ROUND_ADMISSION_REFERENCE_SCHEMA,
    }:
        raise ValueError("v38 sequence admission reference schema is invalid")
    artifact = reference.get(
        "admission_artifact"
        if schema_version == V38_ADMISSION_REFERENCE_SCHEMA
        else "artifact"
    )
    if not isinstance(artifact, dict) or set(artifact) != {
        "sha256",
        "size_bytes",
        "uri",
        "media_type",
    }:
        raise ValueError("v38 sequence admission artifact reference is invalid")
    if artifact["media_type"] != "application/json":
        raise ValueError("v38 sequence admission artifact media type is invalid")
    raw = await asyncio.to_thread(
        ContentAddressedObjectStore().get_bytes, str(artifact["uri"])
    )
    if len(raw) != int(artifact["size_bytes"]) or sha256_bytes(raw) != artifact["sha256"]:
        raise ValueError("v38 sequence admission artifact identity is invalid")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or sha256_json(payload) != reference.get(
        "admission_sha256"
    ):
        raise ValueError("v38 sequence admission payload identity is invalid")
    if artifact["sha256"] != reference["admission_sha256"]:
        raise ValueError("v38 sequence admission hashes disagree")
    return payload


def _normalized_v38_maturity_policy_payload() -> dict[str, Any]:
    policy = build_default_v38_maturity_policy()
    policy_payload = policy.model_dump(mode="json")
    # Pydantic serializes frozenset fields as lists in hash-iteration order.  That
    # order is process-randomized, so evaluate and persist activities running in
    # different worker processes could produce different evidence hashes despite
    # identical authoritative rows.  Normalize every set-backed policy field
    # before it enters a content-addressed admission artifact.
    policy_payload["required_metrics"] = sorted(policy.required_metrics)
    policy_payload["non_gating_out_of_domain_metrics"] = sorted(
        policy.non_gating_out_of_domain_metrics
    )
    for gate_payload, gate in zip(
        policy_payload["label_gates"], policy.label_gates, strict=True
    ):
        gate_payload["allowed_values"] = sorted(gate.allowed_values)
    return policy_payload


async def _build_v38_sequence_admission_payload(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    refinement_round: int,
    knowledge_context_pack_sha256: str,
    source_run_ids: tuple[uuid.UUID, ...] | None = None,
) -> tuple[dict[str, Any], set[uuid.UUID]]:
    policy = build_default_v38_maturity_policy()
    policy_payload = _normalized_v38_maturity_policy_payload()
    source_ids = source_run_ids or (run_id,)
    run_order = {source_id: ordinal for ordinal, source_id in enumerate(source_ids)}
    candidates = list(
        await session.scalars(
            select(Candidate).where(Candidate.run_id.in_(source_ids))
        )
    )
    candidates.sort(key=lambda item: (run_order[item.run_id], str(item.id)))
    unique_by_sequence: dict[str, Candidate] = {}
    for candidate in candidates:
        unique_by_sequence.setdefault(candidate.sequence_sha256, candidate)
    candidates = list(unique_by_sequence.values())
    if not candidates:
        raise ValueError("v38 sequence admission requires persisted candidates")
    evaluations = list(
        await session.scalars(
            select(Evaluation)
            .where(Evaluation.candidate_id.in_([item.id for item in candidates]))
            .order_by(Evaluation.candidate_id, Evaluation.metric_name, Evaluation.id)
        )
    )
    by_candidate: dict[uuid.UUID, list[Evaluation]] = {
        item.id: [] for item in candidates
    }
    evidence_call_ids: set[uuid.UUID] = set()
    for evaluation in evaluations:
        if evaluation.metric_name in policy.required_metrics:
            by_candidate[evaluation.candidate_id].append(evaluation)
            evidence_call_ids.add(evaluation.tool_call_id)
    provisional: list[SequenceCandidateEvidence] = []
    parent_sequences: dict[uuid.UUID, str] = {}
    for candidate in candidates:
        rows = by_candidate[candidate.id]
        names = [item.metric_name for item in rows]
        if len(rows) != len(policy.required_metrics) or set(names) != policy.required_metrics:
            raise ValueError(f"v38 candidate metric coverage is incomplete: {candidate.id}")
        if len(names) != len(set(names)):
            raise ValueError(f"v38 candidate metric evidence is duplicated: {candidate.id}")
        traces = tuple(
            KnowledgeUseTrace.model_validate(item)
            for item in candidate.metadata_json.get("knowledge_traces", [])
        )
        context_sha = candidate.metadata_json.get("cohort_sha256")
        if not isinstance(context_sha, str) or len(context_sha) != 64:
            context_sha = sha256_json(candidate.metadata_json)
        provisional.append(
            SequenceCandidateEvidence(
                candidate_id=candidate.id,
                sequence_sha256=candidate.sequence_sha256,
                parent_candidate_id=candidate.parent_id,
                generation=candidate.generation,
                observations=tuple(
                    MetricObservation(
                        metric_name=row.metric_name,
                        status=("succeeded" if row.status == "succeeded" else "failed"),
                        numeric_value=row.numeric_value,
                        text_value=row.text_value,
                        out_of_domain=row.out_of_domain,
                    )
                    for row in rows
                ),
                rank_stability=1.0,
                knowledge_traces=traces,
                proposal_context_sha256=context_sha,
            )
        )
        parent_sequences[candidate.id] = candidate.sequence
        if candidate.generator_call_id is not None:
            evidence_call_ids.add(candidate.generator_call_id)
    provisional_tuple = tuple(provisional)
    stability = compute_leave_one_objective_out_rank_stability(
        provisional_tuple, policy
    )
    evidence = tuple(
        item.model_copy(update={"rank_stability": stability[item.candidate_id]})
        for item in provisional_tuple
    )
    admission = admit_sequence_cohort(
        evidence,
        policy,
        refinement_round=refinement_round,
    )
    refinement = (
        build_sequence_refinement_plan(
            admission=admission,
            candidates=evidence,
            parent_sequences=parent_sequences,
            policy=policy,
            knowledge_context_pack_sha256=knowledge_context_pack_sha256,
        )
        if admission.refinement_required
        else None
    )
    payload = {
        "schema_version": "v38.sequence-admission-evidence.1",
        "run_id": str(run_id),
        "policy": policy_payload,
        "candidate_evidence_sha256": sha256_json(
            [item.model_dump(mode="json") for item in evidence]
        ),
        "admission": admission.model_dump(mode="json"),
        "refinement_plan": (
            refinement.model_dump(mode="json") if refinement is not None else None
        ),
    }
    if source_run_ids is not None:
        payload["source_run_ids"] = [str(item) for item in source_ids]
    payload["observer_decision_projection"] = build_candidate_decision_projection(
        payload
    )
    return payload, evidence_call_ids


def build_v38_metric_evaluation_rows(
    *,
    contract: V38SequenceExecutionContract,
    candidates: list[dict[str, Any]],
    metric_result: dict[str, Any],
) -> list[dict[str, Any]]:
    result = metric_result["result"]
    plugin = result["plugin"]
    plugin_name = str(plugin["name"])
    if plugin_name not in contract.metric_plugins:
        raise ValueError("v38 metric plugin is outside the execution contract")
    if result.get("status") != "complete":
        raise ValueError("v38 required metric plugin did not complete")
    expected_metrics = set(V38_METRIC_OBSERVATIONS[plugin_name])
    candidate_by_id = {str(item["id"]): item for item in candidates}
    if len(candidate_by_id) != len(candidates):
        raise ValueError("v38 metric candidate identities are duplicated")
    limitations = [
        f"handoff reliability: {result['contract']['reliability']}",
        f"configured trust: {result['contract']['default_trust']}",
        *result.get("limitations", []),
    ]
    rows: list[dict[str, Any]] = []
    for record in result["records"]:
        candidate = candidate_by_id.get(str(record["candidate_id"]))
        if candidate is None or candidate["sequence"] != record["sequence"]:
            raise ValueError("v38 metric candidate identity or sequence mismatch")
        if record.get("status") not in {"complete", "ok", "success"}:
            raise ValueError("v38 required metric contains a failed candidate record")
        for observation in _select_v37_declared_observations(
            record["observations"], expected_metrics
        ):
            is_instability = observation["metric_name"] == (
                "guruprasad_instability_index"
            )
            instability_ood = bool(
                record.get("raw", {}).get(
                    "guruprasad_instability_out_of_domain", False
                )
            )
            rows.append(
                {
                    "candidate_id": str(record["candidate_id"]),
                    "metric_name": observation["metric_name"],
                    "numeric_value": observation["numeric_value"],
                    "text_value": observation["text_value"],
                    "unit": observation["unit"],
                    "out_of_domain": is_instability and instability_ood,
                    "limitations": limitations,
                    "raw": {
                        "plugin": plugin,
                        "contract": result["contract"],
                        "adapter_version": result.get("adapter_version"),
                        "raw_row": record["raw"],
                    },
                }
            )
    expected_pairs = {
        (candidate_id, metric_name)
        for candidate_id in candidate_by_id
        for metric_name in expected_metrics
    }
    if {(row["candidate_id"], row["metric_name"]) for row in rows} != expected_pairs:
        raise ValueError("v38 metric plugin candidate coverage is incomplete")
    rows.sort(key=lambda item: (item["candidate_id"], item["metric_name"]))
    return rows


@activity.defn(name="persist_v38_score_all_generation")
async def persist_v38_score_all_generation(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(str(request["run_id"]))
    contract = V38SequenceExecutionContract.model_validate(request["execution_contract"])
    generated_cells = request["generated_cells"]
    if not isinstance(generated_cells, list):
        raise ValueError("v38 generated_cells must be a list")
    cohort = build_v38_score_all_cohort_from_results(contract, generated_cells)
    generated_by_identity = {
        (item["result"]["generator_id"], int(item["result"]["seed"])): item
        for item in generated_cells
    }
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        bindings: list[GeneratorCellToolBinding] = []
        for cell in contract.cells:
            generated = generated_by_identity[(cell.generator_id, cell.seed)]
            result = generated["result"]
            transition = generated.get("activity_transition_receipt")
            if not isinstance(transition, dict) or transition.get("schema_version") != (
                "v38.activity-transition-receipt.1"
            ):
                raise ValueError("v38 generator result lacks its transition receipt")
            weights = generated.get("weights_sha256")
            weights_sha256 = weights if isinstance(weights, str) else sha256_json(weights)
            call = await repository.record_completed_tool_call(
                run_id,
                f"v38-generate-{cell.generator_id}",
                str(result["adapter_version"]),
                str(generated["environment_sha256"]),
                {
                    "stage": "v38_score_all_generation",
                    "cell_ordinal": cell.ordinal,
                    "generator_id": cell.generator_id,
                    "seed": cell.seed,
                    "execution_contract_sha256": contract.sha256(),
                },
                {
                    "raw_proposal_budget": cell.requested_proposals,
                    "score_all_valid_unique_proposals": True,
                    "first_k_retention_forbidden": True,
                },
                result,
                weights_sha256=weights_sha256,
                random_seed=cell.seed,
                attempt=int(generated["attempt"]),
                logical_stage="generation",
                display_category="design",
            )
            artifact = await _store_json(
                {
                    "result": result,
                    "runtime_identity": generated["runtime_identity"],
                    "stdout_tail": generated["stdout_tail"],
                    "live_launch_receipt": generated["launch_receipt"],
                    "materialization_receipt": generated.get("materialization_receipt"),
                    "activity_transition_receipt": transition,
                }
            )
            await _register_artifact(
                session,
                call.id,
                asdict(artifact),
                "v38_raw_generator_output",
                {
                    "cell_ordinal": cell.ordinal,
                    "generator_id": cell.generator_id,
                    "seed": cell.seed,
                },
            )
            bindings.append(
                GeneratorCellToolBinding(
                    cell_ordinal=cell.ordinal,
                    generator_id=cell.generator_id,
                    seed=cell.seed,
                    tool_call_id=call.id,
                    opaque_arm_label=f"v38-generator-cell-{cell.ordinal}",
                )
            )
        receipt = await persist_score_all_proposal_cohort(
            session,
            run_id=run_id,
            contract=contract,
            cohort=cohort,
            bindings=tuple(bindings),
        )
        cohort_artifact = await _store_json(cohort.model_dump(mode="json"))
        for binding in bindings:
            await _register_artifact(
                session,
                binding.tool_call_id,
                asdict(cohort_artifact),
                "v38_score_all_cohort",
                {
                    "cohort_sha256": cohort.sha256(),
                    "execution_contract_sha256": contract.sha256(),
                },
            )
        persisted_candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == run_id)
                .order_by(Candidate.proposal_rank, Candidate.id)
            )
        )
        if len(persisted_candidates) != cohort.promoted_unique_count:
            raise ValueError("v38 score-all candidate result count drifted")
    return {
        "persistence_receipt": receipt.model_dump(mode="json"),
        "score_all_cohort": cohort.model_dump(mode="json"),
        "candidate_count": cohort.promoted_unique_count,
        "candidates": [
            {
                "id": str(candidate.id),
                "sequence": candidate.sequence,
                "sequence_sha256": candidate.sequence_sha256,
                "proposal_rank": candidate.proposal_rank,
            }
            for candidate in persisted_candidates
        ],
    }


@activity.defn(name="persist_v38_sequence_metric")
async def persist_v38_sequence_metric(request: dict[str, Any]) -> dict[str, Any]:
    _heartbeat_metric_persistence("hydrate_metric_persistence")
    run_id = uuid.UUID(str(request["run_id"]))
    if bool(request.get("hydrate_from_run_spec")):
        candidate_ids = [uuid.UUID(str(item)) for item in request.get("candidate_ids") or []]
        if not candidate_ids or len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("v38 thin metric persistence candidate cohort is incomplete")
        async with SessionFactory() as hydration_session:
            run = await hydration_session.get(ExperimentRun, run_id)
            if run is None:
                raise ValueError("v38 thin metric persistence run is missing")
            spec = run.spec_json if isinstance(run.spec_json, dict) else {}
            workflow_request = spec.get("workflow_request")
            if not isinstance(workflow_request, dict):
                raise ValueError("v38 thin metric persistence lacks workflow request")
            rows = list(
                await hydration_session.scalars(
                    select(Candidate).where(Candidate.id.in_(candidate_ids))
                )
            )
        by_id = {item.id: item for item in rows}
        if set(by_id) != set(candidate_ids) or any(item.run_id != run_id for item in rows):
            raise ValueError("v38 thin metric persistence cohort differs from database")
        request = {
            **request,
            "execution_contract": workflow_request["execution_contract"],
            "candidates": [
                {
                    "id": str(by_id[candidate_id].id),
                    "sequence": by_id[candidate_id].sequence,
                    "sequence_sha256": by_id[candidate_id].sequence_sha256,
                    "generation": by_id[candidate_id].generation,
                }
                for candidate_id in candidate_ids
            ],
        }
    contract = V38SequenceExecutionContract.model_validate(request["execution_contract"])
    candidates = request["candidates"]
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("v38 metric persistence requires a non-empty candidate cohort")
    reference = request["metric_result"]
    _heartbeat_metric_persistence("resolve_metric_result")
    metric_result = await _resolve_v38_metric_result(reference)
    rows = build_v38_metric_evaluation_rows(
        contract=contract,
        candidates=candidates,
        metric_result=metric_result,
    )
    result = metric_result["result"]
    provenance = metric_result["provenance"]
    plugin = result["plugin"]
    plugin_name = str(plugin["name"])
    _heartbeat_metric_persistence("persist_metric_rows")
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        run_candidates = {
            str(item.id): item
            for item in await session.scalars(
                select(Candidate).where(Candidate.run_id == run_id)
            )
        }
        expected_candidate_ids = {str(item["id"]) for item in candidates}
        if not expected_candidate_ids <= set(run_candidates):
            raise ValueError("v38 metric persistence candidate cohort differs from database")
        db_candidates = {
            candidate_id: run_candidates[candidate_id]
            for candidate_id in expected_candidate_ids
        }
        call = await repository.record_completed_tool_call(
            run_id,
            f"v38-metric-{plugin_name}",
            str(provenance["tool_version"]),
            str(provenance["environment_sha256"]),
            {
                "stage": "v38_sequence_metrics",
                "plugin": plugin_name,
                "candidate_ids": sorted(expected_candidate_ids),
                "execution_contract_sha256": contract.sha256(),
            },
            {
                "plugin": plugin,
                "declared_observations": sorted(V38_METRIC_OBSERVATIONS[plugin_name]),
                "score_all_candidate_count": len(candidates),
            },
            reference,
            weights_sha256=provenance.get("weights_sha256"),
            model_uri=provenance.get("model_uri"),
            attempt=int(provenance["attempt"]),
            logical_stage="sequence_metrics",
            display_category="evaluation",
        )
        await _register_artifact(
            session,
            call.id,
            reference["metric_result_artifact"],
            "v38_metric_result",
            {
                "plugin": plugin_name,
                "metric_result_sha256": reference["metric_result_sha256"],
            },
        )
        for role, artifact_key in (
            ("v38_metric_raw_output", "raw_output_artifact"),
            ("v38_metric_environment", "environment_artifact"),
        ):
            stored = provenance.get(artifact_key)
            if not isinstance(stored, dict):
                raise ValueError(f"v38 metric provenance lacks {artifact_key}")
            await _register_artifact(
                session,
                call.id,
                stored,
                role,
                {"plugin": plugin_name},
            )
        await repository.record_evaluations_bulk(
            call.id,
            [
                {
                    **row,
                    "candidate_id": db_candidates[row["candidate_id"]].id,
                }
                for row in rows
            ],
        )
        generator_call_ids = {
            candidate.generator_call_id for candidate in db_candidates.values()
        }
        for parent_id in sorted(generator_call_ids, key=str):
            if parent_id is not None:
                await repository.record_tool_dependency(
                    call.id,
                    parent_id,
                    "evaluates_v38_score_all_candidate",
                )
        await repository.append_event(
            "run",
            run_id,
            "v38.sequence_metric.persisted",
            "v38-sequence-metrics",
            {
                "plugin": plugin_name,
                "tool_call_id": str(call.id),
                "evaluation_count": len(rows),
                "candidate_count": len(candidates),
                "metric_result_sha256": reference["metric_result_sha256"],
            },
        )
    return {
        "plugin": plugin_name,
        "evaluation_count": len(rows),
        "tool_call_id": str(call.id),
    }


@activity.defn(name="persist_v38_refinement_children")
async def persist_v38_refinement_children(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(str(request["run_id"]))
    plan = SequenceRefinementPlan.model_validate(request["refinement_plan"])
    result = request["refinement_result"]
    if not isinstance(result, dict):
        raise ValueError("v38 refinement result is invalid")
    proposals = validate_v38_refinement_result(plan, result)
    provenance = result.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("v38 refinement result lacks provenance")
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        parents = {
            item.id: item
            for item in await session.scalars(
                select(Candidate).where(
                    Candidate.run_id == run_id,
                    Candidate.id.in_([task.parent_candidate_id for task in plan.tasks]),
                )
            )
        }
        if set(parents) != {task.parent_candidate_id for task in plan.tasks}:
            raise ValueError("v38 refinement parent cohort differs from database")
        for task in plan.tasks:
            parent = parents[task.parent_candidate_id]
            if (
                parent.sequence != task.parent_sequence
                or parent.sequence_sha256 != task.parent_sequence_sha256
            ):
                raise ValueError("v38 refinement parent sequence identity drifted")
        call = await repository.record_completed_tool_call(
            run_id,
            "v38-knowledge-traced-refinement",
            str(provenance["tool_version"]),
            str(provenance["environment_sha256"]),
            {
                "stage": "v38_sequence_refinement",
                "refinement_plan_sha256": plan.sha256(),
                "knowledge_context_pack_sha256": plan.tasks[0].knowledge_context_pack_sha256,
            },
            {
                "refinement_round": plan.refinement_round,
                "parent_controls_retained": True,
                "full_rescoring_required": True,
                "structure_dispatch_forbidden_until_readmission": True,
            },
            result,
            weights_sha256=provenance.get("weights_sha256"),
            model_uri=provenance.get("model_uri"),
            attempt=int(provenance["attempt"]),
            logical_stage="refinement",
            display_category="design",
        )
        artifact = await _store_json(
            {
                "refinement_plan": plan.model_dump(mode="json"),
                "refinement_result": result,
            }
        )
        await _register_artifact(
            session,
            call.id,
            asdict(artifact),
            "v38_refinement_children_and_parent_controls",
            {
                "refinement_plan_sha256": plan.sha256(),
                "refinement_round": plan.refinement_round,
            },
        )
        for parent in parents.values():
            if parent.generator_call_id is not None:
                await repository.record_tool_dependency(
                    call.id,
                    parent.generator_call_id,
                    "refines_v38_parent_candidate",
                )
        maximum_rank = int(
            await session.scalar(
                select(func.coalesce(func.max(Candidate.proposal_rank), 0)).where(
                    Candidate.run_id == run_id
                )
            )
            or 0
        )
        existing_by_sha = {
            item.sequence_sha256: item
            for item in await session.scalars(
                select(Candidate).where(Candidate.run_id == run_id)
            )
        }
        children: list[Candidate] = []
        duplicate_count = 0
        observed_knowledge_reads: set[tuple[str, str, str]] = set()
        for ordinal, proposal in enumerate(proposals, start=1):
            sequence = "".join(proposal.child_sequence.split()).upper()
            sequence_sha256 = sha256_text(sequence)
            candidate = existing_by_sha.get(sequence_sha256)
            disposition = "duplicate" if candidate is not None else "promoted_for_scoring"
            if candidate is None:
                candidate = await repository.add_candidate(
                    run_id=run_id,
                    sequence=sequence,
                    generation=plan.refinement_round,
                    proposal_rank=maximum_rank + ordinal,
                    generator_call_id=call.id,
                    parent_id=proposal.parent_candidate_id,
                    metadata={
                        "schema_version": "v38.refinement-child.1",
                        "refinement_plan_sha256": plan.sha256(),
                        "mutation_rationale": proposal.mutation_rationale,
                        "knowledge_traces": [
                            trace.model_dump(mode="json")
                            for trace in proposal.knowledge_traces
                        ],
                        "unchanged_parent_control_sha256": (
                            proposal.unchanged_parent_control_sha256
                        ),
                        "score_all_sequence_metrics_required": True,
                    },
                    actor="v38-knowledge-traced-refinement",
                )
                existing_by_sha[sequence_sha256] = candidate
                children.append(candidate)
            else:
                duplicate_count += 1
            await repository.record_candidate_occurrence(
                run_id=run_id,
                tool_call_id=call.id,
                parent_candidate_id=proposal.parent_candidate_id,
                occurrence_rank=ordinal,
                occurrence_kind="refinement",
                opaque_arm_label=f"v38-refinement-round-{plan.refinement_round}",
                sequence=sequence,
                candidate_id=candidate.id,
                metadata={
                    "schema_version": "v38.refinement-occurrence.1",
                    "refinement_plan_sha256": plan.sha256(),
                    "disposition": disposition,
                    "mutation_rationale": proposal.mutation_rationale,
                    "knowledge_traces": [
                        trace.model_dump(mode="json") for trace in proposal.knowledge_traces
                    ],
                    "unchanged_parent_control_sha256": (
                        proposal.unchanged_parent_control_sha256
                    ),
                },
            )
            for trace in proposal.knowledge_traces:
                identity = (
                    trace.card_id,
                    trace.passage_sha256,
                    trace.decision,
                )
                if identity in observed_knowledge_reads:
                    continue
                observed_knowledge_reads.add(identity)
                await append_typed_lifecycle_event(
                    session,
                    KnowledgeCardReadPayload(
                        run_id=run_id,
                        card_key=trace.card_id,
                        card_version=str(provenance["tool_version"]),
                        content_sha256=trace.passage_sha256,
                        content_kind="passage_evidence",
                        source_uri=(
                            f"provider-task://{trace.provider_task_id}/cards/"
                            f"{trace.card_id}/passages/{trace.passage_sha256}"
                        ),
                        read_at=datetime.now(UTC),
                        status=("adopted" if trace.decision == "adopt" else "rejected"),
                    ),
                )
        await repository.append_event(
            "run",
            run_id,
            "v38.sequence_refinement.persisted",
            "v38-knowledge-traced-refinement",
            {
                "tool_call_id": str(call.id),
                "refinement_round": plan.refinement_round,
                "raw_child_occurrence_count": len(proposals),
                "promoted_unique_child_count": len(children),
                "duplicate_child_count": duplicate_count,
                "full_rescoring_required": True,
            },
        )
    return {
        "tool_call_id": str(call.id),
        "raw_child_occurrence_count": len(proposals),
        "promoted_unique_child_count": len(children),
        "duplicate_child_count": duplicate_count,
        "candidates": [
            {
                "id": str(candidate.id),
                "sequence": candidate.sequence,
                "sequence_sha256": candidate.sequence_sha256,
                "proposal_rank": candidate.proposal_rank,
                "parent_candidate_id": str(candidate.parent_id),
            }
            for candidate in children
        ],
        "full_rescoring_required": True,
    }


@activity.defn(name="evaluate_v38_sequence_admission")
async def evaluate_v38_sequence_admission(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(str(request["run_id"]))
    refinement_round = int(request.get("refinement_round", 0))
    context_sha = str(request["knowledge_context_pack_sha256"])
    if len(context_sha) != 64:
        raise ValueError("v38 knowledge context-pack SHA is invalid")
    async with SessionFactory() as session:
        payload, _ = await _build_v38_sequence_admission_payload(
            session=session,
            run_id=run_id,
            refinement_round=refinement_round,
            knowledge_context_pack_sha256=context_sha,
        )
    artifact = await _store_json(payload)
    admission = payload["admission"]
    return {
        "schema_version": V38_ADMISSION_REFERENCE_SCHEMA,
        "admission_sha256": sha256_json(payload),
        "admission_artifact": asdict(artifact),
        "refinement_round": refinement_round,
        "mature_core_count": len(admission["mature_core_candidate_ids"]),
        "exploration_count": len(admission["exploration_candidate_ids"]),
        "rejected_count": len(admission["rejected_candidate_ids"]),
        "refinement_required": admission["refinement_required"],
        "structure_dispatch_allowed": admission["structure_dispatch_allowed"],
        "refinement_plan": payload["refinement_plan"],
    }


@activity.defn(name="persist_v39_cross_round_admission")
async def persist_v39_cross_round_admission(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Recompute one global Pareto/admission view over four frozen rounds."""

    controller_run_id = uuid.UUID(str(request["controller_run_id"]))
    source_run_ids = tuple(uuid.UUID(str(item)) for item in request["round_run_ids"])
    if len(source_run_ids) != 4 or len(set(source_run_ids)) != 4:
        raise ValueError("v39 cross-round admission requires four unique runs")
    context_sha = str(request["knowledge_context_pack_sha256"])
    if len(context_sha) != 64:
        raise ValueError("v39 knowledge context-pack SHA is invalid")

    async with SessionFactory() as session:
        controller = await session.get(ExperimentRun, controller_run_id)
        children = list(
            await session.scalars(
                select(ExperimentRun).where(ExperimentRun.id.in_(source_run_ids))
            )
        )
        if controller is None or len(children) != 4 or any(
            item.parent_run_id != controller_run_id for item in children
        ):
            raise ValueError("v39 source runs are not frozen controller children")
        payload, _ = await _build_v38_sequence_admission_payload(
            session=session,
            run_id=controller_run_id,
            refinement_round=3,
            knowledge_context_pack_sha256=context_sha,
            source_run_ids=source_run_ids,
        )
    payload.update(
        {
            "schema_version": "v39.cross-round-admission-evidence.1",
            "exploration_contract_sha256": request[
                "exploration_contract_sha256"
            ],
            "schedule_sha256": request["schedule_sha256"],
            "historical_outputs_reused": False,
            "cross_round_exact_sequence_deduplication": True,
        }
    )
    artifact = await _store_json(payload)
    admission_sha256 = sha256_json(payload)
    response = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    async with SessionFactory() as session, session.begin():
        existing = await session.scalar(
            select(AgentDecision).where(
                AgentDecision.run_id == controller_run_id,
                AgentDecision.generation == 4,
                AgentDecision.decision_type == "v39_cross_round_admission",
            )
        )
        if existing is not None:
            if existing.structured_json != payload:
                raise ValueError("existing v39 cross-round admission drifted")
        else:
            existing = await ExperimentRepository(session).record_agent_decision(
                controller_run_id,
                4,
                "v39_cross_round_admission",
                "deterministic-v39-sequence-space-agent",
                str(request["worker_source_revision"]),
                (
                    "Recompute validity, safety and nonweighted Pareto admission over "
                    "all exact-sequence-deduplicated round evidence."
                ),
                response,
                payload,
                model_name="deterministic://v39-cross-round-admission",
            )
        event_payload = {
            "schema_version": "v39.cross-round-admission-receipt.1",
            "decision_id": str(existing.id),
            "admission_sha256": admission_sha256,
            "artifact": asdict(artifact),
            "source_run_ids": [str(item) for item in source_run_ids],
        }
        receipt_sha256 = sha256_json(event_payload)
        prior_event = await session.scalar(
            select(LifecycleEvent).where(
                LifecycleEvent.aggregate_type == "run",
                LifecycleEvent.aggregate_id == controller_run_id,
                LifecycleEvent.event_type == "v39.cross_round_admission_persisted",
                LifecycleEvent.payload_sha256 == receipt_sha256,
            )
        )
        if prior_event is None:
            await ExperimentRepository(session).append_event(
                "run",
                controller_run_id,
                "v39.cross_round_admission_persisted",
                "v39-sequence-space-controller",
                event_payload,
            )
    admission = payload["admission"]
    return {
        "schema_version": "v39.cross-round-admission-reference.1",
        "admission_sha256": admission_sha256,
        "artifact": asdict(artifact),
        "mature_core_count": len(admission["mature_core_candidate_ids"]),
        "exploration_count": len(admission["exploration_candidate_ids"]),
        "rejected_count": len(admission["rejected_candidate_ids"]),
        "structure_dispatch_allowed": admission["structure_dispatch_allowed"],
        "source_run_ids": [str(item) for item in source_run_ids],
    }


@activity.defn(name="persist_v38_sequence_admission")
async def persist_v38_sequence_admission(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(str(request["run_id"]))
    reference = request["admission_reference"]
    payload = await _resolve_v38_admission(reference)
    if payload.get("run_id") != str(run_id):
        raise ValueError("v38 sequence admission reference belongs to another run")
    refinement_round = int(reference["refinement_round"])
    context_sha = str(request["knowledge_context_pack_sha256"])
    environment_sha256 = str(request["environment_sha256"])
    worker_source_revision = str(request["worker_source_revision"])
    if len(context_sha) != 64 or len(environment_sha256) != 64:
        raise ValueError("v38 sequence admission persistence identity is invalid")
    async with SessionFactory() as verify_session:
        recomputed, _ = await _build_v38_sequence_admission_payload(
            session=verify_session,
            run_id=run_id,
            refinement_round=refinement_round,
            knowledge_context_pack_sha256=context_sha,
        )
    if sha256_json(recomputed) != reference["admission_sha256"] or recomputed != payload:
        raise ValueError("v38 sequence admission differs from authoritative database evidence")

    async with SessionFactory() as session, session.begin():
        _, evidence_call_ids = await _build_v38_sequence_admission_payload(
            session=session,
            run_id=run_id,
            refinement_round=refinement_round,
            knowledge_context_pack_sha256=context_sha,
        )
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            "v38-sequence-maturity-admission",
            worker_source_revision,
            environment_sha256,
            {
                "stage": "v38_sequence_admission",
                "candidate_evidence_sha256": payload["candidate_evidence_sha256"],
                "knowledge_context_pack_sha256": context_sha,
                "refinement_round": refinement_round,
            },
            {
                "nonweighted_pareto": True,
                "absolute_mic_threshold_forbidden": True,
                "fixed_mic_agreement_cutoff_forbidden": True,
                "full_sequence_panel_required": True,
            },
            reference,
            model_uri="deterministic://v38-sequence-maturity-admission",
            attempt=activity.info().attempt,
            logical_stage="admission",
            display_category="decision",
        )
        for parent_id in sorted(evidence_call_ids, key=str):
            await repository.record_tool_dependency(
                call.id,
                parent_id,
                "v38_admission_uses_sequence_evidence",
            )
        artifact_row = await _register_artifact(
            session,
            call.id,
            reference["admission_artifact"],
            "v38_sequence_admission_evidence",
            {
                "admission_sha256": reference["admission_sha256"],
                "refinement_round": refinement_round,
            },
        )
        existing_decisions = list(
            await session.scalars(
                select(AgentDecision).where(
                    AgentDecision.run_id == run_id,
                    AgentDecision.generation == refinement_round,
                    AgentDecision.decision_type == "v38_sequence_maturity_admission",
                )
            )
        )
        if len(existing_decisions) > 1:
            raise ValueError("duplicate v38 sequence admission decisions detected")
        response = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if existing_decisions:
            decision = existing_decisions[0]
            if decision.structured_json != payload or decision.response_text != response:
                raise ValueError("existing v38 sequence admission decision drifted")
        else:
            decision = await repository.record_agent_decision(
                run_id,
                refinement_round,
                "v38_sequence_maturity_admission",
                "deterministic-v38-sequence-first-agent",
                worker_source_revision,
                (
                    "Apply the frozen validity and safety gates, independent MIC/activity and "
                    "developability Pareto axes, rank-stability check, and bounded knowledge "
                    "refinement policy to every persisted valid unique sequence."
                ),
                response,
                payload,
                model_name="deterministic://v38-sequence-first-agent",
                response_artifact_id=artifact_row.id,
            )
        for parent_id in sorted(evidence_call_ids, key=str):
            await repository.record_agent_tool_edge(
                decision.id,
                parent_id,
                "input",
                "observes_complete_sequence_evidence",
            )
        await repository.record_agent_tool_edge(
            decision.id,
            call.id,
            "output",
            "materializes_sequence_admission",
        )
    return {
        "tool_call_id": str(call.id),
        "decision_id": str(decision.id),
        "admission_sha256": reference["admission_sha256"],
        "mature_core_count": reference["mature_core_count"],
        "exploration_count": reference["exploration_count"],
        "rejected_count": reference["rejected_count"],
        "refinement_required": reference["refinement_required"],
        "structure_dispatch_allowed": reference["structure_dispatch_allowed"],
    }


async def _build_v38_final_portfolio_payload(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    admission_payload: dict[str, Any],
    expected_seeds: tuple[int, ...],
    decoys_per_seed: int,
) -> tuple[dict[str, Any], set[uuid.UUID]]:
    admission = SequenceCohortAdmission.model_validate(admission_payload["admission"])
    admitted_ids = (
        *admission.mature_core_candidate_ids,
        *admission.exploration_candidate_ids,
    )
    if not admitted_ids or admission.refinement_required:
        raise ValueError("v38 final portfolio requires a concluded admitted cohort")
    decisions = {item.candidate_id: item for item in admission.decisions}
    sequence_fronts: dict[uuid.UUID, int | None] = {}
    for candidate_id in admitted_ids:
        decision = decisions.get(candidate_id)
        if decision is None:
            raise ValueError("v38 admitted candidate lacks its sequence decision")
        if (
            candidate_id in admission.mature_core_candidate_ids
            and decision.pareto_front is None
        ):
            raise ValueError("v38 mature-core candidate lacks its sequence Pareto front")
        sequence_fronts[candidate_id] = decision.pareto_front

    branches = list(
        await session.scalars(
            select(ExperimentRunTargetBranch)
            .where(ExperimentRunTargetBranch.run_id == run_id)
            .order_by(ExperimentRunTargetBranch.branch_order)
        )
    )
    if not branches:
        raise ValueError("v38 final portfolio requires frozen target branches")
    target_key_by_id = {item.target_id: item.branch_key for item in branches}
    rows = list(
        await session.scalars(
            select(MultiTargetStructureEvidenceRecord)
            .where(MultiTargetStructureEvidenceRecord.run_id == run_id)
            .order_by(
                MultiTargetStructureEvidenceRecord.candidate_id,
                MultiTargetStructureEvidenceRecord.target_id,
                MultiTargetStructureEvidenceRecord.control_lane,
                MultiTargetStructureEvidenceRecord.boltz_seed,
                MultiTargetStructureEvidenceRecord.evidence_kind,
                MultiTargetStructureEvidenceRecord.decoy_ordinal,
            )
        )
    )
    expected_tasks = len(admitted_ids) * len(branches) * 2 * len(expected_seeds)
    poses = [item for item in rows if item.evidence_kind == "boltz_pose"]
    decoys = [item for item in rows if item.evidence_kind == "rosetta_decoy"]
    if len(poses) != expected_tasks or len(decoys) != expected_tasks * decoys_per_seed:
        raise ValueError("v38 final portfolio structure evidence cardinality is incomplete")
    pose_tasks = {item.task_sha256 for item in poses}
    if len(pose_tasks) != expected_tasks or any(
        item.task_sha256 not in pose_tasks for item in decoys
    ):
        raise ValueError("v38 final portfolio has an incomplete Boltz/Rosetta task graph")
    evidence = tuple(
        StructureScoreEvidence(
            candidate_id=item.candidate_id,
            target_key=target_key_by_id[item.target_id],
            control_lane=item.control_lane,
            boltz_seed=item.boltz_seed,
            decoy_ordinal=item.decoy_ordinal,
            total_score=float(item.metadata_json["total_score"]),
        )
        for item in decoys
    )
    portfolio = build_v38_final_portfolio(
        sequence_pareto_fronts=sequence_fronts,
        evidence=evidence,
        target_keys=tuple(item.branch_key for item in branches),
        expected_seeds=expected_seeds,
        decoys_per_seed=decoys_per_seed,
    )
    evidence_snapshot = [
        {
            "task_sha256": item.task_sha256,
            "kind": item.evidence_kind,
            "ordinal": item.decoy_ordinal,
            "input_sha256": item.input_artifact_sha256,
            "output_sha256": item.output_artifact_sha256,
            "score_sha256": item.score_artifact_sha256,
        }
        for item in rows
    ]
    payload = {
        "schema_version": "v38.final-portfolio-replay.1",
        "run_id": str(run_id),
        "admission_sha256": sha256_json(admission_payload),
        "structure_evidence_snapshot_sha256": sha256_json(evidence_snapshot),
        "structure_evidence_record_count": len(rows),
        "portfolio": portfolio.model_dump(mode="json"),
        "replay_verified": True,
    }
    return payload, {item.tool_call_id for item in rows}


@activity.defn(name="persist_v38_final_portfolio_replay")
async def persist_v38_final_portfolio_replay(
    request: dict[str, Any],
) -> dict[str, Any]:
    run_id = uuid.UUID(str(request["run_id"]))
    admission_payload = await _resolve_v38_admission(request["admission_reference"])
    if admission_payload.get("run_id") != str(run_id):
        raise ValueError("v38 final portfolio admission belongs to another run")
    expected_seeds = tuple(int(item) for item in request["boltz_seeds"])
    decoys_per_seed = int(request["rosetta_decoys_per_pose"])
    environment_sha256 = str(request["environment_sha256"])
    worker_source_revision = str(request["worker_source_revision"])
    async with SessionFactory() as verify_session:
        payload, _ = await _build_v38_final_portfolio_payload(
            session=verify_session,
            run_id=run_id,
            admission_payload=admission_payload,
            expected_seeds=expected_seeds,
            decoys_per_seed=decoys_per_seed,
        )
    artifact = await _store_json(payload)
    reference = {
        "schema_version": "v38.final-portfolio-reference.1",
        "portfolio_sha256": sha256_json(payload),
        "portfolio_artifact": asdict(artifact),
        "replay_verified": True,
    }
    async with SessionFactory() as session, session.begin():
        recomputed, evidence_call_ids = await _build_v38_final_portfolio_payload(
            session=session,
            run_id=run_id,
            admission_payload=admission_payload,
            expected_seeds=expected_seeds,
            decoys_per_seed=decoys_per_seed,
        )
        if recomputed != payload or sha256_json(recomputed) != reference["portfolio_sha256"]:
            raise ValueError("v38 final portfolio replay drifted before persistence")
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            "v38-final-multiview-portfolio-replay",
            worker_source_revision,
            environment_sha256,
            {
                "admission_sha256": payload["admission_sha256"],
                "structure_evidence_snapshot_sha256": payload[
                    "structure_evidence_snapshot_sha256"
                ],
            },
            {
                "weighted_total_used": False,
                "target_agnostic_view": True,
                "per_target_view": True,
                "cross_target_view": True,
            },
            reference,
            model_uri="deterministic://v38-final-multiview-portfolio-replay",
            attempt=activity.info().attempt,
            logical_stage="final_portfolio",
            display_category="decision",
        )
        for parent_id in sorted(evidence_call_ids, key=str):
            await repository.record_tool_dependency(
                call.id, parent_id, "v38_final_portfolio_uses_structure_evidence"
            )
        artifact_row = await _register_artifact(
            session,
            call.id,
            reference["portfolio_artifact"],
            "v38_final_portfolio_and_replay",
            {
                "portfolio_sha256": reference["portfolio_sha256"],
                "replay_verified": True,
            },
        )
        response = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        existing = list(
            await session.scalars(
                select(AgentDecision).where(
                    AgentDecision.run_id == run_id,
                    AgentDecision.decision_type == "v38_final_multiview_portfolio",
                )
            )
        )
        if len(existing) > 1:
            raise ValueError("duplicate v38 final portfolio decisions detected")
        if existing:
            decision = existing[0]
            if decision.structured_json != payload or decision.response_text != response:
                raise ValueError("existing v38 final portfolio decision drifted")
        else:
            decision = await repository.record_agent_decision(
                run_id,
                int(admission_payload["admission"]["refinement_round"]),
                "v38_final_multiview_portfolio",
                "deterministic-v38-multitarget-agent",
                worker_source_revision,
                (
                    "Preserve the admitted sequence Pareto result, calculate independent "
                    "native-pocket and wrong-pocket structural contrasts per target, and "
                    "derive an unweighted cross-target Pareto view with full replay."
                ),
                response,
                payload,
                model_name="deterministic://v38-multitarget-agent",
                response_artifact_id=artifact_row.id,
            )
        for parent_id in sorted(evidence_call_ids, key=str):
            await repository.record_agent_tool_edge(
                decision.id, parent_id, "input", "observes_structure_evidence"
            )
        await repository.record_agent_tool_edge(
            decision.id, call.id, "output", "materializes_final_portfolio_replay"
        )
    portfolio = payload["portfolio"]
    return {
        "tool_call_id": str(call.id),
        "decision_id": str(decision.id),
        "portfolio_sha256": reference["portfolio_sha256"],
        "replay_verified": True,
        "target_agnostic_front_one_count": len(
            portfolio["target_agnostic_front_one_candidate_ids"]
        ),
        "cross_target_front_one_count": len(
            portfolio["cross_target_front_one_candidate_ids"]
        ),
        "per_target_front_one_count": {
            key: len(value)
            for key, value in portfolio["per_target_front_one_candidate_ids"].items()
        },
    }
