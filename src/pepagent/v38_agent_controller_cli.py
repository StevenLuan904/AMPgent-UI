from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import yaml
from sqlalchemy import func, select
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client

from pepagent.db.models import (
    AgentDecision,
    Candidate,
    CandidateOccurrence,
    Evaluation,
    EvidenceArtifact,
    ExperimentRun,
    MultiTargetStructureEvidenceRecord,
    RunStageCheckpoint,
    Target,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.settings import get_settings
from pepagent.v38_persistence import (
    MultiTargetRunBindingReceipt,
    StageCheckpointReceipt,
    TargetBranchBinding,
    persist_multitarget_run_binding,
    persist_stage_checkpoint,
)
from pepagent.v38_run_control import RunControlDecision, build_default_run_control_plan
from pepagent.v38_sequence_first_multitarget import (
    TargetQualificationWitness,
    build_historical_evidence_snapshot,
)

_FROZEN_V38_STRUCTURE_GPU_KEYS = frozenset({"192.168.99.32:1"})


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root is not an object: {path}")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _capacity_blocker(
    capacity: dict[str, Any], *, owned_structure_worker_pid: int | None = None
) -> str | None:
    observations = capacity.get("observations")
    if not isinstance(observations, list) or not observations:
        return "authorized_structure_gpu_currently_unreachable"
    by_key = {
        f"{item.get('host')}:{item.get('gpu_index')}": item
        for item in observations
        if isinstance(item, dict)
    }
    frozen = [by_key.get(key) for key in _FROZEN_V38_STRUCTURE_GPU_KEYS]
    if any(item is None or item.get("status") != "observed" for item in frozen):
        return "authorized_structure_gpu_currently_unreachable"
    idle = set(capacity.get("idle_gpu_keys") or [])
    if _FROZEN_V38_STRUCTURE_GPU_KEYS <= idle:
        return None
    frozen_observation = frozen[0]
    declarations = frozen_observation.get("cuda_visible_devices_declarations")
    declared_pids = (
        {str(item) for item in declarations}
        if isinstance(declarations, list)
        else ({str(declarations)} if declarations is not None else set())
    )
    if (
        owned_structure_worker_pid is not None
        and frozen_observation.get("compute_processes") in (None, "")
        and declared_pids == {str(owned_structure_worker_pid)}
    ):
        return None
    return "authorized_structure_gpu_currently_busy"


def _owned_structure_worker_pid(state_path: Path) -> int | None:
    placement_path = (
        state_path.parent.parent / "run" / "v38-workers" / "v38-structure-placement.json"
    )
    try:
        placement = json.loads(placement_path.read_text(encoding="utf-8"))
        worker = placement["workers"]["v38-boltz"]
        if (
            placement.get("schema_version") != "v38.worker-placement.1"
            or worker.get("resource") != "192.168.99.19:6"
            or worker.get("ampgent_owned") is not True
            or worker.get("foreign") is not False
            or not isinstance(worker.get("pid"), int)
        ):
            return None
        return worker["pid"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _refinement_provider_request_path() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "config"
        / "experiments"
        / "v38_refinement_provider_acceptance.yaml"
    )


def _load_refinement_provider_request() -> dict[str, Any]:
    return _load_yaml(_refinement_provider_request_path())


def _refinement_provider_blocker() -> str | None:
    try:
        request = _load_refinement_provider_request()
    except (OSError, ValueError, yaml.YAMLError):
        return "v38_refinement_provider_acceptance_contract_missing"
    if request.get("status") != "accepted_immutable_release":
        return "v38_refinement_provider_release_not_delivered"
    accepted = request.get("accepted_release")
    if not isinstance(accepted, dict):
        return "v38_refinement_provider_release_identity_drifted"
    sha_fields = (
        "release_manifest_sha256",
        "runtime_manifest_sha256",
        "environment_sha256",
        "immutable_release_receipt_sha256",
        "acceptance_receipt_sha256",
        "smoke_receipt_sha256",
    )
    if any(
        not isinstance(accepted.get(field), str) or len(accepted[field]) != 64
        for field in sha_fields
    ):
        return "v38_refinement_provider_release_identity_drifted"
    if (
        accepted.get("release_revision") is None
        or accepted.get("poller_identity") is None
        or not isinstance(accepted.get("poller_pid"), int)
    ):
        return "v38_refinement_provider_release_identity_drifted"
    return None


