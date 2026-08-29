from __future__ import annotations

import argparse
import asyncio
import copy
import json
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from pepagent.autoresearch_structure_cohort import TARGET_KEYS
from pepagent.db.models import (
    Artifact,
    Candidate,
    Evaluation,
    EvidenceArtifact,
    EvidenceArtifactLocation,
    ExperimentRun,
    LifecycleEvent,
    Target,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.domain.enums import CandidateStatus, RunStatus
from pepagent.provenance.hashing import sha256_json
from pepagent.settings import get_settings
from pepagent.structure_v2_binding import (
    PREFLIGHT_RECOVERY_REASON,
    PREFLIGHT_RECOVERY_SCHEMA,
    STRUCTURE_COHORT_IMPORT_TOOL,
    STRUCTURE_COHORT_IMPORT_VERSION,
)
from pepagent.structure_v2_reservation import (
    GLOBAL_LOCK_ID,
    BranchPlan,
    _link_source_artifacts,
    _structure_filter,
    _validate_branch_binding,
)
from pepagent.workflows.structure_v2 import (
    STRUCTURE_V2_PERSIST_QUEUE,
    STRUCTURE_V2_RECEIPT_GOAL,
    STRUCTURE_V2_ROSETTA_QUEUE,
    STRUCTURE_V2_WORKFLOW_QUEUE,
    STRUCTURE_V2_WORKFLOW_TASK_TIMEOUT,
    structure_v2_receipt_contract,
)

RECOVERY_RESERVATION_SCHEMA = "ampgent.structure-v2-preflight-recovery-reservation.1"
RECOVERY_RECEIPT_SCHEMA = "ampgent.structure-v2-preflight-recovery-receipt.1"
RECOVERY_ACTOR = "structure-v2-preflight-recovery-reservation"
RECOVERY_RUN_NAMESPACE = uuid.UUID("791fb01a-44bf-4ccd-889c-4c90ef186490")
RECOVERY_WORKFLOW_NAMESPACE = uuid.UUID("c89ade31-a384-446b-bc22-b9869e94dbd0")


@dataclass(frozen=True)
class RecoveryBranch:
    target_key: str
    predecessor: ExperimentRun
    predecessor_candidates: tuple[Candidate, ...]
    predecessor_import_call: ToolCall
    run_id: uuid.UUID
    workflow_id: str
    formal_submission_key: str
    run_spec: dict[str, Any]


def preflight_recovery_reservation_key(
    predecessor_reservation_key: str,
    predecessor_runs: Sequence[ExperimentRun],
    candidates: Sequence[Candidate],
) -> str:
    run_ids = {run.id for run in predecessor_runs}
    if len(predecessor_runs) != len(TARGET_KEYS) or len(run_ids) != len(TARGET_KEYS):
        raise ValueError("structure v2 recovery requires six distinct predecessor runs")
    by_run: dict[uuid.UUID, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.run_id not in run_ids:
            raise ValueError("structure v2 recovery candidate crossed predecessor runs")
        by_run[candidate.run_id].append(candidate)
    branches = []
    for run in predecessor_runs:
        target_key = str(run.spec_json.get("target_key", ""))
        rows = sorted(by_run[run.id], key=lambda item: (item.proposal_rank, str(item.id)))
        if target_key not in TARGET_KEYS or len(rows) != STRUCTURE_V2_RECEIPT_GOAL:
            raise ValueError("structure v2 recovery predecessor cohort differs")
        branches.append(
            {
                "target_key": target_key,
                "predecessor_run_id": str(run.id),
                "predecessor_workflow_id": run.temporal_workflow_id,
                "predecessor_temporal_run_id": run.temporal_run_id,
                "candidate_sequence_sha256s": [row.sequence_sha256 for row in rows],
                "candidate_family_keys": [
                    str(row.metadata_json.get("family_key_80_80", "")) for row in rows
                ],
            }
        )
    return sha256_json(
        {
            "schema_version": RECOVERY_RESERVATION_SCHEMA,
            "predecessor_reservation_key": predecessor_reservation_key,
            "reason": PREFLIGHT_RECOVERY_REASON,
            "scientific_output_reused": False,
            "branches": sorted(branches, key=lambda item: item["target_key"]),
        }
    )


def _recovery_contract(predecessor: ExperimentRun) -> dict[str, Any]:
    return {
        "schema_version": PREFLIGHT_RECOVERY_SCHEMA,
        "predecessor_reservation_key": predecessor.spec_json[
            "structure_v2_reservation_key"
        ],
        "predecessor_run_id": str(predecessor.id),
        "predecessor_workflow_id": predecessor.temporal_workflow_id,
        "predecessor_temporal_run_id": predecessor.temporal_run_id,
        "reason": PREFLIGHT_RECOVERY_REASON,
        "scientific_output_reused": False,
    }


def _recovery_branch(
    *,
    recovery_key: str,
    predecessor: ExperimentRun,
    predecessor_candidates: Sequence[Candidate],
    predecessor_import_call: ToolCall,
) -> RecoveryBranch:
    target_key = str(predecessor.spec_json.get("target_key", ""))
    ordered = tuple(
        sorted(predecessor_candidates, key=lambda item: (item.proposal_rank, str(item.id)))
    )
    identity = {
        "schema_version": RECOVERY_RESERVATION_SCHEMA,
        "recovery_reservation_key": recovery_key,
        "target_key": target_key,
        "predecessor_run_id": str(predecessor.id),
        "predecessor_workflow_id": predecessor.temporal_workflow_id,
        "predecessor_temporal_run_id": predecessor.temporal_run_id,
        "reason": PREFLIGHT_RECOVERY_REASON,
        "scientific_output_reused": False,
        "candidate_sequence_sha256s": [row.sequence_sha256 for row in ordered],
        "candidate_family_keys": [
            str(row.metadata_json.get("family_key_80_80", "")) for row in ordered
        ],
        "workflow_spec": copy.deepcopy(predecessor.spec_json["workflow_spec"]),
    }
    formal_submission_key = sha256_json(identity)
    run_id = uuid.uuid5(RECOVERY_RUN_NAMESPACE, formal_submission_key)
    workflow_uuid = uuid.uuid5(RECOVERY_WORKFLOW_NAMESPACE, formal_submission_key)
    workflow_id = f"pepagent-structure-v2-recovery-{target_key}-{workflow_uuid}"
    run_spec = copy.deepcopy(dict(predecessor.spec_json))
    run_spec.update(
        {
            **identity,
            "run_id": str(run_id),
            "workflow_id": workflow_id,
            "formal_submission_key": formal_submission_key,
            "structure_v2_reservation_key": recovery_key,
            "cohort_id": recovery_key,
            "predecessor_run_id": str(predecessor.id),
            "predecessor_workflow_id": predecessor.temporal_workflow_id,
            "predecessor_temporal_run_id": predecessor.temporal_run_id,
            "predecessor_reason": PREFLIGHT_RECOVERY_REASON,
            "preflight_recovery": _recovery_contract(predecessor),
            "scientific_output_reused": False,
            "temporal_submission_performed": False,
            "workflow_task_timeout_seconds": int(
                STRUCTURE_V2_WORKFLOW_TASK_TIMEOUT.total_seconds()
            ),
            "worker_queues": {
                "workflow": STRUCTURE_V2_WORKFLOW_QUEUE,
                "rosetta": STRUCTURE_V2_ROSETTA_QUEUE,
                "persist": STRUCTURE_V2_PERSIST_QUEUE,
            },
            "receipt_contract": structure_v2_receipt_contract(),
        }
    )
    return RecoveryBranch(
        target_key=target_key,
        predecessor=predecessor,
        predecessor_candidates=ordered,
        predecessor_import_call=predecessor_import_call,
        run_id=run_id,
        workflow_id=workflow_id,
        formal_submission_key=formal_submission_key,
        run_spec=run_spec,
    )


def _recovery_candidate_metadata(
    *,
    recovery_key: str,
    predecessor_run: ExperimentRun,
    predecessor_candidate: Candidate,
    rank: int,
) -> dict[str, Any]:
    metadata = copy.deepcopy(dict(predecessor_candidate.metadata_json))
    snapshot = metadata.get("structure_v2_eligibility")
    if not isinstance(snapshot, Mapping):
        raise ValueError("structure v2 recovery predecessor eligibility is missing")
    metadata["structure_v2_eligibility"] = {
        **copy.deepcopy(dict(snapshot)),
        "cohort_sha256": recovery_key,
    }
    metadata["structure_rank"] = rank
    metadata["preflight_recovery"] = {
        "predecessor_run_id": str(predecessor_run.id),
        "predecessor_candidate_id": str(predecessor_candidate.id),
        "reason": PREFLIGHT_RECOVERY_REASON,
        "scientific_output_reused": False,
    }
    return metadata


async def _assert_temporal_absent(workflow_ids: Sequence[str]) -> None:
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    for workflow_id in workflow_ids:
        try:
            await client.get_workflow_handle(workflow_id).describe()
        except RPCError as error:
            if error.status == RPCStatusCode.NOT_FOUND:
                continue
            raise
        raise ValueError(f"structure v2 recovery workflow already exists: {workflow_id}")


async def _load_predecessors(
    session: AsyncSession,
    predecessor_reservation_key: str,
) -> tuple[
    tuple[ExperimentRun, ...],
    tuple[Candidate, ...],
    dict[uuid.UUID, ToolCall],
]:
    runs = tuple(
        await session.scalars(
            select(ExperimentRun)
            .where(
                ExperimentRun.spec_json["structure_v2_reservation_key"].as_string()
                == predecessor_reservation_key
            )
            .with_for_update()
        )
    )
    by_target = {str(run.spec_json.get("target_key", "")): run for run in runs}
    if len(runs) != len(TARGET_KEYS) or set(by_target) != set(TARGET_KEYS):
        raise ValueError("structure v2 recovery predecessor target set differs")
    ordered_runs = tuple(by_target[target] for target in TARGET_KEYS)
    if any(
        run.status != RunStatus.FAILED
        or run.finished_at is None
        or not run.temporal_workflow_id
        or not run.temporal_run_id
        for run in ordered_runs
    ):
        raise ValueError("structure v2 recovery predecessor is not durably failed")
    run_ids = {run.id for run in ordered_runs}
    candidates = tuple(
        await session.scalars(
            select(Candidate)
            .where(Candidate.run_id.in_(run_ids))
            .order_by(Candidate.run_id, Candidate.proposal_rank, Candidate.id)
        )
    )
    if (
        len(candidates) != len(TARGET_KEYS) * STRUCTURE_V2_RECEIPT_GOAL
        or len({row.sequence_sha256 for row in candidates}) != len(candidates)
        or len(
            {str(row.metadata_json.get("family_key_80_80", "")) for row in candidates}
        )
        != len(candidates)
        or any(row.status != CandidateStatus.STRUCTURE_QUEUED for row in candidates)
    ):
        raise ValueError("structure v2 recovery predecessor candidate cohort differs")

    calls = tuple(await session.scalars(select(ToolCall).where(ToolCall.run_id.in_(run_ids))))
    scientific_calls = [call for call in calls if call.tool_name != STRUCTURE_COHORT_IMPORT_TOOL]
    if scientific_calls:
        raise ValueError("structure v2 recovery predecessor has scientific ToolCalls")
    imports_by_run = {
        call.run_id: call
        for call in calls
        if call.tool_name == STRUCTURE_COHORT_IMPORT_TOOL
        and call.tool_version == STRUCTURE_COHORT_IMPORT_VERSION
        and str(call.status) == "succeeded"
    }
    if len(calls) != len(TARGET_KEYS) or set(imports_by_run) != run_ids:
        raise ValueError("structure v2 recovery predecessor import provenance differs")
    evaluation_count = int(
        await session.scalar(
            select(func.count())
            .select_from(Evaluation)
            .where(Evaluation.tool_call_id.in_([call.id for call in calls]))
        )
        or 0
    )
    if evaluation_count:
        raise ValueError("structure v2 recovery predecessor has scientific evaluations")
    failed_run_ids = set(
        await session.scalars(
            select(LifecycleEvent.aggregate_id).where(
                LifecycleEvent.aggregate_type == "run",
                LifecycleEvent.aggregate_id.in_(run_ids),
                LifecycleEvent.event_type == "run.failed",
            )
        )
    )
    if failed_run_ids != run_ids:
        raise ValueError("structure v2 recovery predecessor lacks failed-run lifecycle")
    return ordered_runs, candidates, imports_by_run


async def _source_artifacts_for_call(
    session: AsyncSession,
    call_id: uuid.UUID,
) -> tuple[tuple[str, Artifact], ...]:
    edges = tuple(
        await session.scalars(
            select(EvidenceArtifact).where(EvidenceArtifact.tool_call_id == call_id)
        )
    )
    result = []
    for edge in edges:
        artifact = await session.get(Artifact, edge.artifact_id)
        if artifact is None:
            raise ValueError("structure v2 recovery source artifact disappeared")
        result.append((edge.role, artifact))
    if len(result) != 3:
        raise ValueError("structure v2 recovery source artifact set differs")
    return tuple(sorted(result, key=lambda item: item[0]))


async def _existing_recovery_runs(
    session: AsyncSession,
    recovery_key: str,
) -> tuple[ExperimentRun, ...]:
    return tuple(
        await session.scalars(
            select(ExperimentRun).where(
                ExperimentRun.spec_json["structure_v2_reservation_key"].as_string()
                == recovery_key
            )
        )
    )


async def reserve_structure_v2_preflight_recovery_inert(
    predecessor_reservation_key: str,
    *,
    execute: bool,
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    async with session_factory() as session, session.begin():
        await session.execute(text("SET LOCAL statement_timeout = '300s'"))
        await session.execute(text("SET LOCAL lock_timeout = '30s'"))
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": GLOBAL_LOCK_ID}
        )
        predecessors, predecessor_candidates, imports_by_run = await _load_predecessors(
            session,
            predecessor_reservation_key,
        )
        recovery_key = preflight_recovery_reservation_key(
            predecessor_reservation_key,
            predecessors,
            predecessor_candidates,
        )
        by_run: dict[uuid.UUID, list[Candidate]] = defaultdict(list)
        for candidate in predecessor_candidates:
            by_run[candidate.run_id].append(candidate)
        branches = tuple(
            _recovery_branch(
                recovery_key=recovery_key,
                predecessor=predecessor,
                predecessor_candidates=by_run[predecessor.id],
                predecessor_import_call=imports_by_run[predecessor.id],
            )
            for predecessor in predecessors
        )
        await _assert_temporal_absent([branch.workflow_id for branch in branches])

        existing = await _existing_recovery_runs(session, recovery_key)
        if existing:
            if len(existing) != len(TARGET_KEYS):
                raise ValueError("structure v2 recovery reservation is partially present")
            return await _readback(
                session,
                recovery_key=recovery_key,
                predecessor_runs=predecessors,
                recovery_runs=existing,
                created=False,
            )

        predecessor_run_ids = {run.id for run in predecessors}
        other_rows = tuple(
            await session.execute(
                select(
                    Candidate.sequence_sha256,
                    Candidate.metadata_json["family_key_80_80"].as_string(),
                )
                .join(ExperimentRun, Candidate.run_id == ExperimentRun.id)
                .where(
                    ExperimentRun.id.not_in(predecessor_run_ids),
                    _structure_filter(),
                )
            )
        )
        predecessor_sequences = {row.sequence_sha256 for row in predecessor_candidates}
        predecessor_families = {
            str(row.metadata_json.get("family_key_80_80", ""))
            for row in predecessor_candidates
        }
        other_sequences = {row[0] for row in other_rows if row[0]}
        other_families = {row[1] for row in other_rows if row[1]}
        if predecessor_sequences & other_sequences or predecessor_families & other_families:
            raise ValueError(
                "structure v2 recovery cohort intersects non-predecessor structure history"
            )
        plan = {
            "schema_version": RECOVERY_RECEIPT_SCHEMA,
            "status": "ready_inert" if execute else "preflight_inert",
            "created": False,
            "recovery_reservation_key": recovery_key,
            "predecessor_reservation_key": predecessor_reservation_key,
            "reason": PREFLIGHT_RECOVERY_REASON,
            "scientific_output_reused": False,
            "run_count": len(branches),
            "candidate_count": len(predecessor_candidates),
            "distinct_sequence_count": len(predecessor_sequences),
            "distinct_family_count": len(predecessor_families),
            "other_structure_sequence_intersection_count": 0,
            "other_structure_family_intersection_count": 0,
            "temporal_submission_performed": False,
            "branches": [
                {
                    "target_key": branch.target_key,
                    "predecessor_run_id": str(branch.predecessor.id),
                    "run_id": str(branch.run_id),
                    "workflow_id": branch.workflow_id,
                    "candidate_count": len(branch.predecessor_candidates),
                }
                for branch in branches
            ],
        }
        if not execute:
            return plan

        repository = ExperimentRepository(session)
        created_runs: list[ExperimentRun] = []
        for branch in branches:
            collision = await session.scalar(
                select(ExperimentRun.id).where(
                    or_(
                        ExperimentRun.id == branch.run_id,
                        ExperimentRun.formal_submission_key
                        == branch.formal_submission_key,
                        ExperimentRun.temporal_workflow_id == branch.workflow_id,
                    )
                )
            )
            if collision is not None:
                raise ValueError("structure v2 recovery deterministic identity collides")
            run = ExperimentRun(
                id=branch.run_id,
                target_id=branch.predecessor.target_id,
                spec_json=branch.run_spec,
                spec_sha256=sha256_json(branch.run_spec),
                formal_submission_key=branch.formal_submission_key,
                status=RunStatus.CREATED,
                temporal_workflow_id=branch.workflow_id,
                temporal_run_id=None,
                parent_run_id=branch.predecessor.id,
            )
            session.add(run)
            await session.flush()
            await repository.append_event(
                "run",
                run.id,
                "structure.v2.preflight_recovery.pg_reserved",
                RECOVERY_ACTOR,
                {
                    "recovery_reservation_key": recovery_key,
                    "predecessor_run_id": str(branch.predecessor.id),
                    "predecessor_workflow_id": branch.predecessor.temporal_workflow_id,
                    "reason": PREFLIGHT_RECOVERY_REASON,
                    "candidate_count": STRUCTURE_V2_RECEIPT_GOAL,
                    "scientific_output_reused": False,
                    "workflow_id": branch.workflow_id,
                    "temporal_submission_performed": False,
                },
            )
            old_call = branch.predecessor_import_call
            source_key = str(old_call.input_json.get("source_content_address_key", ""))
            strict_library = str(old_call.input_json.get("strict_library_sha256", ""))
            import_call = await repository.record_completed_tool_call(
                run.id,
                STRUCTURE_COHORT_IMPORT_TOOL,
                STRUCTURE_COHORT_IMPORT_VERSION,
                old_call.environment_sha256,
                {
                    "cohort_id": recovery_key,
                    "target_key": branch.target_key,
                    "candidate_count": STRUCTURE_V2_RECEIPT_GOAL,
                    "source_content_address_key": source_key,
                    "strict_library_sha256": strict_library,
                    "predecessor_run_id": str(branch.predecessor.id),
                    "predecessor_import_tool_call_id": str(old_call.id),
                    "reason": PREFLIGHT_RECOVERY_REASON,
                    "scientific_output_reused": False,
                },
                {
                    "selection_policy": "exact_failed_preflight_cohort_reissue",
                    "other_structure_history_excluded": True,
                    "predecessor_scientific_tool_calls": 0,
                    "predecessor_scientific_evaluations": 0,
                },
                {
                    "candidate_identities": [
                        {
                            "predecessor_candidate_id": str(row.id),
                            "sequence_sha256": row.sequence_sha256,
                            "family_key_80_80": str(
                                row.metadata_json.get("family_key_80_80", "")
                            ),
                        }
                        for row in branch.predecessor_candidates
                    ],
                    "scientific_output_reused": False,
                },
                weights_sha256=old_call.weights_sha256,
                model_uri=old_call.model_uri,
                random_seed=old_call.random_seed,
            )
            source_artifacts = await _source_artifacts_for_call(session, old_call.id)
            await _link_source_artifacts(
                session,
                call=import_call,
                artifacts=source_artifacts,
            )
            for rank, predecessor_candidate in enumerate(
                branch.predecessor_candidates,
                start=1,
            ):
                candidate = await repository.add_candidate(
                    run.id,
                    predecessor_candidate.sequence,
                    generation=predecessor_candidate.generation,
                    proposal_rank=rank,
                    generator_call_id=import_call.id,
                    metadata=_recovery_candidate_metadata(
                        recovery_key=recovery_key,
                        predecessor_run=branch.predecessor,
                        predecessor_candidate=predecessor_candidate,
                        rank=rank,
                    ),
                    actor=RECOVERY_ACTOR,
                )
                await repository.append_event(
                    "candidate",
                    candidate.id,
                    "candidate.preflight_recovery_reissued",
                    RECOVERY_ACTOR,
                    {
                        "predecessor_run_id": str(branch.predecessor.id),
                        "predecessor_candidate_id": str(predecessor_candidate.id),
                        "reason": PREFLIGHT_RECOVERY_REASON,
                        "scientific_output_reused": False,
                        "sequence_sha256": candidate.sequence_sha256,
                        "family_key_80_80": candidate.metadata_json["family_key_80_80"],
                    },
                )
                await repository.transition_candidate(
                    candidate.id,
                    CandidateStatus.STRUCTURE_QUEUED,
                    RECOVERY_ACTOR,
                    "exact failed-preflight cohort reissued without scientific output reuse",
                )
            await repository.append_event(
                "run",
                run.id,
                "structure.v2.preflight_recovery.ready_inert",
                RECOVERY_ACTOR,
                {
                    "recovery_reservation_key": recovery_key,
                    "target_key": branch.target_key,
                    "candidate_count": STRUCTURE_V2_RECEIPT_GOAL,
                    "distinct_family_count": STRUCTURE_V2_RECEIPT_GOAL,
                    "reason": PREFLIGHT_RECOVERY_REASON,
                    "scientific_output_reused": False,
                    "workflow_id": branch.workflow_id,
                    "workflow_task_timeout_seconds": int(
                        STRUCTURE_V2_WORKFLOW_TASK_TIMEOUT.total_seconds()
                    ),
                    "worker_queues": branch.run_spec["worker_queues"],
                    "temporal_workflow_absent": True,
                    "temporal_submission_performed": False,
                },
            )
            created_runs.append(run)

        new_run_ids = {run.id for run in created_runs}
        allowed_run_ids = predecessor_run_ids | new_run_ids
        current_other_rows = tuple(
            await session.execute(
                select(
                    Candidate.sequence_sha256,
                    Candidate.metadata_json["family_key_80_80"].as_string(),
                )
                .join(ExperimentRun, Candidate.run_id == ExperimentRun.id)
                .where(ExperimentRun.id.not_in(allowed_run_ids), _structure_filter())
            )
        )
        current_other_sequences = frozenset(row[0] for row in current_other_rows if row[0])
        current_other_families = frozenset(row[1] for row in current_other_rows if row[1])
        for branch in branches:
            target = await session.get(Target, branch.predecessor.target_id)
            if target is None:
                raise ValueError("structure v2 recovery target disappeared")
            await _validate_branch_binding(
                session,
                branch=BranchPlan(
                    target_key=branch.target_key,
                    predecessor_run_id=branch.predecessor.id,
                    target=target,
                    workflow_spec=copy.deepcopy(branch.run_spec["workflow_spec"]),
                    selected=(),
                    formal_submission_key=branch.formal_submission_key,
                    run_id=branch.run_id,
                    workflow_id=branch.workflow_id,
                    run_spec=branch.run_spec,
                ),
                excluded_sequences=current_other_sequences,
                excluded_families=current_other_families,
            )
        await session.flush()
        return await _readback(
            session,
            recovery_key=recovery_key,
            predecessor_runs=predecessors,
            recovery_runs=tuple(created_runs),
            created=True,
        )


async def _readback(
    session: AsyncSession,
    *,
    recovery_key: str,
    predecessor_runs: Sequence[ExperimentRun],
    recovery_runs: Sequence[ExperimentRun],
    created: bool,
) -> dict[str, Any]:
    predecessor_ids = {run.id for run in predecessor_runs}
    recovery_ids = {run.id for run in recovery_runs}
    candidates = tuple(
        await session.scalars(select(Candidate).where(Candidate.run_id.in_(recovery_ids)))
    )
    predecessor_candidates = tuple(
        await session.scalars(select(Candidate).where(Candidate.run_id.in_(predecessor_ids)))
    )
    calls = tuple(await session.scalars(select(ToolCall).where(ToolCall.run_id.in_(recovery_ids))))
    candidate_ids = {row.id for row in candidates}
    events = tuple(
        await session.scalars(
            select(LifecycleEvent).where(
                LifecycleEvent.aggregate_type == "candidate",
                LifecycleEvent.aggregate_id.in_(candidate_ids),
            )
        )
    )
    edges = tuple(
        await session.scalars(
            select(EvidenceArtifact).where(
                EvidenceArtifact.tool_call_id.in_([call.id for call in calls])
            )
        )
    )
    locations = tuple(
        await session.scalars(
            select(EvidenceArtifactLocation).where(
                EvidenceArtifactLocation.tool_call_id.in_([call.id for call in calls])
            )
        )
    )
    sequences = {row.sequence_sha256 for row in candidates}
    families = {str(row.metadata_json.get("family_key_80_80", "")) for row in candidates}
    predecessor_sequences = {row.sequence_sha256 for row in predecessor_candidates}
    predecessor_families = {
        str(row.metadata_json.get("family_key_80_80", "")) for row in predecessor_candidates
    }
    other_rows = tuple(
        await session.execute(
            select(
                Candidate.sequence_sha256,
                Candidate.metadata_json["family_key_80_80"].as_string(),
            )
            .join(ExperimentRun, Candidate.run_id == ExperimentRun.id)
            .where(
                ExperimentRun.id.not_in(predecessor_ids | recovery_ids),
                _structure_filter(),
            )
        )
    )
    other_sequences = {row[0] for row in other_rows if row[0]}
    other_families = {row[1] for row in other_rows if row[1]}
    receipt = {
        "schema_version": RECOVERY_RECEIPT_SCHEMA,
        "status": "ready_inert",
        "created": created,
        "recovery_reservation_key": recovery_key,
        "reason": PREFLIGHT_RECOVERY_REASON,
        "scientific_output_reused": False,
        "run_count": len(recovery_runs),
        "candidate_count": len(candidates),
        "distinct_sequence_count": len(sequences),
        "distinct_family_count": len(families),
        "predecessor_sequence_reuse_count": len(sequences & predecessor_sequences),
        "predecessor_family_reuse_count": len(families & predecessor_families),
        "new_candidate_identity_count": len(
            candidate_ids - {row.id for row in predecessor_candidates}
        ),
        "other_structure_sequence_intersection_count": len(sequences & other_sequences),
        "other_structure_family_intersection_count": len(families & other_families),
        "cohort_import_tool_call_count": sum(
            call.tool_name == STRUCTURE_COHORT_IMPORT_TOOL for call in calls
        ),
        "scientific_tool_call_count": sum(
            call.tool_name != STRUCTURE_COHORT_IMPORT_TOOL for call in calls
        ),
        "candidate_generated_event_count": sum(
            event.event_type == "candidate.generated" for event in events
        ),
        "candidate_reissued_event_count": sum(
            event.event_type == "candidate.preflight_recovery_reissued" for event in events
        ),
        "candidate_structure_queued_event_count": sum(
            event.event_type == "candidate.status_changed" for event in events
        ),
        "source_artifact_edge_count": len(edges),
        "source_artifact_location_count": len(locations),
        "temporal_submission_performed": False,
        "branches": [
            {
                "target_key": str(run.spec_json.get("target_key", "")),
                "predecessor_run_id": str(run.parent_run_id),
                "run_id": str(run.id),
                "workflow_id": run.temporal_workflow_id,
                "temporal_run_id": run.temporal_run_id,
                "run_status": str(run.status),
                "candidate_count": sum(row.run_id == run.id for row in candidates),
            }
            for run in sorted(
                recovery_runs,
                key=lambda item: str(item.spec_json.get("target_key", "")),
            )
        ],
    }
    expected = {
        "run_count": 6,
        "candidate_count": 300,
        "distinct_sequence_count": 300,
        "distinct_family_count": 300,
        "predecessor_sequence_reuse_count": 300,
        "predecessor_family_reuse_count": 300,
        "new_candidate_identity_count": 300,
        "other_structure_sequence_intersection_count": 0,
        "other_structure_family_intersection_count": 0,
        "cohort_import_tool_call_count": 6,
        "scientific_tool_call_count": 0,
        "candidate_generated_event_count": 300,
        "candidate_reissued_event_count": 300,
        "candidate_structure_queued_event_count": 300,
        "source_artifact_edge_count": 18,
        "source_artifact_location_count": 18,
    }
    for field, value in expected.items():
        if receipt[field] != value:
            raise ValueError(
                f"structure v2 recovery readback {field}={receipt[field]} expected={value}"
            )
    if any(
        branch["candidate_count"] != 50
        or branch["temporal_run_id"] is not None
        or branch["run_status"] != "created"
        for branch in receipt["branches"]
    ):
        raise ValueError("structure v2 recovery branch readback differs")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reserve exact failed-preflight Structure v2 cohort successors in PG only"
    )
    parser.add_argument("--predecessor-reservation-key", required=True)
    parser.add_argument("--execute", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        pool_pre_ping=True,
        isolation_level="SERIALIZABLE",
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        return await reserve_structure_v2_preflight_recovery_inert(
            args.predecessor_reservation_key,
            execute=bool(args.execute),
            session_factory=factory,
        )
    finally:
        await engine.dispose()


def main() -> None:
    print(json.dumps(asyncio.run(_run(_parser().parse_args())), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()


__all__ = [
    "RECOVERY_RECEIPT_SCHEMA",
    "RECOVERY_RESERVATION_SCHEMA",
    "preflight_recovery_reservation_key",
    "reserve_structure_v2_preflight_recovery_inert",
]
