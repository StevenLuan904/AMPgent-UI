from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import WorkflowAlreadyStartedError

from pepagent.db.models import Artifact, ExperimentRun, Target
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.settings import get_settings
from pepagent.seven_branch_design import (
    SevenBranchDesignContract,
    SevenBranchDesignSchedule,
)
from pepagent.seven_branch_schedule import (
    build_initial_seven_branch_schedule,
    derive_initial_seven_branch_run_ids,
)
from pepagent.storage.object_store import ContentAddressedObjectStore

WORKFLOW_TYPE = "SevenBranchPeptideDesignWorkflow"
TASK_QUEUE = "pepagent-control-v38"
MEMO_KEY = "ampgent_seven_branch_submission_identity"
TARGET_ID_NAMESPACE = UUID("f9617cc4-803a-42c3-bb18-e027f9953a58")


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def build_seven_branch_reservation_specs(
    schedule: SevenBranchDesignSchedule,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    schedule_sha256 = schedule.sha256()
    controller_identity = {
        "schema_version": "ampgent.seven-branch-controller-reservation.1",
        "run_kind": "seven_branch_peptide_design_control",
        "controller_run_id": str(schedule.controller_run_id),
        "schedule_sha256": schedule_sha256,
        "design_contract_sha256": schedule.design_contract.sha256(),
        "delivery_quota": sum(
            item.requested_delivery_count for item in schedule.design_contract.branches
        ),
        "formal_science_workflow_submitted": False,
        "historical_outputs_reused": False,
    }
    controller_key = sha256_json(controller_identity)
    controller_spec = {
        **controller_identity,
        "formal_submission_key": controller_key,
        "temporal_workflow_id": f"pepagent-seven-branch-design-{controller_key}",
    }
    contract_branch_by_key = {
        item.branch_key: item for item in schedule.design_contract.branches
    }
    child_specs: list[dict[str, Any]] = []
    for frozen_round in schedule.rounds:
        binding = frozen_round.request["seven_branch_round"]
        identity = {
            "schema_version": "ampgent.seven-branch-round-reservation.1",
            "run_kind": "seven_branch_design_round",
            "controller_run_id": str(schedule.controller_run_id),
            "run_id": str(frozen_round.run_id),
            "branch_key": str(binding["branch_key"]),
            "branch_kind": str(binding["branch_kind"]),
            "round_ordinal": int(binding["round_ordinal"]),
            "schedule_sha256": schedule_sha256,
            "design_contract_sha256": binding["design_contract_sha256"],
            "execution_contract_sha256": binding["execution_contract_sha256"],
            "expected_raw_occurrences": int(binding["expected_raw_occurrences"]),
            "requested_delivery_count": contract_branch_by_key[
                str(binding["branch_key"])
            ].requested_delivery_count,
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


async def _ensure_targets(
    session: Any, target_manifest: dict[str, Any]
) -> dict[str, Target]:
    targets = target_manifest.get("targets")
    if not isinstance(targets, list) or len(targets) != 6:
        raise ValueError("seven-branch target manifest must contain six targets")
    by_key: dict[str, Target] = {}
    for item in targets:
        target_id = uuid.uuid5(TARGET_ID_NAMESPACE, str(item["sequence_sha256"]))
        await session.execute(
            postgresql_insert(Target)
            .values(
                id=target_id,
                name=f"{item['target_key']} {item['protein_accession']}",
                organism=item["organism"],
                accession=item["protein_accession"],
                sequence=item["sequence"],
                sequence_sha256=item["sequence_sha256"],
                metadata_json={
                    "target_key": item["target_key"],
                    "source_uri": item["source_uri"],
                    "source_type": item["source_type"],
                    "partial": item["partial"],
                    "manifest_schema_version": target_manifest["schema_version"],
                },
            )
            .on_conflict_do_nothing(index_elements=[Target.sequence_sha256])
        )
        target = await session.scalar(
            select(Target).where(Target.sequence_sha256 == item["sequence_sha256"])
        )
        if target is None or target.sequence != item["sequence"]:
            raise ValueError("registered target sequence identity drifted")
        by_key[str(item["target_key"])] = target
    return by_key


async def reserve_seven_branch_schedule(
    *, schedule: SevenBranchDesignSchedule, target_manifest: dict[str, Any]
) -> dict[str, Any]:
    controller_spec, child_specs = build_seven_branch_reservation_specs(schedule)
    schedule_bytes = json.dumps(
        schedule.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    object_store = await asyncio.to_thread(ContentAddressedObjectStore)
    stored = await asyncio.to_thread(
        object_store.put_bytes, schedule_bytes, "application/json"
    )
    all_ids = [schedule.controller_run_id, *(item.run_id for item in schedule.rounds)]
    lock_id = int.from_bytes(
        bytes.fromhex(schedule.sha256())[:8], byteorder="big", signed=True
    )
    async with SessionFactory() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
        )
        existing = list(
            await session.scalars(select(ExperimentRun).where(ExperimentRun.id.in_(all_ids)))
        )
        if existing:
            if len(existing) != len(all_ids):
                raise ValueError("seven-branch schedule reservation is partially present")
            by_id = {item.id: item for item in existing}
            expected = {schedule.controller_run_id: controller_spec}
            for frozen, spec in zip(schedule.rounds, child_specs, strict=True):
                stored = by_id[frozen.run_id]
                expected[frozen.run_id] = {
                    **spec,
                    "target_id": str(stored.target_id),
                    "target_binding_role": (
                        "frozen_target_sequence"
                        if spec["branch_key"] in schedule.target_runtime_by_key
                        else "relational_anchor_only"
                    ),
                }
            if any(item.spec_json != expected[item.id] for item in existing):
                raise ValueError("existing seven-branch reservation drifted")
            return {
                "created": False,
                "controller_run_id": str(schedule.controller_run_id),
                "child_run_ids": [str(item.run_id) for item in schedule.rounds],
                "schedule_sha256": schedule.sha256(),
                "schedule_artifact_sha256": stored.sha256,
            }
        targets = await _ensure_targets(session, target_manifest)
        anchor = targets["acea"]
        session.add(
            ExperimentRun(
                id=schedule.controller_run_id,
                target_id=anchor.id,
                spec_json=controller_spec,
                spec_sha256=sha256_json(controller_spec),
                formal_submission_key=controller_spec["formal_submission_key"],
                status="created",
                temporal_workflow_id=controller_spec["temporal_workflow_id"],
            )
        )
        for frozen, spec in zip(schedule.rounds, child_specs, strict=True):
            branch_key = spec["branch_key"]
            target = targets.get(branch_key, anchor)
            session.add(
                ExperimentRun(
                    id=frozen.run_id,
                    target_id=target.id,
                    spec_json={
                        **spec,
                        "target_id": str(target.id),
                        "target_binding_role": (
                            "frozen_target_sequence"
                            if branch_key in targets
                            else "relational_anchor_only"
                        ),
                    },
                    spec_sha256=sha256_json(
                        {
                            **spec,
                            "target_id": str(target.id),
                            "target_binding_role": (
                                "frozen_target_sequence"
                                if branch_key in targets
                                else "relational_anchor_only"
                            ),
                        }
                    ),
                    formal_submission_key=spec["formal_submission_key"],
                    status="created",
                    temporal_workflow_id=frozen.workflow_id,
                    parent_run_id=schedule.controller_run_id,
                )
            )
        await session.flush()
        await session.execute(
            postgresql_insert(Artifact)
            .values(
                id=uuid.uuid4(),
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                media_type=stored.media_type,
                storage_uri=stored.uri,
                metadata_json={
                    "role": "seven_branch_design_schedule",
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
            "seven_branch.schedule_reserved",
            "seven-branch-reservation-cli",
            {
                "schedule_sha256": schedule.sha256(),
                "schedule_artifact_sha256": stored.sha256,
                "child_run_ids": [str(item.run_id) for item in schedule.rounds],
                "temporal_workflow_submitted": False,
            },
        )
    return {
        "created": True,
        "controller_run_id": str(schedule.controller_run_id),
        "child_run_ids": [str(item.run_id) for item in schedule.rounds],
        "schedule_sha256": schedule.sha256(),
        "schedule_artifact": asdict(stored),
    }


async def _start_or_recover(
    client: Client, *, workflow_id: str, schedule: SevenBranchDesignSchedule
) -> WorkflowHandle:
    identity = {
        "workflow_type": WORKFLOW_TYPE,
        "schedule_sha256": schedule.sha256(),
        "controller_run_id": str(schedule.controller_run_id),
    }
    try:
        return await client.start_workflow(
            WORKFLOW_TYPE,
            schedule.model_dump(mode="json"),
            id=workflow_id,
            task_queue=TASK_QUEUE,
            memo={MEMO_KEY: identity},
        )
    except WorkflowAlreadyStartedError as error:
        handle = client.get_workflow_handle(workflow_id)
        description = await handle.describe()
        if getattr(description, "workflow_type", None) != WORKFLOW_TYPE:
            raise ValueError("existing seven-branch workflow type drifted") from error
        memo = getattr(description, "memo", None)
        if not isinstance(memo, dict) or memo.get(MEMO_KEY) != identity:
            raise ValueError("existing seven-branch submission identity drifted") from error
        return handle


async def submit_reserved_seven_branch_schedule(
    *, schedule: SevenBranchDesignSchedule
) -> dict[str, Any]:
    controller_spec, _ = build_seven_branch_reservation_specs(schedule)
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    handle = await _start_or_recover(
        client,
        workflow_id=controller_spec["temporal_workflow_id"],
        schedule=schedule,
    )
    description = await handle.describe()
    temporal_run_id = str(getattr(description, "run_id", "") or "")
    if not temporal_run_id:
        raise ValueError("seven-branch Temporal submission returned no run identity")
    lock_id = int.from_bytes(
        bytes.fromhex(schedule.sha256())[:8], byteorder="big", signed=True
    )
    async with SessionFactory() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
        )
        run = await session.get(ExperimentRun, schedule.controller_run_id)
        if run is None or run.spec_json != controller_spec:
            raise ValueError("seven-branch controller reservation is missing or drifted")
        if run.temporal_run_id not in (None, temporal_run_id):
            raise ValueError("seven-branch Temporal run identity drifted")
        already_recorded = run.temporal_run_id == temporal_run_id and run.status == "running"
        run.temporal_run_id = temporal_run_id
        run.status = "running"
        if not already_recorded:
            repository = ExperimentRepository(session)
            await repository.append_event(
                "run",
                run.id,
                "seven_branch.workflow_submitted",
                "seven-branch-reservation-cli",
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
        description="Reserve and exact-once submit the initial seven-branch epoch"
    )
    parser.add_argument("--request-template", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--design-contract", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("seven-branch reservation is inert without explicit --execute")
    request_template = _load_json(args.request_template.resolve())
    preflight = _load_json(args.preflight.resolve())
    contract = SevenBranchDesignContract.model_validate(
        _load_json(args.design_contract.resolve())
    )
    target_manifest_path = args.target_manifest.resolve()
    target_manifest = _load_json(target_manifest_path)
    controller_id, child_ids = derive_initial_seven_branch_run_ids(preflight)
    schedule = build_initial_seven_branch_schedule(
        request_template=request_template,
        submission_preflight=preflight,
        design_contract=contract,
        target_manifest=target_manifest,
        target_manifest_sha256=sha256_file(target_manifest_path),
        controller_run_id=controller_id,
        child_run_ids=child_ids,
    )

    async def execute() -> dict[str, Any]:
        reservation = await reserve_seven_branch_schedule(
            schedule=schedule, target_manifest=target_manifest
        )
        if not args.submit:
            return reservation
        submission = await submit_reserved_seven_branch_schedule(schedule=schedule)
        return {"reservation": reservation, "submission": submission}

    print(
        json.dumps(
            asyncio.run(execute()), ensure_ascii=False, indent=2, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