async def _refinement_provider_poller_blocker() -> str | None:
    try:
        request = _load_refinement_provider_request()
        accepted = request["accepted_release"]
        queue = request["required_temporal_interface"]["dedicated_task_queue"]
        expected = accepted["poller_identity"].lower()
        settings = get_settings()
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        response = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=settings.temporal_namespace,
                task_queue=TaskQueue(name=queue),
                task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
            )
        )
        if expected not in {item.identity.lower() for item in response.pollers}:
            return "v38_refinement_provider_poller_not_visible"
    except Exception:
        return "v38_refinement_provider_poller_not_visible"
    return None


async def _sequence_worker_release_status(
    state_path: Path,
) -> tuple[str | None, dict[str, Any] | None]:
    required = {
        "v38-control": (
            "pepagent-control-v38",
            (
                TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
                TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
            ),
        ),
        "v38-generator": ("pepagent-generator-v38", (TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,)),
        "v38-metrics": ("pepagent-cpu-metrics-v38", (TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,)),
    }
    receipt_root = state_path.parent.parent / "run" / "v38-workers"
    receipts: dict[str, dict[str, Any]] = {}
    try:
        for role in required:
            receipt = json.loads((receipt_root / f"{role}.json").read_text(encoding="utf-8"))
            if (
                receipt.get("schema_version") != "v38.local-sequence-worker-receipt.1"
                or receipt.get("role") != role
                or receipt.get("ampgent_owned") is not True
                or receipt.get("foreign") is not False
                or not isinstance(receipt.get("pid"), int)
            ):
                raise ValueError("invalid v38 worker receipt")
            receipts[role] = receipt
    except (OSError, ValueError, json.JSONDecodeError):
        return "v38_sequence_generation_and_refinement_worker_release_not_deployed", None
    sources = {item["source_revision"] for item in receipts.values()}
    releases = {item["release_sha256"] for item in receipts.values()}
    if (
        len(sources) != 1
        or len(releases) != 1
        or any(len(item) != 40 for item in sources)
        or any(len(item) != 64 for item in releases)
    ):
        return "v38_sequence_worker_release_identity_drifted", None
    settings = get_settings()
    try:
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        for role, (queue, kinds) in required.items():
            receipt = receipts[role]
            expected = (
                f"pepagent:{role}:{receipt['pid']}@{receipt['host']}:"
                f"{receipt['source_revision']}"
            ).lower()
            for kind in kinds:
                response = await client.workflow_service.describe_task_queue(
                    DescribeTaskQueueRequest(
                        namespace=settings.temporal_namespace,
                        task_queue=TaskQueue(name=queue),
                        task_queue_type=kind,
                    )
                )
                if expected not in {item.identity.lower() for item in response.pollers}:
                    return "v38_sequence_worker_poller_identity_not_visible", None
    except Exception:
        return "v38_sequence_worker_poller_identity_not_visible", None
    return None, {
        "source_revision": next(iter(sources)),
        "release_sha256": next(iter(releases)),
        "roles": {
            role: {"pid": item["pid"], "host": item["host"]}
            for role, item in receipts.items()
        },
    }


