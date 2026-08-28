from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from temporalio.client import Client

from pepagent.autoresearch_formal_submit_cli import (
    ACTOR,
    AutoResearchFormalBranch,
    AutoResearchFormalPlan,
    _advisory_lock_id,
    _build_run_spec,
    _revalidate_plan_submission_boundary,
    _workflow_description_memo,
    _workflow_memo_identity,
    load_autoresearch_formal_plan,
)
from pepagent.db.models import ExperimentRun, LifecycleEvent
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.settings import get_settings
from pepagent.storage.object_store import StoredObject

SCHEMA_VERSION = "ampgent.autoresearch-partial-submission-reconciliation.1"
WORKFLOW_MEMO_KEY = "ampgent_autoresearch_formal_submission_identity"
HELPER_PATH = Path(__file__).resolve()


def _expected_run_spec_from_existing_artifact(
    plan: AutoResearchFormalPlan,
    branch: AutoResearchFormalBranch,
    run: ExperimentRun,
) -> dict[str, Any]:
    artifact = run.spec_json.get("workflow_request_artifact")
    if not isinstance(artifact, dict):
        raise ValueError(f"{branch.branch_key} request artifact identity is missing")
    stored = StoredObject(
        sha256=str(artifact.get("sha256") or ""),
        size_bytes=int(artifact.get("size_bytes") or 0),
        uri=str(artifact.get("uri") or ""),
        media_type=str(artifact.get("media_type") or ""),
    )
    if (
        stored.sha256 != branch.request_sha256
        or stored.size_bytes != len(branch.request_bytes)
        or stored.media_type != "application/json"
        or not stored.uri
    ):
        raise ValueError(f"{branch.branch_key} request artifact identity drifted")
    return _build_run_spec(plan, branch, stored)


async def _describe_exact_existing_workflows(
    client: Client,
    plan: AutoResearchFormalPlan,
) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    for branch in plan.branches:
        description = await client.get_workflow_handle(branch.workflow_id).describe()
        if getattr(description, "workflow_type", None) != "AutoResearchClosedLoopWorkflow":
            raise ValueError(f"existing {branch.branch_key} workflow type differs")
        memo = await _workflow_description_memo(description)
        if memo.get(WORKFLOW_MEMO_KEY) != _workflow_memo_identity(plan, branch):
            raise ValueError(f"existing {branch.branch_key} workflow memo identity drifted")
        temporal_run_id = str(getattr(description, "run_id", "") or "")
        if not temporal_run_id:
            raise ValueError(f"existing {branch.branch_key} Temporal run ID is empty")
        bindings[branch.branch_key] = {
            "workflow_id": branch.workflow_id,
            "temporal_run_id": temporal_run_id,
            "temporal_status": str(getattr(description, "status", "")),
            "history_length": int(getattr(description, "history_length", 0)),
            "memo_identity_sha256": sha256_json(_workflow_memo_identity(plan, branch)),
        }
    return bindings


def _submitted_event_payload(
    branch: AutoResearchFormalBranch,
    temporal_run_id: str,
) -> dict[str, Any]:
    return {
        "workflow_id": branch.workflow_id,
        "temporal_run_id": temporal_run_id,
        "formal_submission_key": branch.formal_submission_key,
        "request_sha256": branch.request_sha256,
    }


def _validate_and_bind_run(
    run: ExperimentRun,
    *,
    branch: AutoResearchFormalBranch,
    expected_spec: dict[str, Any],
    temporal_run_id: str,
) -> bool:
    if (
        run.target_id != branch.target_id
        or run.formal_submission_key != branch.formal_submission_key
        or run.temporal_workflow_id != branch.workflow_id
        or run.parent_run_id != branch.parent_run_id
        or run.spec_json != expected_spec
        or run.spec_sha256 != sha256_json(expected_spec)
    ):
        raise ValueError(f"existing {branch.branch_key} durable reservation identity drifted")
    if run.temporal_run_id is None:
        if run.status != "created":
            raise ValueError(f"existing {branch.branch_key} unbound durable status differs")
        run.temporal_run_id = temporal_run_id
        run.status = "running"
        return True
    if run.temporal_run_id != temporal_run_id or run.status == "created":
        raise ValueError(f"existing {branch.branch_key} durable Temporal identity drifted")
    return False


