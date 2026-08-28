from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Any

from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_json
from pepagent.structure_v2_binding import bind_structure_v2_target_request
from pepagent.workflows.structure_v2 import (
    STRUCTURE_V2_WORKFLOW_QUEUE,
    STRUCTURE_V2_WORKFLOW_TASK_TIMEOUT,
    validate_structure_v2_target_request,
)

STRUCTURE_V2_SUCCESSOR_SCHEMA = "ampgent.structure-workflow-successor.2"


def validate_predecessor(predecessor: Mapping[str, Any]) -> dict[str, str]:
    normalized = {
        field: str(predecessor.get(field, "")).strip()
        for field in ("workflow_id", "run_id", "activity_id", "reason")
    }
    missing = [field for field, value in normalized.items() if not value]
    if missing:
        raise ValueError(f"structure successor predecessor lacks {', '.join(missing)}")
    return normalized


def _successor_memo(
    *,
    workflow_id: str,
    request: Mapping[str, Any],
    predecessor: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not workflow_id.strip():
        raise ValueError("structure v2 workflow ID must not be empty")
    normalized_predecessor = validate_predecessor(predecessor) if predecessor is not None else None
    if normalized_predecessor is not None and workflow_id == normalized_predecessor["workflow_id"]:
        raise ValueError("structure successor must use a new workflow ID")
    run_id = str(request.get("run_id", "")).strip()
    if not run_id:
        raise ValueError("structure v2 request requires its scientific run ID")
    return {
        "schema_version": STRUCTURE_V2_SUCCESSOR_SCHEMA,
        "workflow_id": workflow_id,
        "scientific_run_id": run_id,
        "predecessor": normalized_predecessor,
        "request_identity": sha256_json(dict(request)),
        "workflow_task_timeout_seconds": int(STRUCTURE_V2_WORKFLOW_TASK_TIMEOUT.total_seconds()),
    }


async def start_structure_validation_v2(
    client: Client,
    *,
    workflow_id: str,
    request: dict[str, Any],
    predecessor: Mapping[str, Any] | None = None,
    session_factory: Callable[[], Any] = SessionFactory,
) -> WorkflowHandle[Any, Any]:
    """Start a new v2 run without reusing or resetting predecessor history."""

    bound_request = await bind_structure_v2_target_request(
        request,
        session_factory=session_factory,
    )
    validate_structure_v2_target_request(bound_request)
    memo = _successor_memo(
        workflow_id=workflow_id,
        request=bound_request,
        predecessor=predecessor,
    )
    return await client.start_workflow(
        "CandidateStructureValidationWorkflowV2",
        bound_request,
        id=workflow_id,
        task_queue=STRUCTURE_V2_WORKFLOW_QUEUE,
        task_timeout=STRUCTURE_V2_WORKFLOW_TASK_TIMEOUT,
        memo={"ampgent_structure_v2_successor": memo},
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
    )


async def start_structure_evidence_repair_v2(
    client: Client,
    *,
    workflow_id: str,
    run_id: str,
    score_reference: dict[str, Any],
    predecessor: Mapping[str, Any],
) -> WorkflowHandle[Any, Any]:
    """Start a pointer-based repair successor with an explicit predecessor edge."""

    normalized_predecessor = validate_predecessor(predecessor)
    request = {
        "run_id": run_id,
        "score_reference": score_reference,
        "predecessor": normalized_predecessor,
    }
    memo = _successor_memo(
        workflow_id=workflow_id,
        request=request,
        predecessor=normalized_predecessor,
    )
    return await client.start_workflow(
        "StructureEvidenceRepairWorkflowV2",
        request,
        id=workflow_id,
        task_queue=STRUCTURE_V2_WORKFLOW_QUEUE,
        task_timeout=timedelta(seconds=60),
        memo={"ampgent_structure_v2_successor": memo},
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
    )


__all__ = [
    "STRUCTURE_V2_SUCCESSOR_SCHEMA",
    "start_structure_evidence_repair_v2",
    "start_structure_validation_v2",
    "validate_predecessor",
]
