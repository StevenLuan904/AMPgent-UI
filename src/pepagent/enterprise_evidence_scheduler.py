from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from pepagent.provenance.hashing import sha256_json

EVIDENCE_BACKLOG_SCHEMA = "ampgent.enterprise-evidence-backlog.1"


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


@dataclass(frozen=True)
class EvidenceActionPlan:
    schema_version: str
    backlog_sha256: str
    unresolved_domains: tuple[str, ...]
    selected_task_ids: tuple[str, ...]
    deferred_reasons: dict[str, str]
    estimated_cpu_minutes: int
    estimated_gpu_minutes: int
    formal_run_submission_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _gap_domains(model_registry_audit: Mapping[str, Any]) -> set[str]:
    gaps = model_registry_audit.get("gaps")
    if not isinstance(gaps, (list, tuple)):
        raise ValueError("model registry audit gaps must be a list")
    domains: set[str] = set()
    for gap in gaps:
        text = _text(gap, label="model registry gap")
        domain, separator, _details = text.partition(":")
        if not separator:
            raise ValueError(f"model registry gap has no domain: {text}")
        domains.add(domain)
    return domains


def plan_enterprise_evidence_actions(
    *,
    model_registry_audit: Mapping[str, Any],
    backlog: Mapping[str, Any],
    completed_task_ids: set[str] | None = None,
    maximum_actions: int = 3,
    maximum_cpu_minutes: int = 240,
    maximum_gpu_minutes: int = 0,
) -> EvidenceActionPlan:
    """Choose the next discriminating evidence work without launching a science run.

    Ordering is deterministic and lexicographic, not a scientific weighted score: lower priority
    tier, more unresolved domains covered, less GPU, less CPU, then task identity. Tasks must carry
    explicit acceptance artifacts and stop conditions so a scheduled loop cannot claim progress
    from prose, monitoring, or an unqualified model smoke.
    """

    if backlog.get("schema_version") != EVIDENCE_BACKLOG_SCHEMA:
        raise ValueError("enterprise evidence backlog schema is invalid")
    tasks = backlog.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError("enterprise evidence backlog tasks must be a list")
    if maximum_actions < 1 or maximum_cpu_minutes < 0 or maximum_gpu_minutes < 0:
        raise ValueError("enterprise evidence action limits are invalid")

    unresolved = _gap_domains(model_registry_audit)
    completed = set(completed_task_ids or set())
    for raw_task in tasks:
        if isinstance(raw_task, Mapping) and raw_task.get("status") == "completed":
            completed.add(_text(raw_task.get("task_id"), label="completed task_id"))
    candidates: list[tuple[tuple[int, int, int, int, str], Mapping[str, Any]]] = []
    deferred: dict[str, str] = {}
    seen: set[str] = set()

    for raw_task in tasks:
        if not isinstance(raw_task, Mapping):
            raise ValueError("enterprise evidence task must be an object")
        task_id = _text(raw_task.get("task_id"), label="task_id")
        if task_id in seen:
            raise ValueError(f"duplicate enterprise evidence task: {task_id}")
        seen.add(task_id)
        domains = raw_task.get("evidence_domains")
        if not isinstance(domains, list) or not domains:
            raise ValueError(f"enterprise evidence task has no domains: {task_id}")
        domain_set = {_text(domain, label=f"domain for {task_id}") for domain in domains}
        acceptance = raw_task.get("acceptance_artifacts")
        stop_conditions = raw_task.get("stop_conditions")
        if not isinstance(acceptance, list) or not acceptance:
            raise ValueError(f"enterprise evidence task has no acceptance artifacts: {task_id}")
        if not isinstance(stop_conditions, list) or not stop_conditions:
            raise ValueError(f"enterprise evidence task has no stop conditions: {task_id}")
        status = _text(raw_task.get("status"), label=f"status for {task_id}")
        if task_id in completed or status == "completed":
            deferred[task_id] = "already_completed"
            continue
        covered = domain_set & unresolved
        if not covered:
            deferred[task_id] = "does_not_cover_current_gap"
            continue
        prerequisites = raw_task.get("prerequisites", [])
        if not isinstance(prerequisites, list):
            raise ValueError(f"enterprise evidence task prerequisites are invalid: {task_id}")
        missing = sorted(str(item) for item in prerequisites if str(item) not in completed)
        if missing:
            deferred[task_id] = "waiting_for:" + ",".join(missing)
            continue
        if status != "ready":
            deferred[task_id] = f"status:{status}"
            continue
        cpu_minutes = int(raw_task.get("estimated_cpu_minutes", 0))
        gpu_minutes = int(raw_task.get("estimated_gpu_minutes", 0))
        if cpu_minutes < 0 or gpu_minutes < 0:
            raise ValueError(f"enterprise evidence task cost is invalid: {task_id}")
        order = (
            int(raw_task.get("priority_tier", 100)),
            -len(covered),
            gpu_minutes,
            cpu_minutes,
            task_id,
        )
        candidates.append((order, raw_task))

    selected: list[str] = []
    cpu_total = 0
    gpu_total = 0
    for _order, task in sorted(candidates, key=lambda item: item[0]):
        task_id = str(task["task_id"])
        cpu_minutes = int(task.get("estimated_cpu_minutes", 0))
        gpu_minutes = int(task.get("estimated_gpu_minutes", 0))
        if len(selected) >= maximum_actions:
            deferred[task_id] = "action_limit"
            continue
        if cpu_total + cpu_minutes > maximum_cpu_minutes:
            deferred[task_id] = "cpu_budget"
            continue
        if gpu_total + gpu_minutes > maximum_gpu_minutes:
            deferred[task_id] = "gpu_budget"
            continue
        selected.append(task_id)
        cpu_total += cpu_minutes
        gpu_total += gpu_minutes

    return EvidenceActionPlan(
        schema_version="ampgent.enterprise-evidence-action-plan.1",
        backlog_sha256=sha256_json(backlog),
        unresolved_domains=tuple(sorted(unresolved)),
        selected_task_ids=tuple(selected),
        deferred_reasons=dict(sorted(deferred.items())),
        estimated_cpu_minutes=cpu_total,
        estimated_gpu_minutes=gpu_total,
        formal_run_submission_allowed=bool(
            model_registry_audit.get("formal_science_run_authorized") is True
            and not unresolved
        ),
    )
