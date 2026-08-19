from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import WorkflowAlreadyStartedError

from pepagent.db.models import Artifact, ExperimentRun
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore, StoredObject
from pepagent.v38_persistence import (
    MultiTargetRunBindingReceipt,
    TargetBranchBinding,
    persist_multitarget_run_binding,
)

_WORKFLOW_TYPE = "V38SequenceFirstAgentWorkflow"
_WORKFLOW_TASK_QUEUE = "pepagent-control-v38"
_WORKFLOW_MEMO_KEY = "v38_submission_identity"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"YAML root is not an object: {path}")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _v38_controller_lock_id(controller_run_id: UUID) -> int:
    digest = bytes.fromhex(
        sha256_json(
            {
                "domain": "v38-formal-science-submission",
                "controller_run_id": str(controller_run_id),
            }
        )
    )
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _validate_v38_submission_bundle(
    *,
    request_template: dict[str, Any],
    preflight: dict[str, Any],
    controller_state: dict[str, Any],
    panel: dict[str, Any],
) -> None:
    if "run_id" in request_template or "submission_preflight" in request_template:
        raise ValueError("v38 request template already contains submission-time identity")
    if preflight.get("schema_version") != "v38.submission-preflight.1":
        raise ValueError("v38 submission preflight schema is invalid")
    if (
        preflight.get("status") != "ready_to_submit_unique_run"
        or preflight.get("execution_authorized") is not True
        or preflight.get("failed_gates") != []
    ):
        raise ValueError("v38 submission preflight is not executable")
    if preflight.get("request_template_sha256") != sha256_json(request_template):
        raise ValueError("v38 request template bytes drifted from preflight")

    controller_run_id = str(
        controller_state.get("controller_run_id") or controller_state.get("run_id")
    )
    if controller_run_id != str(preflight.get("controller_run_id")):
        raise ValueError("v38 controller run identity drifted")
    if controller_state.get("formal_science_workflow_submitted") is not False:
        raise ValueError("v38 controller already records a formal science workflow")
    if controller_state.get("candidate_generation_started") is not False:
        raise ValueError("v38 controller already records candidate generation")
    if controller_state.get("blockers", []) != []:
        raise ValueError("v38 controller has unresolved blockers")
    counts = controller_state.get("durable_counts", controller_state.get("counts", {}))
    if isinstance(counts, dict) and any(int(value) != 0 for value in counts.values()):
        raise ValueError("v38 controller has unexpected science rows")
    if controller_state.get("formal_submission_key") != preflight.get(
        "controller_formal_submission_key"
    ):
        raise ValueError("v38 controller submission identity drifted")

    formal_key = str(preflight.get("formal_submission_key", ""))
    if len(formal_key) != 64 or preflight.get("workflow_id") != (
        f"pepagent-sequence-first-v38-{formal_key}"
    ):
        raise ValueError("v38 formal workflow identity is invalid")
    if panel.get("schema_version") != "v38.target-panel.1":
        raise ValueError("v38 target panel schema is invalid")
    panel_branches = panel.get("branches")
    request_plan = request_template.get("multitarget_plan_template")
    request_branches = (
        request_plan.get("target_branches") if isinstance(request_plan, dict) else None
    )
    if not isinstance(panel_branches, list) or not isinstance(request_branches, list):
        raise ValueError("v38 target branches are absent")
    if len(panel_branches) < 2 or len(panel_branches) != len(request_branches):
        raise ValueError("v38 target branch count drifted")
    by_key = {str(item["target_key"]): item for item in request_branches}
    if len(by_key) != len(request_branches):
        raise ValueError("v38 request target keys are not unique")
    for branch in panel_branches:
        frozen = by_key.get(str(branch.get("target_key")))
        if frozen is None:
            raise ValueError("v38 request does not cover the frozen target panel")
        for panel_key, request_key in (
            ("target_id", "target_id"),
            ("target_sequence_sha256", "target_sequence_sha256"),
            ("coordinate_sha256", "coordinate_sha256"),
            ("primary_pocket_definition_sha256", "native_pocket_sha256"),
            ("wrong_pocket_definition_sha256", "wrong_pocket_sha256"),
        ):
            if str(branch.get(panel_key)) != str(frozen.get(request_key)):
                raise ValueError(f"v38 target branch binding drifted: {panel_key}")


