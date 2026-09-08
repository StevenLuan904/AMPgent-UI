from __future__ import annotations

import heapq
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

LOGICAL_STAGE_ORDER = {
    "generation": 10,
    "score_all": 20,
    "challenger": 30,
    "archive": 40,
    "boltz": 50,
    "rosetta": 60,
    "md": 70,
    "pool_s": 80,
    "other": 90,
}


def evidence_stage(
    *,
    tool_name: str | None = None,
    metric_name: str | None = None,
    evidence_family: str | None = None,
) -> str:
    """Map persisted evidence to scientific order, never ingestion time."""

    value = " ".join(part.casefold() for part in (tool_name, metric_name, evidence_family) if part)
    if "pool_s" in value or "pool-s" in value:
        return "pool_s"
    if any(
        token in value
        for token in (
            "mmgbsa",
            "mm/gbsa",
            "mmpbsa",
            "mm/pbsa",
            "molecular-dynamics",
            "molecular_dynamics",
            "-md-",
            "md_",
        )
    ):
        return "md"
    if "rosetta" in value or "interfaceanalyzer" in value:
        return "rosetta"
    if "boltz" in value or "structure-predict" in value:
        return "boltz"
    if any(token in value for token in ("challenger", "shadow", "hemopi2", "apex", "peptiverse")):
        return "challenger"
    if any(
        token in value
        for token in ("archive", "lineage", "quality-diversity", "quality_diversity", "qd-")
    ):
        return "archive"
    if any(
        token in value
        for token in ("score", "metric", "evaluation", "tox", "hemolysis", "instability")
    ):
        return "score_all"
    if any(
        token in value
        for token in ("generate", "generator", "proposal", "candidate-import", "cohort-import")
    ):
        return "generation"
    return "other"


def logical_evidence_prefix(
    run_id: uuid.UUID | str,
    *,
    stage: str,
    candidate_id: uuid.UUID | str | None = None,
) -> str:
    if stage not in LOGICAL_STAGE_ORDER:
        raise ValueError(f"unknown logical evidence stage: {stage}")
    root = f"runs/{run_id}"
    if candidate_id is not None:
        root += f"/candidates/{candidate_id}"
    return f"{root}/evidence/{LOGICAL_STAGE_ORDER[stage]:02d}-{stage}/"


def require_existing_posthoc_runs(existing_count: int, expected_count: int) -> None:
    """Allow historical reservations to resume but forbid new evidence-only runs."""

    if existing_count == 0:
        raise RuntimeError(
            "posthoc Rosetta/MD must attach to the candidate's original ExperimentRun; "
            "creating a separate evidence run is forbidden"
        )
    if existing_count != expected_count:
        raise ValueError("posthoc evidence reservation is partially present")


def order_tool_calls(calls: Sequence[Any], dependencies: Iterable[Any]) -> list[Any]:
    """Dependency-aware stable order with scientific stage before timestamps."""

    by_id = {call.id: call for call in calls}
    children: dict[Any, set[Any]] = {call_id: set() for call_id in by_id}
    indegree = {call_id: 0 for call_id in by_id}
    for edge in dependencies:
        parent = edge.parent_tool_call_id
        child = edge.child_tool_call_id
        if parent not in by_id or child not in by_id or child in children[parent]:
            continue
        children[parent].add(child)
        indegree[child] += 1

    def key(call_id: Any) -> tuple[int, int, str]:
        call = by_id[call_id]
        stage = evidence_stage(tool_name=call.tool_name)
        return LOGICAL_STAGE_ORDER[stage], int(call.attempt), str(call.id)

    ready = [(key(call_id), call_id) for call_id, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    ordered: list[Any] = []
    while ready:
        _, call_id = heapq.heappop(ready)
        ordered.append(by_id[call_id])
        for child in children[call_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(ready, (key(child), child))
    if len(ordered) != len(calls):
        raise ValueError("tool-call dependency graph contains a cycle")
    return ordered