def _validate_panel(
    panel_path: Path,
    coordinate_root: Path,
) -> tuple[TargetQualificationWitness, ...]:
    payload = _load_yaml(panel_path)
    if payload.get("selection_frozen_before_peptide_outcomes") is not True:
        raise ValueError("target panel was not frozen before peptide outcomes")
    witnesses = tuple(
        TargetQualificationWitness.model_validate(item) for item in payload.get("branches", [])
    )
    if not 2 <= len(witnesses) <= 6:
        raise ValueError("v38 target panel requires two to six qualified branches")
    for witness in witnesses:
        coordinate = coordinate_root / f"{witness.coordinate_source_accession}.cif"
        if not coordinate.is_file():
            raise ValueError(f"target coordinate is missing: {coordinate}")
        content = coordinate.read_bytes()
        if len(content) != witness.coordinate_size_bytes:
            raise ValueError(f"target coordinate size drifted: {coordinate}")
        if sha256_bytes(content) != witness.coordinate_sha256:
            raise ValueError(f"target coordinate SHA drifted: {coordinate}")
    return witnesses


async def initialize_controller(
    *,
    benchmark_path: Path,
    panel_path: Path,
    coordinate_root: Path,
    state_path: Path,
    implementation_revision: str,
) -> dict[str, Any]:
    benchmark = _load_yaml(benchmark_path)
    if benchmark.get("scope", {}).get("formal_run_authorized") is not True:
        raise ValueError("v38 run authorization is not frozen")
    if benchmark.get("scope", {}).get("formal_run_submitted") is not False:
        raise ValueError("v38 formal run already claims to be submitted")
    witnesses = _validate_panel(panel_path, coordinate_root)
    benchmark_bytes, panel_bytes = await asyncio.gather(
        asyncio.to_thread(benchmark_path.read_bytes),
        asyncio.to_thread(panel_path.read_bytes),
    )
    plan = build_default_run_control_plan(structure_branch_count=len(witnesses))
    now = datetime.now(UTC)
    async with SessionFactory() as session, session.begin():
        history = await build_historical_evidence_snapshot(session, history_cutoff_at=now)
        target = await session.get(Target, witnesses[0].target_id)
        if target is None:
            raise ValueError("legacy primary target row does not exist")
        spec = {
            "schema_version": "v38.agent-control-run.1",
            "run_kind": "multitarget_sequence_first_agent_control",
            "benchmark_sha256": sha256_bytes(benchmark_bytes),
            "panel_sha256": sha256_bytes(panel_bytes),
            "history_snapshot_sha256": history.sha256(),
            "history_terminal_run_count": history.terminal_run_count,
            "target_witness_sha256": [item.sha256() for item in witnesses],
            "run_control_plan": plan.model_dump(mode="json"),
            "implementation_revision": implementation_revision,
            "knowledge_provider_task_id": benchmark["knowledge_use"]["provider_task_id"],
            "knowledge_provider_smoke_sha256": benchmark["knowledge_use"][
                "provider_smoke_context_pack_sha256"
            ],
            "candidate_generation_started": False,
            "formal_science_workflow_submitted": False,
        }
        formal_key = sha256_json({"v38_agent_control": spec})
        existing = await session.scalar(
            select(ExperimentRun).where(ExperimentRun.formal_submission_key == formal_key)
        )
        if existing is not None:
            run = existing
            created = False
        else:
            run = ExperimentRun(
                id=uuid4(),
                target_id=target.id,
                spec_json=spec,
                spec_sha256=sha256_json(spec),
                formal_submission_key=formal_key,
                status="created",
            )
            session.add(run)
            await session.flush()
            repository = ExperimentRepository(session)
            await repository.append_event(
                "run", run.id, "run.created", "v38-agent-controller", spec
            )
            await repository.append_event(
                "run",
                run.id,
                "v38.agent_control.initialized",
                "v38-agent-controller",
                {"formal_science_workflow_submitted": False},
            )
            branches = tuple(
                TargetBranchBinding(
                    branch_order=index,
                    branch_key=witness.target_key,
                    target_id=witness.target_id,
                    panel_role="qualified_target",
                    qualification_witness_sha256=witness.sha256(),
                    coordinate_sha256=witness.coordinate_sha256,
                    native_pocket_id=witness.primary_pocket_id,
                    wrong_pocket_id=witness.wrong_pocket_id,
                    evidence_namespace=f"target/{witness.target_key}/{witness.target_id}",
                    metadata={"coordinate_source_accession": witness.coordinate_source_accession},
                )
                for index, witness in enumerate(witnesses, start=1)
            )
            await persist_multitarget_run_binding(
                session,
                MultiTargetRunBindingReceipt(run_id=run.id, branches=branches),
            )
            freeze_decision = RunControlDecision(
                action="advance_stage",
                reasons=("history_target_panel_and_knowledge_contract_frozen",),
                tasks=("persist_stage_completion_receipt", "prepare_sequence_workers"),
            )
            await persist_stage_checkpoint(
                session,
                StageCheckpointReceipt(
                    run_id=run.id,
                    stage="history_target_knowledge_freeze",
                    stage_order=0,
                    observation_no=1,
                    durable_count=3,
                    expected_durable_count=3,
                    stage_status="completed",
                    decision=freeze_decision,
                    observed_at=now,
                ),
            )
            created = True
    state = {
        "schema_version": "v38.agent-controller-state.1",
        "controller_run_id": str(run.id),
        "formal_submission_key": formal_key,
        "created": created,
        "status": "controller_active_science_not_submitted",
        "current_stage": "proposal_generation",
        "formal_science_workflow_submitted": False,
        "candidate_generation_started": False,
        "blockers": [
            "v38_sequence_generation_and_refinement_worker_release_not_deployed",
            "authorized_structure_gpu_currently_unreachable",
        ],
        "history_snapshot_sha256": spec["history_snapshot_sha256"],
        "history_terminal_run_count": spec["history_terminal_run_count"],
        "target_witness_sha256": spec["target_witness_sha256"],
        "knowledge_provider_smoke_sha256": spec["knowledge_provider_smoke_sha256"],
        "implementation_revision": implementation_revision,
        "last_tick_at": now.isoformat(),
        "next_progress_check_at": (now + timedelta(minutes=5)).isoformat(),
        "next_plan_review_at": (now + timedelta(minutes=15)).isoformat(),
        "next_user_review_at": (now + timedelta(hours=2)).isoformat(),
    }
    _atomic_json(state_path, state)
    return state