def _same_v38_submission(
    run: ExperimentRun,
    *,
    formal_submission_key: str,
    workflow_id: str,
    spec: dict[str, Any],
) -> bool:
    immutable_keys = tuple(spec)
    return (
        run.formal_submission_key == formal_submission_key
        and run.temporal_workflow_id == workflow_id
        and all(run.spec_json.get(key) == spec[key] for key in immutable_keys)
    )


def _build_target_binding_receipt(
    *, run_id: UUID, request_template: dict[str, Any], panel: dict[str, Any]
) -> MultiTargetRunBindingReceipt:
    request_branches = {
        item["target_key"]: item
        for item in request_template["multitarget_plan_template"]["target_branches"]
    }
    bindings: list[TargetBranchBinding] = []
    for ordinal, branch in enumerate(panel["branches"], start=1):
        request_branch = request_branches[branch["target_key"]]
        bindings.append(
            TargetBranchBinding(
                branch_order=ordinal,
                branch_key=branch["target_key"],
                target_id=UUID(str(branch["target_id"])),
                panel_role=request_branch["panel_role"],
                qualification_witness_sha256=request_branch[
                    "qualification_witness_sha256"
                ],
                coordinate_sha256=branch["coordinate_sha256"],
                native_pocket_id=UUID(str(branch["primary_pocket_id"])),
                wrong_pocket_id=UUID(str(branch["wrong_pocket_id"])),
                evidence_namespace=(
                    f"target/{branch['target_key']}/{branch['target_id']}"
                ),
                metadata={
                    "target_sequence_sha256": branch["target_sequence_sha256"],
                    "native_pocket_sha256": branch[
                        "primary_pocket_definition_sha256"
                    ],
                    "wrong_pocket_sha256": branch[
                        "wrong_pocket_definition_sha256"
                    ],
                },
            )
        )
    return MultiTargetRunBindingReceipt(run_id=run_id, branches=tuple(bindings))