async def reconcile_existing_autoresearch_formal_plan(
    plan: AutoResearchFormalPlan,
    *,
    client: Client,
) -> dict[str, Any]:
    """Bind already-started exact workflows without calling ``start_workflow``."""

    _revalidate_plan_submission_boundary(plan)
    bindings = await _describe_exact_existing_workflows(client, plan)
    branch_by_run_id = {branch.run_id: branch for branch in plan.branches}
    newly_bound: list[str] = []
    events_appended: list[str] = []
    durable: dict[str, dict[str, Any]] = {}
    async with SessionFactory() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _advisory_lock_id(plan.config_sha256)},
        )
        runs = list(
            await session.scalars(
                select(ExperimentRun)
                .where(ExperimentRun.id.in_(branch_by_run_id))
                .with_for_update()
            )
        )
        if {run.id for run in runs} != set(branch_by_run_id):
            raise ValueError("formal AutoResearch reconciliation run set is incomplete")
        repository = ExperimentRepository(session)
        for run in runs:
            branch = branch_by_run_id[run.id]
            binding = bindings[branch.branch_key]
            expected_spec = _expected_run_spec_from_existing_artifact(plan, branch, run)
            temporal_run_id = str(binding["temporal_run_id"])
            if _validate_and_bind_run(
                run,
                branch=branch,
                expected_spec=expected_spec,
                temporal_run_id=temporal_run_id,
            ):
                newly_bound.append(branch.branch_key)

            payload = _submitted_event_payload(branch, temporal_run_id)
            events = list(
                await session.scalars(
                    select(LifecycleEvent).where(
                        LifecycleEvent.aggregate_type == "run",
                        LifecycleEvent.aggregate_id == run.id,
                        LifecycleEvent.event_type
                        == "autoresearch.formal_workflow_submitted",
                    )
                )
            )
            if len(events) > 1:
                raise ValueError(
                    f"existing {branch.branch_key} formal submission event is duplicated"
                )
            if events:
                accepted = [payload]
                if branch.branch_key == "angpt1":
                    accepted.append({**payload, "canary_branch": "angpt1"})
                if events[0].payload_json not in accepted:
                    raise ValueError(
                        f"existing {branch.branch_key} formal submission event drifted"
                    )
            else:
                await repository.append_event(
                    "run",
                    run.id,
                    "autoresearch.formal_workflow_submitted",
                    ACTOR,
                    payload,
                )
                events_appended.append(branch.branch_key)
            durable[branch.branch_key] = {
                "run_id": str(run.id),
                "status": run.status,
                "workflow_id": run.temporal_workflow_id,
                "temporal_run_id": run.temporal_run_id,
                "formal_submission_key": run.formal_submission_key,
            }
    return {
        "bindings": dict(sorted(bindings.items())),
        "durable": dict(sorted(durable.items())),
        "newly_bound_branch_keys": sorted(newly_bound),
        "submission_events_appended_branch_keys": sorted(events_appended),
        "start_workflow_call_count": 0,
    }


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


async def _execute(args: argparse.Namespace) -> dict[str, Any]:
    config_path, preflight_path, receipt_dir = await asyncio.gather(
        asyncio.to_thread(args.config.resolve),
        asyncio.to_thread(args.preflight.resolve),
        asyncio.to_thread(args.receipt_dir.resolve),
    )
    plan = load_autoresearch_formal_plan(
        config_path=config_path,
        preflight_path=preflight_path,
    )
    if not args.execute:
        return {
            "schema_version": SCHEMA_VERSION,
            "executed": False,
            "inert": True,
            "config_sha256": plan.config_sha256,
            "preflight_sha256": plan.preflight_sha256,
            "release_sha256": plan.release_sha256,
        }
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    reconciliation = await reconcile_existing_autoresearch_formal_plan(plan, client=client)
    receipt: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "executed": True,
        "helper_path": str(HELPER_PATH),
        "helper_sha256": sha256_file(HELPER_PATH),
        "config_path": str(config_path),
        "config_file_sha256": sha256_file(config_path),
        "config_sha256": plan.config_sha256,
        "preflight_path": str(preflight_path),
        "preflight_file_sha256": sha256_file(preflight_path),
        "preflight_sha256": plan.preflight_sha256,
        "release_sha256": plan.release_sha256,
        "reconciliation": reconciliation,
    }
    receipt["receipt_sha256"] = sha256_json(receipt)
    receipt_path = receipt_dir / f"reconciliation-{receipt['receipt_sha256'][:16]}.json"
    _write_receipt(receipt_path, receipt)
    return {**receipt, "receipt_path": str(receipt_path)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exact reconcile already-started AutoResearch workflows without resubmission"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    result = asyncio.run(_execute(_parser().parse_args(argv)))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