async def _run_counts(run_id: UUID) -> dict[str, int]:
    async with SessionFactory() as session:
        direct_models = {
            "candidates": Candidate,
            "occurrences": CandidateOccurrence,
            "tool_calls": ToolCall,
            "decisions": AgentDecision,
            "structure_evidence_records": MultiTargetStructureEvidenceRecord,
        }
        counts = {
            name: int(
                await session.scalar(
                    select(func.count()).select_from(model).where(model.run_id == run_id)
                )
                or 0
            )
            for name, model in direct_models.items()
        }
        counts["evaluations"] = int(
            await session.scalar(
                select(func.count())
                .select_from(Evaluation)
                .join(Candidate, Candidate.id == Evaluation.candidate_id)
                .where(Candidate.run_id == run_id)
            )
            or 0
        )
        counts["replay_evidence_links"] = int(
            await session.scalar(
                select(func.count())
                .select_from(EvidenceArtifact)
                .join(ToolCall, ToolCall.id == EvidenceArtifact.tool_call_id)
                .where(
                    ToolCall.run_id == run_id,
                    EvidenceArtifact.role.like("%replay%"),
                )
            )
            or 0
        )
        return counts


def _infer_science_stage(counts: dict[str, int], *, run_status: str) -> str:
    if run_status in {"succeeded", "failed", "cancelled"}:
        return "terminal"
    if counts["replay_evidence_links"]:
        return "pareto_and_replay"
    if counts["structure_evidence_records"]:
        return "parallel_target_structure"
    if counts["decisions"]:
        return "sequence_admission"
    if counts["evaluations"]:
        return "sequence_metrics"
    return "proposal_generation"