async def _reserve_v38_formal_run(
    session: Any,
    *,
    controller_run_id: UUID,
    formal_submission_key: str,
    workflow_id: str,
    spec: dict[str, Any],
    request_template: dict[str, Any],
    panel: dict[str, Any],
) -> tuple[ExperimentRun, bool]:
    """Reserve exactly one immutable child science run under the controller."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _v38_controller_lock_id(controller_run_id)},
    )
    controller = await session.get(ExperimentRun, controller_run_id)
    if controller is None:
        raise ValueError("v38 controller run does not exist")
    if controller.spec_json.get("run_kind") != "multitarget_sequence_first_agent_control":
        raise ValueError("v38 parent run is not the frozen controller")

    existing_children = list(
        await session.scalars(
            select(ExperimentRun).where(ExperimentRun.parent_run_id == controller_run_id)
        )
    )
    if len(existing_children) > 1:
        raise ValueError("v38 controller owns multiple formal science children")
    if existing_children:
        run = existing_children[0]
        if not _same_v38_submission(
            run,
            formal_submission_key=formal_submission_key,
            workflow_id=workflow_id,
            spec=spec,
        ):
            raise ValueError("different v38 formal science run already owns controller")
        return run, False

    first_target_id = UUID(str(panel["branches"][0]["target_id"]))
    proposed_run_id = uuid.uuid4()
    statement = (
        postgresql_insert(ExperimentRun)
        .values(
            id=proposed_run_id,
            target_id=first_target_id,
            spec_json=spec,
            spec_sha256=sha256_json(spec),
            formal_submission_key=formal_submission_key,
            status="created",
            temporal_workflow_id=workflow_id,
            parent_run_id=controller_run_id,
        )
        .on_conflict_do_nothing(index_elements=[ExperimentRun.formal_submission_key])
        .returning(ExperimentRun.id)
    )
    inserted_id = (await session.execute(statement)).scalar_one_or_none()
    run = await session.scalar(
        select(ExperimentRun).where(
            ExperimentRun.formal_submission_key == formal_submission_key
        )
    )
    if run is None or inserted_id is None or run.id != proposed_run_id:
        raise ValueError("v38 formal key is already owned outside this controller")
    repository = ExperimentRepository(session)
    await repository.append_event(
        "run", run.id, "run.created", "v38-exact-once-submission-cli", spec
    )
    await repository.append_event(
        "run",
        run.id,
        "run.workflow_reserved",
        "v38-exact-once-submission-cli",
        {"workflow_id": workflow_id, "controller_run_id": str(controller_run_id)},
    )
    await persist_multitarget_run_binding(
        session,
        _build_target_binding_receipt(
            run_id=run.id, request_template=request_template, panel=panel
        ),
    )
    return run, True


async def _register_stored_artifact(
    session: Any, *, stored: StoredObject, role: str
) -> None:
    statement = postgresql_insert(Artifact).values(
        id=uuid.uuid4(),
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        media_type=stored.media_type,
        storage_uri=stored.uri,
        metadata_json={"role": role, "immutable": True, "workflow": "v38"},
    ).on_conflict_do_nothing(index_elements=[Artifact.sha256])
    await session.execute(statement)


async def _start_or_recover_v38_workflow(
    client: Client,
    *,
    workflow_id: str,
    request: dict[str, Any],
    request_sha256: str,
    run_id: str,
    controller_run_id: str,
    formal_submission_key: str,
) -> WorkflowHandle:
    identity = {
        "workflow_type": _WORKFLOW_TYPE,
        "request_sha256": request_sha256,
        "run_id": run_id,
        "controller_run_id": controller_run_id,
        "formal_submission_key": formal_submission_key,
    }
    try:
        return await client.start_workflow(
            _WORKFLOW_TYPE,
            request,
            id=workflow_id,
            task_queue=_WORKFLOW_TASK_QUEUE,
            memo={_WORKFLOW_MEMO_KEY: identity},
        )
    except WorkflowAlreadyStartedError as error:
        handle = client.get_workflow_handle(workflow_id)
        description = await handle.describe()
        if getattr(description, "workflow_type", None) != _WORKFLOW_TYPE:
            raise ValueError("existing v38 workflow type differs from reservation") from error
        memo = getattr(description, "memo", None)
        if not isinstance(memo, dict) or memo.get(_WORKFLOW_MEMO_KEY) != identity:
            raise ValueError("existing v38 workflow submission identity drifted") from error
        return handle


async def submit_v38_once(
    *,
    request_template_path: Path,
    preflight_path: Path,
    controller_state_path: Path,
    panel_path: Path,
) -> dict[str, Any]:
    request_template = _load_json(request_template_path)
    preflight = _load_json(preflight_path)
    controller_state = _load_json(controller_state_path)
    panel = _load_yaml(panel_path)
    _validate_v38_submission_bundle(
        request_template=request_template,
        preflight=preflight,
        controller_state=controller_state,
        panel=panel,
    )
    controller_run_id = UUID(str(preflight["controller_run_id"]))
    formal_key = str(preflight["formal_submission_key"])
    workflow_id = str(preflight["workflow_id"])
    spec = {
        "schema_version": "v38.formal-science-run.1",
        "run_kind": "multitarget_sequence_first_formal_science",
        "controller_run_id": str(controller_run_id),
        "formal_submission_key": formal_key,
        "workflow_id": workflow_id,
        "request_template_sha256": preflight["request_template_sha256"],
        "submission_preflight_sha256": sha256_json(preflight),
        "benchmark_sha256": preflight["benchmark_sha256"],
        "target_panel_sha256": preflight["target_panel_sha256"],
        "worker_placement_sha256": preflight["worker_placement_sha256"],
        "worker_source_revision": preflight["sequence_worker_source_revision"],
        "worker_release_sha256": preflight["sequence_worker_release_sha256"],
        "history_snapshot_sha256": preflight["history_snapshot_sha256"],
        "history_terminal_run_count": preflight["history_terminal_run_count"],
        "historical_outputs_reused": False,
        "database_object_store_replay_required": True,
    }
    object_store = await asyncio.to_thread(ContentAddressedObjectStore)
    async with SessionFactory() as session, session.begin():
        run, created = await _reserve_v38_formal_run(
            session,
            controller_run_id=controller_run_id,
            formal_submission_key=formal_key,
            workflow_id=workflow_id,
            spec=spec,
            request_template=request_template,
            panel=panel,
        )
        run_id = str(run.id)
        request = {
            **request_template,
            "run_id": run_id,
            "controller_run_id": str(controller_run_id),
            "submission_preflight": preflight,
        }
        request_bytes = json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        request_sha256 = sha256_bytes(request_bytes)
        stored = await asyncio.to_thread(
            object_store.put_bytes, request_bytes, "application/json"
        )
        await _register_stored_artifact(
            session, stored=stored, role="v38_temporal_workflow_request"
        )
        enriched = {
            **run.spec_json,
            "workflow_request_sha256": request_sha256,
            "workflow_request_artifact": {
                "sha256": stored.sha256,
                "size_bytes": stored.size_bytes,
                "media_type": stored.media_type,
                "storage_uri": stored.uri,
            },
        }
        if created:
            run.spec_json = enriched
            run.spec_sha256 = sha256_json(enriched)
        elif run.spec_json != enriched:
            raise ValueError("recovered v38 workflow request artifact drifted")

    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    handle = await _start_or_recover_v38_workflow(
        client,
        workflow_id=workflow_id,
        request=request,
        request_sha256=request_sha256,
        run_id=run_id,
        controller_run_id=str(controller_run_id),
        formal_submission_key=formal_key,
    )
    description = await handle.describe()
    temporal_run_id = str(getattr(description, "run_id", "") or "")
    async with SessionFactory() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _v38_controller_lock_id(controller_run_id)},
        )
        run = await session.get(ExperimentRun, UUID(run_id))
        controller = await session.get(ExperimentRun, controller_run_id)
        if run is None or controller is None:
            raise RuntimeError("v38 reserved run identity disappeared")
        if run.temporal_run_id not in (None, temporal_run_id):
            raise ValueError("v38 Temporal run identity drifted")
        run.temporal_run_id = temporal_run_id or run.temporal_run_id
        run.status = "running"
        controller_spec = dict(controller.spec_json)
        if controller_spec.get("formal_science_workflow_submitted") is True:
            if controller_spec.get("science_run_id") != run_id:
                raise ValueError("v38 controller already points at another science run")
        else:
            controller_spec.update(
                {
                    "formal_science_workflow_submitted": True,
                    "candidate_generation_started": True,
                    "science_run_id": run_id,
                    "science_workflow_id": workflow_id,
                    "science_temporal_run_id": temporal_run_id,
                    "status": "formal_science_workflow_running",
                }
            )
            controller.spec_json = controller_spec
            controller.spec_sha256 = sha256_json(controller_spec)
            repository = ExperimentRepository(session)
            await repository.append_event(
                "run",
                run.id,
                "run.workflow_submitted",
                "v38-exact-once-submission-cli",
                {"workflow_id": workflow_id, "temporal_run_id": temporal_run_id},
            )
            await repository.append_event(
                "run",
                controller_run_id,
                "v38.formal_science_workflow_submitted",
                "v38-exact-once-submission-cli",
                {
                    "science_run_id": run_id,
                    "workflow_id": workflow_id,
                    "temporal_run_id": temporal_run_id,
                    "formal_submission_key": formal_key,
                },
            )
    controller_state.update(
        {
            "formal_science_workflow_submitted": True,
            "candidate_generation_started": True,
            "science_run_id": run_id,
            "science_workflow_id": workflow_id,
            "science_temporal_run_id": temporal_run_id,
            "status": "formal_science_workflow_running",
        }
    )
    await asyncio.to_thread(_atomic_json, controller_state_path, controller_state)
    return {
        "run_id": run_id,
        "controller_run_id": str(controller_run_id),
        "workflow_id": workflow_id,
        "temporal_run_id": temporal_run_id,
        "formal_submission_key": formal_key,
        "request_sha256": request_sha256,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit the unique v38 formal science run")
    parser.add_argument("--request-template", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--controller-state", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("v38 submission is inert without explicit --execute")
    result = asyncio.run(
        submit_v38_once(
            request_template_path=args.request_template.resolve(),
            preflight_path=args.preflight.resolve(),
            controller_state_path=args.controller_state.resolve(),
            panel_path=args.panel.resolve(),
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
