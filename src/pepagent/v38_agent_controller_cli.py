from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml
from sqlalchemy import func, select

from pepagent.db.models import (
    AgentDecision,
    Candidate,
    CandidateOccurrence,
    Evaluation,
    ExperimentRun,
    RunStageCheckpoint,
    Target,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_bytes, sha256_json
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
        counts: dict[str, int] = {}
        for name, model in (
            ("candidates", Candidate),
            ("occurrences", CandidateOccurrence),
            ("evaluations", Evaluation),
            ("tool_calls", ToolCall),
            ("decisions", AgentDecision),
        ):
            counts[name] = int(
                await session.scalar(
                    select(func.count()).select_from(model).where(model.run_id == run_id)
                )
                or 0
            )
        return counts


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("controller state contains a timezone-naive timestamp")
    return parsed


async def tick_controller(*, state_path: Path) -> dict[str, Any]:
    state_text = await asyncio.to_thread(state_path.read_text, encoding="utf-8")
    state = json.loads(state_text)
    run_id = UUID(state["controller_run_id"])
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
        if plan_due:
            decision = RunControlDecision(
                action="wait_for_executable_release",
                reasons=("v38_scientific_executor_not_yet_deployed",),
                tasks=(
                    "implement_score_all_proposals_sequence_workflow",
                    "implement_iterative_knowledge_traced_refinement",
                    "implement_isolated_parallel_target_branches",
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
    state["durable_counts"] = await _run_counts(run_id)
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