async def _probe_services() -> dict[str, dict[str, object]]:
    settings = get_settings()
    health: dict[str, dict[str, object]] = {
        "database": {"healthy": True, "detail": "controller_count_query_succeeded"}
    }
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, url in (
            ("api", "http://127.0.0.1:8080/healthz"),
            ("object_store", f"{settings.s3_endpoint.rstrip('/')}/minio/health/live"),
        ):
            try:
                response = await client.get(url)
                response.raise_for_status()
                health[name] = {"healthy": True, "status_code": response.status_code}
            except Exception as exc:
                health[name] = {"healthy": False, "detail": type(exc).__name__}
    try:
        temporal = await asyncio.wait_for(
            Client.connect(
                settings.temporal_address,
                namespace=settings.temporal_namespace,
            ),
            timeout=5.0,
        )
        healthy = await asyncio.wait_for(temporal.service_client.check_health(), timeout=5.0)
        health["temporal"] = {"healthy": bool(healthy)}
    except Exception as exc:
        health["temporal"] = {"healthy": False, "detail": type(exc).__name__}
    return health


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("controller state contains a timezone-naive timestamp")
    return parsed


async def tick_controller(*, state_path: Path) -> dict[str, Any]:
    state_text = await asyncio.to_thread(state_path.read_text, encoding="utf-8")
    state = json.loads(state_text)
    run_id = UUID(state["controller_run_id"])
    submitted = state.get("formal_science_workflow_submitted") is True
    science_run_id = UUID(state["science_run_id"]) if submitted else None
    now = datetime.now(UTC)
    plan_due = now >= _parse_time(state["next_plan_review_at"])
    user_review_due = now >= _parse_time(state["next_user_review_at"])
    async with SessionFactory() as session, session.begin():
        run = await session.get(ExperimentRun, run_id)
        if run is None:
            raise ValueError("controller run identity is missing")
        if run.spec_json.get("implementation_revision") != state["implementation_revision"]:
            raise ValueError("controller implementation revision drifted")
        latest = await session.scalar(
            select(RunStageCheckpoint)
            .where(
                RunStageCheckpoint.run_id == run_id,
                RunStageCheckpoint.stage_name == state["current_stage"],
            )
            .order_by(RunStageCheckpoint.observation_no.desc())
            .limit(1)
        )
        science_run = (
            await session.get(ExperimentRun, science_run_id)
            if science_run_id is not None
            else None
        )
        if science_run_id is not None and science_run is None:
            raise ValueError("submitted v38 science run identity is missing")
        if plan_due and not submitted:
            decision = RunControlDecision(
                action="wait_for_executable_release",
                reasons=("v38_scientific_executor_not_yet_deployed",),
                tasks=(
                    "persist_sequence_admission_and_knowledge_refinement_as_temporal_evidence",
                    "integrate_isolated_parallel_target_branches_with_structure_workers",
                    "do_not_submit_legacy_v37_workflow",
                ),
            )
            await persist_stage_checkpoint(
                session,
                StageCheckpointReceipt(
                    run_id=run_id,
                    stage="proposal_generation",
                    stage_order=1,
                    observation_no=(latest.observation_no + 1) if latest else 1,
                    durable_count=0,
                    expected_durable_count=900,
                    stage_status="blocked_before_science_dispatch",
                    decision=decision,
                    observed_at=now,
                ),
            )
    state["last_tick_at"] = now.isoformat()
    monitored_run_id = science_run_id or run_id
    state["durable_counts"] = await _run_counts(monitored_run_id)
    if science_run is not None:
        state["current_stage"] = _infer_science_stage(
            state["durable_counts"], run_status=science_run.status
        )
        state["science_run_status"] = science_run.status
        state["status"] = (
            "formal_science_workflow_terminal"
            if science_run.status in {"succeeded", "failed", "cancelled"}
            else "formal_science_workflow_running"
        )
    state["service_health"] = await _probe_services()
    service_blocker = "control_plane_service_unhealthy"
    if all(item.get("healthy") is True for item in state["service_health"].values()):
        state["blockers"] = [item for item in state["blockers"] if item != service_blocker]
    elif service_blocker not in state["blockers"]:
        state["blockers"].append(service_blocker)
    capacity_blockers = {
        "authorized_structure_gpu_currently_unreachable",
        "authorized_structure_gpu_currently_busy",
    }
    state["blockers"] = [item for item in state["blockers"] if item not in capacity_blockers]
    capacity_path = state_path.with_name("ampgent-gpu-capacity.json")
    try:
        capacity = json.loads(capacity_path.read_text(encoding="utf-8"))
        capacity_blocker = _capacity_blocker(
            capacity,
            owned_structure_worker_pid=_owned_structure_worker_pid(state_path),
        )
    except (OSError, json.JSONDecodeError):
        capacity_blocker = "authorized_structure_gpu_currently_unreachable"
    if capacity_blocker is not None:
        state["blockers"].append(capacity_blocker)
    provider_blockers = {
        "v38_refinement_provider_acceptance_contract_missing",
        "v38_refinement_provider_release_not_delivered",
        "v38_refinement_provider_release_identity_drifted",
        "v38_refinement_provider_poller_not_visible",
    }
    state["blockers"] = [
        item for item in state["blockers"] if item not in provider_blockers
    ]
    provider_blocker = _refinement_provider_blocker()
    if provider_blocker is not None:
        state["blockers"].append(provider_blocker)
    else:
        provider_poller_blocker = await _refinement_provider_poller_blocker()
        if provider_poller_blocker is not None:
            state["blockers"].append(provider_poller_blocker)
        else:
            request = _load_refinement_provider_request()
            state["refinement_provider_release"] = request["accepted_release"]
    worker_blockers = {
        "v38_sequence_generation_and_refinement_worker_release_not_deployed",
        "v38_sequence_worker_release_identity_drifted",
        "v38_sequence_worker_poller_identity_not_visible",
    }
    state["blockers"] = [
        item for item in state["blockers"] if item not in worker_blockers
    ]
    worker_blocker, worker_release = await _sequence_worker_release_status(state_path)
    if worker_blocker is not None:
        state["blockers"].append(worker_blocker)
        state.pop("sequence_worker_release", None)
    else:
        state["sequence_worker_release"] = worker_release
    state["progress_check_due"] = True
    state["plan_review_performed"] = plan_due
    state["user_review_due"] = user_review_due
    state["next_progress_check_at"] = (now + timedelta(minutes=5)).isoformat()
    if plan_due:
        state["next_plan_review_at"] = (now + timedelta(minutes=15)).isoformat()
    if user_review_due:
        state["next_user_review_at"] = (now + timedelta(hours=2)).isoformat()
    _atomic_json(state_path, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate the v38 durable agent controller")
    parser.add_argument("--mode", choices=("initialize", "tick"), default="initialize")
    parser.add_argument("--benchmark", type=Path)
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--coordinate-root", type=Path)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--implementation-revision")
    args = parser.parse_args()
    if args.mode == "tick":
        state = asyncio.run(tick_controller(state_path=args.state.resolve()))
    else:
        if not all(
            (args.benchmark, args.panel, args.coordinate_root, args.implementation_revision)
        ):
            parser.error(
                "initialize requires --benchmark, --panel, --coordinate-root, "
                "and --implementation-revision"
            )
        state = asyncio.run(
            initialize_controller(
                benchmark_path=args.benchmark.resolve(),
                panel_path=args.panel.resolve(),
                coordinate_root=args.coordinate_root.resolve(),
                state_path=args.state.resolve(),
                implementation_revision=args.implementation_revision,
            )
        )
    print(json.dumps(state, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
