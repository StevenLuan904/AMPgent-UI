from __future__ import annotations

import argparse
import asyncio
import copy
import json
import uuid
from dataclasses import asdict
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
from pepagent.provenance.hashing import sha256_json
from pepagent.sequence_space_exploration import (
    V39ExplorationSchedule,
    build_default_v39_exploration_contract,
    build_v39_round_execution_contract,
)
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore
from pepagent.v38_persistence import (
    MultiTargetRunBindingReceipt,
    TargetBranchBinding,
    persist_multitarget_run_binding,
)

V39_ID_NAMESPACE = UUID("20cb3355-66a0-42ee-bb58-f9cafcc9bf73")
V39_WORKFLOW_TYPE = "V39SequenceSpaceExplorationWorkflow"
V39_TASK_QUEUE = "pepagent-control-v38"
V39_MEMO_KEY = "ampgent_v39_submission_identity"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML root is not an object: {path}")
    return payload


def derive_v39_schedule_run_ids(
    submission_preflight: dict[str, Any],
) -> tuple[UUID, tuple[UUID, ...]]:
    formal_key = str(submission_preflight.get("formal_submission_key", ""))
    if len(formal_key) != 64 or any(c not in "0123456789abcdef" for c in formal_key):
        raise ValueError("v39 preflight formal submission key is invalid")
    controller = uuid.uuid5(V39_ID_NAMESPACE, f"{formal_key}:controller")
    rounds = tuple(
        uuid.uuid5(V39_ID_NAMESPACE, f"{formal_key}:round:{ordinal}")
        for ordinal in range(4)
    )
    return controller, rounds


def build_v39_schedule(
    *,
    request_template: dict[str, Any],
    submission_preflight: dict[str, Any],
    controller_run_id: UUID,
    round_run_ids: tuple[UUID, ...],
) -> V39ExplorationSchedule:
    """Freeze every replay-visible identity before Temporal starts."""

    if set(request_template) & {"run_id", "exploration_round", "submission_preflight"}:
        raise ValueError("v39 request template contains a run-time identity")
    if (
        submission_preflight.get("schema_version")
        != "v39.exploration-submission-preflight.1"
        or submission_preflight.get("status") != "ready_to_submit_unique_run"
        or submission_preflight.get("execution_authorized") is not True
        or submission_preflight.get("failed_gates") != []
        or submission_preflight.get("request_template_sha256")
        != sha256_json(request_template)
    ):
        raise ValueError("v39 schedule requires a passed submission preflight template")
    contract = build_default_v39_exploration_contract()
    if len(round_run_ids) != contract.maximum_rounds:
        raise ValueError("v39 schedule requires exactly four pre-reserved child identities")
    if len(set(round_run_ids)) != len(round_run_ids):
        raise ValueError("v39 child run identities must be unique")

    rounds: list[dict[str, Any]] = []
    for ordinal, run_id in enumerate(round_run_ids):
        binding, execution = build_v39_round_execution_contract(
            contract, round_ordinal=ordinal
        )
        request = copy.deepcopy(request_template)
        request.update(
            {
                "submission_preflight": copy.deepcopy(submission_preflight),
                "run_id": str(run_id),
                "controller_run_id": str(controller_run_id),
                "execution_contract": execution.model_dump(mode="json"),
                "exploration_round": binding.model_dump(mode="json"),
            }
        )
        rounds.append(
            {
                "run_id": run_id,
                "workflow_id": (
                    f"pepagent-sequence-space-v39-round-{ordinal}-{run_id.hex}"
                ),
                "request": request,
            }
        )
    return V39ExplorationSchedule(
        controller_run_id=controller_run_id,
        exploration_contract=contract,
        rounds=tuple(rounds),
    )


def build_v39_reservation_specs(
    schedule: V39ExplorationSchedule,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    schedule_sha256 = schedule.sha256()
    controller_identity = {
        "schema_version": "v39.sequence-space-controller-reservation.1",
        "run_kind": "sequence_space_exploration_control",
        "controller_run_id": str(schedule.controller_run_id),
        "schedule_sha256": schedule_sha256,
        "exploration_contract_sha256": schedule.exploration_contract.sha256(),
        "maximum_rounds": schedule.exploration_contract.maximum_rounds,
        "expected_maximum_raw_occurrences": (
            schedule.exploration_contract.expected_maximum_raw_occurrences
        ),
        "formal_science_workflow_submitted": False,
        "historical_outputs_reused": False,
    }
    controller_key = sha256_json(controller_identity)
    controller_spec = {
        **controller_identity,
        "formal_submission_key": controller_key,
        "temporal_workflow_id": f"pepagent-sequence-space-v39-{controller_key}",
    }
    child_specs: list[dict[str, Any]] = []
    for frozen_round in schedule.rounds:
        binding = frozen_round.request["exploration_round"]
        identity = {
            "schema_version": "v39.sequence-space-round-reservation.1",
            "run_kind": "sequence_space_exploration_round",
            "controller_run_id": str(schedule.controller_run_id),
            "run_id": str(frozen_round.run_id),
            "round_ordinal": int(binding["round_ordinal"]),
            "schedule_sha256": schedule_sha256,
            "exploration_contract_sha256": binding[
                "exploration_contract_sha256"
            ],
            "execution_contract_sha256": binding["execution_contract_sha256"],
            "expected_raw_occurrences": int(binding["expected_raw_occurrences"]),
            "historical_outputs_reused": False,
        }
        child_specs.append(
            {
                **identity,
                "formal_submission_key": sha256_json(identity),
                "temporal_workflow_id": frozen_round.workflow_id,
            }
        )
    return controller_spec, tuple(child_specs)


def _target_binding(
    *, controller_run_id: UUID, request_template: dict[str, Any], panel: dict[str, Any]
) -> MultiTargetRunBindingReceipt:
    request_branches = {
        str(item["target_key"]): item
        for item in request_template["multitarget_plan_template"]["target_branches"]
    }
    bindings: list[TargetBranchBinding] = []
    for ordinal, branch in enumerate(panel["branches"], start=1):
        request_branch = request_branches.get(str(branch["target_key"]))
        if request_branch is None:
            raise ValueError("v39 schedule request does not cover the frozen target panel")
        bindings.append(
            TargetBranchBinding(
                branch_order=ordinal,
                branch_key=str(branch["target_key"]),
                target_id=UUID(str(branch["target_id"])),
                panel_role=str(request_branch["panel_role"]),
                qualification_witness_sha256=str(
                    request_branch["qualification_witness_sha256"]
                ),
                coordinate_sha256=str(branch["coordinate_sha256"]),
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
    if len(bindings) < 2:
        raise ValueError("v39 controller requires at least two target branches")
    return MultiTargetRunBindingReceipt(
        run_id=controller_run_id, branches=tuple(bindings)
    )


async def reserve_v39_schedule(
    *, schedule: V39ExplorationSchedule, panel: dict[str, Any]
) -> dict[str, Any]:
    """Atomically reserve one controller and four child runs without submitting Temporal."""

    controller_spec, child_specs = build_v39_reservation_specs(schedule)
    schedule_payload = schedule.model_dump(mode="json")
    schedule_bytes = json.dumps(
        schedule_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    object_store = await asyncio.to_thread(ContentAddressedObjectStore)
    stored = await asyncio.to_thread(
        object_store.put_bytes, schedule_bytes, "application/json"
    )
    first_target_id = UUID(str(panel["branches"][0]["target_id"]))
    all_ids = [schedule.controller_run_id, *(item.run_id for item in schedule.rounds)]
    lock_id = int.from_bytes(
        bytes.fromhex(schedule.sha256())[:8], byteorder="big", signed=True
    )
    async with SessionFactory() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
        )
        existing = list(
            await session.scalars(
                select(ExperimentRun).where(ExperimentRun.id.in_(all_ids))
            )
        )
        if existing:
            if len(existing) != len(all_ids):
                raise ValueError("v39 schedule reservation is partially present")
            by_id = {item.id: item for item in existing}
            expected = {
                schedule.controller_run_id: controller_spec,
                **{
                    frozen_round.run_id: spec
                    for frozen_round, spec in zip(
                        schedule.rounds, child_specs, strict=True
                    )
                },
            }
            if any(by_id[run_id].spec_json != spec for run_id, spec in expected.items()):
                raise ValueError("existing v39 schedule reservation drifted")
            return {
                "created": False,
                "controller_run_id": str(schedule.controller_run_id),
                "round_run_ids": [str(item.run_id) for item in schedule.rounds],
                "schedule_sha256": schedule.sha256(),
                "schedule_artifact_sha256": stored.sha256,
            }

        controller = ExperimentRun(
            id=schedule.controller_run_id,
            target_id=first_target_id,
            spec_json=controller_spec,
            spec_sha256=sha256_json(controller_spec),
            formal_submission_key=controller_spec["formal_submission_key"],
            status="created",
            temporal_workflow_id=controller_spec["temporal_workflow_id"],
        )
        session.add(controller)
        for frozen_round, spec in zip(schedule.rounds, child_specs, strict=True):
            session.add(
                ExperimentRun(
                    id=frozen_round.run_id,
                    target_id=first_target_id,
                    spec_json=spec,
                    spec_sha256=sha256_json(spec),
                    formal_submission_key=spec["formal_submission_key"],
                    status="created",
                    temporal_workflow_id=frozen_round.workflow_id,
                    parent_run_id=schedule.controller_run_id,
                )
            )
        await session.flush()
        await persist_multitarget_run_binding(
            session,
            _target_binding(
                controller_run_id=schedule.controller_run_id,
                request_template=schedule.rounds[-1].request,
                panel=panel,
            ),
        )
        await session.execute(
            postgresql_insert(Artifact)
            .values(
                id=uuid.uuid4(),
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                media_type=stored.media_type,
                storage_uri=stored.uri,
                metadata_json={
                    "role": "v39_exploration_schedule",
                    "immutable": True,
                    "controller_run_id": str(schedule.controller_run_id),
                },
            )
            .on_conflict_do_nothing(index_elements=[Artifact.sha256])
        )
        repository = ExperimentRepository(session)
        await repository.append_event(
            "run",
            schedule.controller_run_id,
            "v39.exploration_schedule_reserved",
            "v39-schedule-reservation-cli",
            {
                "schedule_sha256": schedule.sha256(),
                "schedule_artifact_sha256": stored.sha256,
                "round_run_ids": [str(item.run_id) for item in schedule.rounds],
                "temporal_workflow_submitted": False,
            },
        )
        for frozen_round in schedule.rounds:
            await repository.append_event(
                "run",
                frozen_round.run_id,
                "run.workflow_reserved",
                "v39-schedule-reservation-cli",
                {
                    "controller_run_id": str(schedule.controller_run_id),
                    "workflow_id": frozen_round.workflow_id,
                    "temporal_workflow_submitted": False,
                },
            )
    return {
        "created": True,
        "controller_run_id": str(schedule.controller_run_id),
        "round_run_ids": [str(item.run_id) for item in schedule.rounds],
        "schedule_sha256": schedule.sha256(),
        "schedule_artifact": asdict(stored),
    }


async def _start_or_recover_v39_workflow(
    client: Client, *, workflow_id: str, schedule: V39ExplorationSchedule
) -> WorkflowHandle:
    schedule_payload = schedule.model_dump(mode="json")
    identity = {
        "workflow_type": V39_WORKFLOW_TYPE,
        "schedule_sha256": schedule.sha256(),
        "controller_run_id": str(schedule.controller_run_id),
    }
    try:
        return await client.start_workflow(
            V39_WORKFLOW_TYPE,
            schedule_payload,
            id=workflow_id,
            task_queue=V39_TASK_QUEUE,
            memo={V39_MEMO_KEY: identity},
        )
    except WorkflowAlreadyStartedError as error:
        handle = client.get_workflow_handle(workflow_id)
        description = await handle.describe()
        if getattr(description, "workflow_type", None) != V39_WORKFLOW_TYPE:
            raise ValueError("existing v39 workflow type differs from reservation") from error
        memo = getattr(description, "memo", None)
        if not isinstance(memo, dict) or memo.get(V39_MEMO_KEY) != identity:
            raise ValueError("existing v39 workflow submission identity drifted") from error
        return handle


async def submit_reserved_v39_schedule(
    *, schedule: V39ExplorationSchedule
) -> dict[str, Any]:
    controller_spec, _ = build_v39_reservation_specs(schedule)
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    handle = await _start_or_recover_v39_workflow(
        client,
        workflow_id=controller_spec["temporal_workflow_id"],
        schedule=schedule,
    )
    description = await handle.describe()
    temporal_run_id = str(getattr(description, "run_id", "") or "")
    if not temporal_run_id:
        raise ValueError("v39 Temporal submission returned no run identity")
    lock_id = int.from_bytes(
        bytes.fromhex(schedule.sha256())[:8], byteorder="big", signed=True
    )
    async with SessionFactory() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
        )
        run = await session.get(ExperimentRun, schedule.controller_run_id)
        if run is None or run.spec_json != controller_spec:
            raise ValueError("v39 controller reservation is missing or drifted")
        if run.temporal_run_id not in (None, temporal_run_id):
            raise ValueError("v39 Temporal run identity drifted")
        already_recorded = (
            run.temporal_run_id == temporal_run_id and run.status == "running"
        )
        run.temporal_run_id = temporal_run_id or run.temporal_run_id
        run.status = "running"
        if not already_recorded:
            repository = ExperimentRepository(session)
            await repository.append_event(
                "run",
                run.id,
                "v39.exploration_workflow_submitted",
                "v39-schedule-reservation-cli",
                {
                    "workflow_id": controller_spec["temporal_workflow_id"],
                    "temporal_run_id": temporal_run_id,
                    "schedule_sha256": schedule.sha256(),
                    "exact_once": True,
                },
            )
    return {
        "controller_run_id": str(schedule.controller_run_id),
        "workflow_id": controller_spec["temporal_workflow_id"],
        "temporal_run_id": temporal_run_id,
        "schedule_sha256": schedule.sha256(),
        "status": "submitted_or_recovered",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Atomically reserve a frozen v39 controller and four child runs"
    )
    parser.add_argument("--request-template", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("v39 schedule reservation is inert without explicit --execute")
    request_template = _load_json(args.request_template.resolve())
    submission_preflight = _load_json(args.preflight.resolve())
    controller_run_id, round_run_ids = derive_v39_schedule_run_ids(
        submission_preflight
    )
    schedule = build_v39_schedule(
        request_template=request_template,
        submission_preflight=submission_preflight,
        controller_run_id=controller_run_id,
        round_run_ids=round_run_ids,
    )
    async def execute() -> dict[str, Any]:
        reservation = await reserve_v39_schedule(
            schedule=schedule, panel=_load_yaml(args.panel.resolve())
        )
        if not args.submit:
            return reservation
        submission = await submit_reserved_v39_schedule(schedule=schedule)
        return {"reservation": reservation, "submission": submission}

    result = asyncio.run(execute())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
