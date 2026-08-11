from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import uuid
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from temporalio import activity

from pepagent.db.models import (
    Artifact,
    Candidate,
    Evaluation,
    ExperimentRun,
    LifecycleEvent,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.developability import CANONICAL_AMINO_ACIDS
from pepagent.domain.enums import CandidateStatus, EvaluationStatus, RunStatus
from pepagent.evidence_replay import build_database_evidence_graph
from pepagent.handoff_metrics import (
    HANDOFF_METRIC_VERSION,
    METRIC_PLUGIN_CONTRACTS,
    normalize_metric_records,
    physicochemical_descriptors,
)
from pepagent.model_workers.sequence_metric_plan import (
    build_external_metric_plan,
    consume_external_metric_result,
    load_external_metric_adapter,
    materialize_external_metric_input,
)
from pepagent.provenance.environment import fingerprint_runtime
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore
from pepagent.v34_external_adapters import (
    build_knowledge_command,
)
from pepagent.v37_attempt_ledger import (
    V37AttemptContext,
    build_v37_attempt_artifacts,
    execute_v37_durable_attempt,
)
from pepagent.v37_evidence import build_v37_evidence_plan
from pepagent.v37_persistence import (
    _selection_witness_payloads,
    persist_v37_agent_decision,
    persist_v37_dependencies,
    persist_v37_proposal_events,
    persist_v37_tool_result,
    proposal_occurrence_payload,
)
from pepagent.v37_persistence import (
    validate_v37_database_object_replay as _validate_v37_replay_graph,
)
from pepagent.v37_preregistration import V37Manifest
from pepagent.v37_provider_consumers import (
    build_v37_pepshot_inspect_request,
    consume_v37_pepshot_inspection,
)
from pepagent.v37_runtime_execution import (
    V37GenericRuntimeExpectation,
    V37GenericRuntimePaths,
    V37LiveRuntimePaths,
    run_v37_guarded_provider_subprocess,
    run_v37_guarded_subprocess,
)
from pepagent.v37_runtime_manifests import V37GeneratorRuntimeExpectation
from pepagent.v37_selection import select_v37_lanes
from pepagent.workers.activities import (
    _register_artifact,
    _store_json,
    predict_boltz2_complex,
    score_rosetta_complex,
)

V37_ACTIVITY_VERSION = "v37.0.0"


@activity.defn(name="evaluate_v37_sequence_metric")
async def evaluate_v37_sequence_metric(request: dict[str, Any]) -> dict[str, Any]:
    plugin_name = str(request["plugin"]["name"])
    context = V37AttemptContext(
        run_id=uuid.UUID(request["run_id"]),
        logical_id=f"v37:metric:{plugin_name}",
        activity_name="evaluate_v37_sequence_metric",
        attempt=activity.info().attempt,
    )

    async def operation() -> dict[str, Any]:
        plugin = request["plugin"]
        contract = METRIC_PLUGIN_CONTRACTS[plugin_name]
        if contract["provider"] == "builtin":
            parameters = plugin.get("parameters", {})
            raw_rows = [
                {
                    "candidate_id": candidate["id"],
                    "sequence": candidate["sequence"],
                    "status": "complete",
                    **physicochemical_descriptors(
                        candidate["sequence"],
                        ph=float(parameters.get("ph", 7.4)),
                        c_terminal_amidated=bool(
                            parameters.get("c_terminal_amidated", False)
                        ),
                        hydrophobic_moment_angle=int(
                            parameters.get("hydrophobic_moment_angle", 100)
                        ),
                    ),
                }
                for candidate in request["candidates"]
            ]
            result = {
                "plugin": plugin,
                "contract": contract,
                "candidate_count": len(request["candidates"]),
                "status": "complete",
                "adapter_version": HANDOFF_METRIC_VERSION,
                "records": normalize_metric_records(plugin_name, raw_rows),
                "raw_rows": raw_rows,
            }
            environment_sha256, environment = fingerprint_runtime()
            raw_artifact = await _store_json(result)
            environment_artifact = await _store_json(environment)
            return {
                "result": result,
                "provenance": {
                    "tool_name": f"handoff-metric-{plugin_name}",
                    "tool_version": HANDOFF_METRIC_VERSION,
                    "model_uri": f"metric://{plugin_name}",
                    "weights_sha256": None,
                    "environment_sha256": environment_sha256,
                    "environment": environment,
                    "attempt": activity.info().attempt,
                    "raw_output_artifact": asdict(raw_artifact),
                    "environment_artifact": asdict(environment_artifact),
                    "execution_mode": "builtin_no_subprocess",
                },
            }

        registry_path = Path(str(plugin["registry_path"]))
        adapter, registry_sha256 = load_external_metric_adapter(
            registry_path, plugin_name
        )
        if not adapter or adapter.get("enabled") is not True:
            raise ValueError(f"v37 required metric adapter is absent: {plugin_name}")
        if registry_sha256 != plugin.get("registry_sha256"):
            raise ValueError("v37 metric registry identity differs from frozen runtime")
        settings = get_settings()
        work = (
            Path(settings.work_root)
            / request["run_id"]
            / "v37"
            / "metrics"
            / plugin_name
        )
        await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
        plan = build_external_metric_plan(
            plugin_name=plugin_name,
            adapter=adapter,
            work_dir=work,
            run_id=request["run_id"],
            registry_path=registry_path,
            registry_sha256=registry_sha256,
        )
        input_receipt = await asyncio.to_thread(
            materialize_external_metric_input, plan, request["candidates"]
        )
        environment = os.environ.copy()
        environment.update(plan["environment"])
        stdout, launch_receipt = await _run_guarded_generic_runtime(
            command=list(plan["command_argv"]),
            runtime=plugin,
            context=context,
            cwd=Path(plan["working_directory"]) if plan.get("working_directory") else work,
            env=environment,
            input_paths={
                "candidate_input": Path(plan["input"]["candidates_csv"]),
                "metric_registry": registry_path,
            },
        )
        provider_result = consume_external_metric_result(
            plan=plan,
            candidates=request["candidates"],
            execution_receipt={
                "status": "completed",
                "returncode": launch_receipt["returncode"],
                "stdout": stdout,
                "stderr": launch_receipt["stderr_tail"],
                "command_argv": list(plan["command_argv"]),
            },
        )
        result = {
            "plugin": plugin,
            "contract": contract,
            "candidate_count": len(request["candidates"]),
            **provider_result,
        }
        environment_sha256 = launch_receipt["byte_identity_sha256"]
        raw_artifact = await _store_json(result)
        environment_artifact = await _store_json(
            {
                "execution_plan": plan,
                "input_receipt": input_receipt,
                "launch_receipt": launch_receipt,
            }
        )
        return {
            "result": result,
            "provenance": {
                "tool_name": f"handoff-metric-{plugin_name}",
                "tool_version": result.get("adapter_version") or HANDOFF_METRIC_VERSION,
                "model_uri": result.get("model_uri") or f"metric://{plugin_name}",
                "weights_sha256": result.get("weights_sha256"),
                "environment_sha256": environment_sha256,
                "environment": {"execution_plan": plan},
                "attempt": activity.info().attempt,
                "raw_output_artifact": asdict(raw_artifact),
                "environment_artifact": asdict(environment_artifact),
                "execution_plan": plan,
                "input_receipt": input_receipt,
                "live_launch_receipt": launch_receipt,
            },
        }

    return await execute_v37_durable_attempt(operation, context=context)


@activity.defn(name="predict_v37_boltz2_complex")
async def predict_v37_boltz2_complex(request: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(request["candidate"]["id"])
    seed = int(request["seed"])
    context = V37AttemptContext(
        run_id=uuid.UUID(request["run_id"]),
        logical_id=f"v37:physical:boltz:{candidate_id}:{seed}",
        activity_name="predict_v37_boltz2_complex",
        attempt=activity.info().attempt,
    )

    async def operation() -> dict[str, Any]:
        return await predict_boltz2_complex(request)

    return await execute_v37_durable_attempt(operation, context=context)


@activity.defn(name="score_v37_rosetta_complex")
async def score_v37_rosetta_complex(request: dict[str, Any]) -> dict[str, Any]:
    structure_call_id = str(request["structure"]["tool_call_id"])
    context = V37AttemptContext(
        run_id=uuid.UUID(request["run_id"]),
        logical_id=f"v37:physical:rosetta:{structure_call_id}",
        activity_name="score_v37_rosetta_complex",
        attempt=activity.info().attempt,
    )

    async def operation() -> dict[str, Any]:
        return await score_rosetta_complex(request)

    return await execute_v37_durable_attempt(operation, context=context)


def _v37_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    return build_v37_evidence_plan(V37Manifest.model_validate(manifest))


async def _durable_attempt_artifacts(
    *, run_id: uuid.UUID, logical_id: str
) -> dict[str, dict[str, Any]]:
    async with SessionFactory() as ledger_session:
        rows = list(
            await ledger_session.scalars(
                select(LifecycleEvent).where(
                    LifecycleEvent.aggregate_type == "v37_attempt",
                    LifecycleEvent.payload_json["run_id"].astext == str(run_id),
                    LifecycleEvent.payload_json["v37_logical_id"].astext == logical_id,
                )
            )
        )
    events = [
        {"event_type": item.event_type, "payload_json": item.payload_json}
        for item in rows
    ]
    return build_v37_attempt_artifacts(events, logical_id=logical_id)


async def _persist_v37_node(
    session: Any,
    *,
    run_id: uuid.UUID,
    manifest: dict[str, Any],
    logical_id: str,
    environment_sha256: str,
    input_payload: dict[str, Any],
    parameters: dict[str, Any],
    output_payload: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
    model_uri: str | None = None,
    weights_sha256: str | None = None,
    random_seed: int | None = None,
) -> ToolCall:
    ledgers = await _durable_attempt_artifacts(run_id=run_id, logical_id=logical_id)
    return await persist_v37_tool_result(
        session,
        run_id=run_id,
        plan=_v37_plan(manifest),
        logical_id=logical_id,
        environment_sha256=environment_sha256,
        input_payload=input_payload,
        parameters=parameters,
        output_payload=output_payload,
        artifact_payloads_by_role={**artifacts, **ledgers},
        artifact_writer=_store_json,
        model_uri=model_uri,
        weights_sha256=weights_sha256,
        random_seed=random_seed,
    )


async def _persist_v37_stop(
    session: Any, *, run_id: uuid.UUID, logical_id: str, stop_reason: str
) -> dict[str, Any]:
    payload = {"v37_logical_id": logical_id, "stop_reason": stop_reason}
    existing = await session.scalar(
        select(LifecycleEvent).where(
            LifecycleEvent.aggregate_type == "run",
            LifecycleEvent.aggregate_id == run_id,
            LifecycleEvent.event_type == "v37.stage_stopped",
            LifecycleEvent.payload_json["v37_logical_id"].astext == logical_id,
        )
    )
    if existing is not None:
        if existing.payload_json != payload:
            raise ValueError("v37 stage stop evidence differs on retry")
        return payload
    await ExperimentRepository(session).append_event(
        "run", run_id, "v37.stage_stopped", "v37-formal-evidence", payload
    )
    return payload


async def _persist_v37_launch_receipt(
    *, context: V37AttemptContext, receipt: dict[str, Any]
) -> None:
    stored = await _store_json(receipt)
    async with SessionFactory() as session, session.begin():
        artifact = await session.scalar(
            select(Artifact).where(Artifact.sha256 == stored.sha256)
        )
        if artifact is None:
            artifact = Artifact(
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                media_type=stored.media_type,
                storage_uri=stored.uri,
                metadata_json={"v37_role": "live_launch_receipt"},
            )
            session.add(artifact)
            await session.flush()
        payload = {
            "run_id": str(context.run_id),
            "v37_logical_id": context.logical_id,
            "activity_name": context.activity_name,
            "attempt": context.attempt,
            "artifact_sha256": stored.sha256,
            "launch_receipt_sha256": receipt["launch_receipt_sha256"],
        }
        existing = await session.scalar(
            select(LifecycleEvent).where(
                LifecycleEvent.aggregate_type == "v37_attempt",
                LifecycleEvent.aggregate_id == context.aggregate_id,
                LifecycleEvent.event_type == "v37.launch_receipt_persisted",
            )
        )
        if existing is None:
            await ExperimentRepository(session).append_event(
                "v37_attempt",
                context.aggregate_id,
                "v37.launch_receipt_persisted",
                "v37-formal-runtime",
                payload,
            )
        elif existing.payload_json != payload:
            raise ValueError("v37 launch receipt differs on retry")


async def _persist_v37_aggregate_launch_receipt(
    *, context: V37AttemptContext, receipt: dict[str, Any]
) -> None:
    stored = await _store_json(receipt)
    async with SessionFactory() as session, session.begin():
        artifact = await session.scalar(select(Artifact).where(Artifact.sha256 == stored.sha256))
        if artifact is None:
            artifact = Artifact(
                sha256=stored.sha256,
                size_bytes=stored.size_bytes,
                media_type=stored.media_type,
                storage_uri=stored.uri,
                metadata_json={"v37_role": "aggregate_live_launch_receipt"},
            )
            session.add(artifact)
            await session.flush()
        payload = {
            "run_id": str(context.run_id),
            "v37_logical_id": context.logical_id,
            "activity_name": context.activity_name,
            "attempt": context.attempt,
            "artifact_sha256": stored.sha256,
            "launch_receipt_sha256": receipt["launch_receipt_sha256"],
            "all_boundaries_match": receipt["all_boundaries_match"],
        }
        existing = await session.scalar(
            select(LifecycleEvent).where(
                LifecycleEvent.aggregate_type == "v37_attempt",
                LifecycleEvent.aggregate_id == context.aggregate_id,
                LifecycleEvent.event_type
                == "v37.aggregate_launch_receipt_persisted",
            )
        )
        if existing is None:
            await ExperimentRepository(session).append_event(
                "v37_attempt",
                context.aggregate_id,
                "v37.aggregate_launch_receipt_persisted",
                "v37-formal-runtime",
                payload,
            )
        elif existing.payload_json != payload:
            raise ValueError("v37 aggregate launch receipt differs on retry")


async def _run_guarded_generic_runtime(
    *,
    command: list[str],
    runtime: dict[str, Any],
    context: V37AttemptContext,
    cwd: Path,
    env: dict[str, str] | None = None,
    input_paths: dict[str, Path] | None = None,
) -> tuple[str, dict[str, Any]]:
    guard = runtime.get("execution_guard")
    if not isinstance(guard, dict):
        raise ValueError("v37 provider runtime lacks a frozen execution guard")
    contract = guard.get("contract")
    expectation_payload = guard.get("expectation")
    paths_payload = guard.get("paths")
    if not all(isinstance(item, dict) for item in (contract, expectation_payload, paths_payload)):
        raise ValueError("v37 provider execution guard is incomplete")
    expectation = V37GenericRuntimeExpectation(**expectation_payload)
    paths = V37GenericRuntimePaths(
        executable_path=Path(paths_payload["executable_path"]),
        runtime_manifest_path=Path(paths_payload["runtime_manifest_path"]),
        packages_lock_path=Path(paths_payload["packages_lock_path"]),
        source_root=Path(paths_payload["source_root"]),
        model_root=Path(paths_payload["model_root"]),
        adapter_path=(
            Path(paths_payload["adapter_path"])
            if paths_payload.get("adapter_path") is not None
            else None
        ),
    )

    async def receipt_writer(receipt: dict[str, Any]) -> None:
        await _persist_v37_launch_receipt(context=context, receipt=receipt)

    async def aggregate_receipt_writer(receipt: dict[str, Any]) -> None:
        await _persist_v37_aggregate_launch_receipt(context=context, receipt=receipt)

    async def progress_writer() -> None:
        activity.heartbeat(
            {
                "v37_logical_id": context.logical_id,
                "attempt": context.attempt,
                "status": "guarded_provider_subprocess_running",
            }
        )

    return await run_v37_guarded_provider_subprocess(
        command,
        contract=contract,
        expectation=expectation,
        paths=paths,
        receipt_writer=receipt_writer,
        aggregate_receipt_writer=aggregate_receipt_writer,
        progress_writer=progress_writer,
        cwd=cwd,
        env=env,
        input_paths=input_paths,
    )


async def _persist_v37_committed_runtime_receipt(
    session: Any,
    *,
    run_id: uuid.UUID,
    tool_call_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    complete_payload = {"tool_call_id": str(tool_call_id), **payload}
    existing = await session.scalar(
        select(LifecycleEvent).where(
            LifecycleEvent.aggregate_type == "run",
            LifecycleEvent.aggregate_id == run_id,
            LifecycleEvent.event_type == "v37.runtime_receipts.committed",
            LifecycleEvent.payload_json["tool_call_id"].astext == str(tool_call_id),
        )
    )
    if existing is None:
        await ExperimentRepository(session).append_event(
            "run",
            run_id,
            "v37.runtime_receipts.committed",
            "v37-formal-runtime",
            complete_payload,
        )
    elif existing.payload_json != complete_payload:
        raise ValueError("v37 committed runtime receipt differs on retry")


async def _get_or_create_pending_v37_replay_call(
    session: Any,
    *,
    run_id: uuid.UUID,
    final_call_id: uuid.UUID,
    plan_sha256: str,
    attempt: int,
) -> ToolCall:
    input_payload = {
        "v37_logical_id": "v37:replay",
        "payload": {"final_portfolio_tool_call_id": str(final_call_id)},
    }
    parameters = {"v37_plan_sha256": plan_sha256, "database_only": True}
    environment_sha256 = sha256_json({"replay": "database-only-v37"})
    input_sha256 = sha256_json(input_payload)
    idempotency_key = sha256_json(
        {
            "run_id": str(run_id),
            "tool_name": "v37-replay",
            "tool_version": V37_ACTIVITY_VERSION,
            "environment_sha256": environment_sha256,
            "weights_sha256": None,
            "input_sha256": input_sha256,
            "parameters": parameters,
            "random_seed": None,
        }
    )
    existing = await session.scalar(
        select(ToolCall).where(ToolCall.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if (
            existing.run_id != run_id
            or existing.input_json != input_payload
            or existing.parameters_json != parameters
            or existing.status not in {EvaluationStatus.RUNNING, EvaluationStatus.SUCCEEDED}
        ):
            raise ValueError("persisted v37 replay call differs on retry")
        return existing
    now = datetime.now(UTC)
    call = ToolCall(
        run_id=run_id,
        tool_name="v37-replay",
        tool_version=V37_ACTIVITY_VERSION,
        model_uri="deterministic://v37-database-object-replay",
        environment_sha256=environment_sha256,
        idempotency_key=idempotency_key,
        input_sha256=input_sha256,
        input_json=input_payload,
        parameters_json=parameters,
        status=EvaluationStatus.RUNNING,
        attempt=attempt,
        queued_at=now,
        started_at=now,
    )
    session.add(call)
    await session.flush()
    return call


async def _complete_v37_replay_call(
    session: Any, *, call: ToolCall, output_payload: dict[str, Any]
) -> None:
    output_sha256 = sha256_json(output_payload)
    if call.status == EvaluationStatus.SUCCEEDED:
        if call.output_sha256 != output_sha256:
            raise ValueError("persisted v37 replay output differs on retry")
        return
    if call.status != EvaluationStatus.RUNNING or call.output_sha256 is not None:
        raise ValueError("v37 replay call is not in a completable state")
    call.status = EvaluationStatus.SUCCEEDED
    call.finished_at = datetime.now(UTC)
    call.output_sha256 = output_sha256
    await ExperimentRepository(session).append_event(
        "run",
        call.run_id,
        "tool_call.succeeded",
        call.tool_name,
        {
            "tool_call_id": str(call.id),
            "idempotency_key": call.idempotency_key,
            "input_sha256": call.input_sha256,
            "output_sha256": output_sha256,
        },
    )


def _select_v37_coordinate_artifact(structure: dict[str, Any]) -> dict[str, Any]:
    artifacts = structure["provenance"]["engine_artifacts"]
    coordinates = [
        item
        for item in artifacts
        if Path(item["path"]).suffix.lower() in {".cif", ".pdb"}
    ]
    preferred = [
        item for item in coordinates if "model_0" in Path(item["path"]).name
    ]
    if not (preferred or coordinates):
        raise ValueError("v37 PepShot inspect requires a persisted coordinate artifact")
    return sorted(preferred or coordinates, key=lambda item: item["path"])[0]


def _generator_command(
    engine: dict[str, Any], runtime: dict[str, Any], request_path: Path, output_path: Path
) -> list[str]:
    base = [
        str(runtime["python_path"]),
        str(runtime["adapter_path"]),
        "--request",
        str(request_path),
        "--output",
        str(output_path),
    ]
    generator = engine["generator_id"]
    if generator == "hydramp":
        return [
            *base,
            "--model-path",
            str(runtime["model_path"]),
            "--decomposer-path",
            str(runtime["decomposer_path"]),
            "--model-archive",
            str(runtime["model_archive_path"]),
        ]
    if generator == "ampgan_v2":
        return [
            *base,
            "--source-dir",
            str(runtime["source_dir"]),
            "--model-dir",
            str(runtime["model_dir"]),
        ]
    if generator == "amp_designer":
        return [
            *base,
            "--config",
            str(runtime["model_config_path"]),
            "--weights",
            str(runtime["model_weights_path"]),
            "--vocab",
            str(runtime["vocab_path"]),
        ]
    raise ValueError(f"unknown v37 generator: {generator}")


@activity.defn(name="generate_v37_batch")
async def generate_v37_batch(request: dict[str, Any]) -> dict[str, Any]:
    engine = request["engine"]
    runtime = request["runtime"]
    generator = engine["generator_id"]
    seed = int(request["seed"])
    settings = get_settings()
    work = Path(settings.work_root) / request["run_id"] / "v37" / generator / str(seed)
    await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
    request_path = work / "request.json"
    output_path = work / "raw-output.json"
    payload: dict[str, Any] = {
        "generator_id": generator,
        "seed": seed,
        "raw_proposal_budget": 1000,
    }
    if generator == "amp_designer":
        payload.update(
            batch_size=100,
            batches=10,
            top_k=10,
            top_p=1.0,
            temperature=None,
            decode_steps=34,
            device=runtime["device"],
        )
    await asyncio.to_thread(
        request_path.write_text,
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    command = _generator_command(engine, runtime, request_path, output_path)
    context = V37AttemptContext(
        run_id=uuid.UUID(request["run_id"]),
        logical_id=f"v37:generate:{generator}:{seed}",
        activity_name="generate_v37_batch",
        attempt=activity.info().attempt,
    )

    async def operation() -> dict[str, Any]:
        expectation = V37GeneratorRuntimeExpectation(**runtime["expectation"])
        live_paths = V37LiveRuntimePaths(
            adapter_path=Path(runtime["adapter_path"]),
            python_path=Path(runtime["python_path"]),
            packages_lock_path=Path(runtime["packages_lock_path"]),
            source_root=Path(runtime["source_root"]),
            model_root=Path(runtime["model_root"]),
        )

        async def receipt_writer(receipt: dict[str, Any]) -> None:
            await _persist_v37_launch_receipt(context=context, receipt=receipt)

        async def aggregate_receipt_writer(receipt: dict[str, Any]) -> None:
            await _persist_v37_aggregate_launch_receipt(
                context=context, receipt=receipt
            )

        async def progress_writer() -> None:
            activity.heartbeat(
                {
                    "v37_logical_id": context.logical_id,
                    "attempt": context.attempt,
                    "status": "guarded_generator_subprocess_running",
                }
            )

        stdout, launch_receipt = await run_v37_guarded_subprocess(
            command,
            manifest=runtime["runtime_manifest"],
            expectation=expectation,
            paths=live_paths,
            receipt_writer=receipt_writer,
            aggregate_receipt_writer=aggregate_receipt_writer,
            progress_writer=progress_writer,
            cwd=Path(runtime.get("cwd", work)),
        )
        result = json.loads(
            await asyncio.to_thread(output_path.read_text, encoding="utf-8")
        )
        if result.get("generator_id") != generator or result.get("seed") != seed:
            raise ValueError("v37 generator output identity mismatch")
        if len(result.get("records", [])) != 1000:
            raise ValueError("v37 generator output must contain exactly 1000 records")
        return {
            "result": result,
            "runtime_identity": runtime["identity"],
            "environment_sha256": runtime["environment_sha256"],
            "weights_sha256": runtime["weights_sha256"],
            "stdout_tail": stdout[-8000:],
            "launch_receipt": launch_receipt,
            "attempt": activity.info().attempt,
        }

    return await execute_v37_durable_attempt(operation, context=context)


@activity.defn(name="persist_v37_generation_batch")
async def persist_v37_generation_batch(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    manifest = request["manifest"]
    generated = request["generated"]
    result = generated["result"]
    generator = result["generator_id"]
    seed = int(result["seed"])
    raw_records = result["records"]
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            f"v37-generate-{generator}",
            str(result["adapter_version"]),
            generated["environment_sha256"],
            {"stage": "v37_generator_detail", "generator": generator, "seed": seed},
            {"raw_proposal_budget": 1000, "no_refill": True},
            result,
            weights_sha256=(
                generated["weights_sha256"]
                if isinstance(generated["weights_sha256"], str)
                else sha256_json(generated["weights_sha256"])
            ),
            random_seed=seed,
            attempt=int(generated["attempt"]),
        )
        existing = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == run_id, Candidate.generator_call_id == call.id)
                .order_by(Candidate.proposal_rank)
            )
        )
        recovered = bool(existing)
        existing_by_rank = {
            int(item.metadata_json["raw_rank"]): item for item in existing
        }
        artifact = await _store_json(
            {
                "records": raw_records,
                "runtime_identity": generated["runtime_identity"],
                "stdout_tail": generated["stdout_tail"],
                "live_launch_receipt": generated["launch_receipt"],
            }
        )
        await _register_artifact(
            session,
            call.id,
            asdict(artifact),
            "v37_raw_generator_output",
            {"generator_id": generator, "seed": seed},
        )
        runtime_receipt_artifact = await _store_json(generated["launch_receipt"])
        await _register_artifact(
            session,
            call.id,
            asdict(runtime_receipt_artifact),
            "v37_runtime_receipts",
            {"generator_id": generator, "seed": seed},
        )
        await _persist_v37_committed_runtime_receipt(
            session,
            run_id=run_id,
            tool_call_id=call.id,
            payload={
                "runtime_id": f"generator:{generator}",
                "seed": seed,
                "artifact_sha256": runtime_receipt_artifact.sha256,
                "launch_receipt_sha256": generated["launch_receipt"][
                    "launch_receipt_sha256"
                ],
            },
        )
        existing_sequences = set(
            await session.scalars(
                select(Candidate.sequence).where(
                    Candidate.run_id == run_id,
                    Candidate.generator_call_id != call.id,
                )
            )
        )
        seen = set()
        retained = []
        occurrence_witness = []
        proposal_occurrences = []
        cell_index = next(
            index
            for index, engine in enumerate(manifest["generators"]["engines"])
            if engine["generator_id"] == generator
        )
        seed_index = next(
            index
            for index, engine in enumerate(manifest["generators"]["engines"])
            if engine["generator_id"] == generator
            for index, value in enumerate(engine["seeds"])
            if int(value) == seed
        )
        cell_ordinal = cell_index * 3 + seed_index
        for expected_rank, row in enumerate(raw_records, start=1):
            if int(row["raw_rank"]) != expected_rank:
                raise ValueError("v37 raw ranks must be contiguous")
            sequence = "".join(str(row["sequence"]).split()).upper()
            disposition = "retained"
            if not sequence or set(sequence) - CANONICAL_AMINO_ACIDS:
                disposition = "invalid_symbol_or_empty"
            elif not 10 <= len(sequence) <= 25:
                disposition = "out_of_length"
            elif sequence in seen or sequence in existing_sequences:
                disposition = "duplicate"
            elif len(retained) >= 100:
                disposition = "valid_after_fixed_first_100"
            if disposition == "retained":
                seen.add(sequence)
                candidate = existing_by_rank.get(expected_rank)
                if candidate is None:
                    if recovered:
                        raise ValueError("v37 recovered generation candidate mapping is incomplete")
                    candidate = await repository.add_candidate(
                        run_id,
                        sequence,
                        generation=0,
                        proposal_rank=cell_ordinal * 1000 + expected_rank,
                        generator_call_id=call.id,
                        metadata={
                            "benchmark_id": manifest["benchmark_id"],
                            "generator_id": generator,
                            "generator_seed": seed,
                            "raw_rank": expected_rank,
                        },
                        actor="v37-generation",
                    )
                elif candidate.sequence != sequence:
                    raise ValueError("v37 recovered generation sequence differs from raw evidence")
                retained.append(candidate)
            else:
                candidate = None
            occurrence_witness.append(
                {"raw_rank": expected_rank, "sequence": sequence, "disposition": disposition}
            )
            proposal_occurrences.append(
                proposal_occurrence_payload(
                    logical_id=f"v37:generate:{generator}:{seed}",
                    raw_rank=expected_rank,
                    sequence=sequence,
                    valid=disposition not in {"invalid_symbol_or_empty", "out_of_length"},
                    duplicate=disposition == "duplicate",
                    retained=disposition == "retained",
                    candidate_id=str(candidate.id) if candidate is not None else None,
                    reason=None if disposition == "retained" else disposition,
                )
            )
            await repository.record_candidate_occurrence(
                run_id=run_id,
                tool_call_id=call.id,
                parent_candidate_id=None,
                occurrence_rank=expected_rank,
                occurrence_kind="de_novo",
                opaque_arm_label="rapid_champion",
                sequence=sequence,
                candidate_id=candidate.id if candidate is not None else None,
                metadata={
                    "disposition": disposition,
                    "generator_id": generator,
                    "generator_seed": seed,
                },
            )
        if recovered and set(existing_by_rank) != {
            int(item.metadata_json["raw_rank"]) for item in retained
        }:
            raise ValueError("v37 recovered generation retained set drifted")
        witness_artifact = await _store_json({"occurrences": occurrence_witness})
        await _register_artifact(
            session,
            call.id,
            asdict(witness_artifact),
            "v37_proposal_occurrence_manifest",
            {"generator_id": generator, "seed": seed},
        )
        await repository.append_event(
            "run",
            run_id,
            "v37.generation_batch_frozen",
            "v37-generation",
            {
                "generator_id": generator,
                "seed": seed,
                "generator_tool_call_id": str(call.id),
                "occurrence_witness_sha256": sha256_json(occurrence_witness),
                "retained_count": len(retained),
                "shortfall": 100 - len(retained),
                "no_refill": True,
            },
        )
        logical_id = f"v37:generate:{generator}:{seed}"
        await _persist_v37_node(
            session,
            run_id=run_id,
            manifest=manifest,
            logical_id=logical_id,
            environment_sha256=generated["environment_sha256"],
            input_payload={"generator_id": generator, "seed": seed},
            parameters={"raw_proposal_budget": len(raw_records), "no_refill": True},
            output_payload=generated,
            artifacts={
                "raw_proposals": {"records": raw_records},
                "proposal_occurrences": {
                    "schema_version": "1.0",
                    "occurrences": proposal_occurrences,
                },
                "retention_witness": {
                    "retained_candidate_ids": [str(item.id) for item in retained],
                    "shortfall": 100 - len(retained),
                    "no_refill": True,
                },
                "source_runtime_receipt": {
                    "runtime_identity": generated["runtime_identity"],
                    "environment_sha256": generated["environment_sha256"],
                    "weights_sha256": generated["weights_sha256"],
                    "adapter_version": result["adapter_version"],
                    "live_launch_receipt": generated["launch_receipt"],
                    "upstream_attempt": int(generated["attempt"]),
                    "stdout_sha256": sha256_json(generated["stdout_tail"]),
                },
            },
            model_uri=f"generator://{generator}",
            weights_sha256=(
                generated["weights_sha256"]
                if isinstance(generated["weights_sha256"], str)
                else sha256_json(generated["weights_sha256"])
            ),
            random_seed=seed,
        )
        await persist_v37_proposal_events(
            session,
            run_id=run_id,
            logical_id=logical_id,
            occurrences=proposal_occurrences,
            expected_count=len(raw_records),
        )
    return {
        "candidates": [
            {
                "id": str(item.id),
                "sequence": item.sequence,
                "sequence_sha256": item.sequence_sha256,
                "generator_id": generator,
                "seed": seed,
            }
            for item in retained
        ],
        "idempotently_recovered": recovered,
    }


@activity.defn(name="persist_v37_sequence_metric")
async def persist_v37_sequence_metric(request: dict[str, Any]) -> dict[str, Any]:
    """Persist one frozen metric plugin directly onto its canonical logical call."""
    run_id = uuid.UUID(request["run_id"])
    manifest = request["manifest"]
    result = request["metric_result"]["result"]
    provenance = request["metric_result"]["provenance"]
    plugin = result["plugin"]
    logical_id = f"v37:metric:{plugin['name']}"
    plan_item = next(
        item for item in _v37_plan(manifest)["metric_calls"] if item["logical_id"] == logical_id
    )
    if result.get("status") != "complete":
        raise ValueError(f"v37 required metric plugin did not complete: {plugin['name']}")
    candidates = {item["id"]: item for item in request["candidates"]}
    expected_metrics = set(plan_item["metric_names"])
    limitations = [
        f"handoff reliability: {result['contract']['reliability']}",
        f"configured trust: {plugin['trust']}",
        *result.get("limitations", []),
    ]
    rows: list[dict[str, Any]] = []
    for record in result["records"]:
        candidate = candidates.get(record["candidate_id"])
        if candidate is None or candidate["sequence"] != record["sequence"]:
            raise ValueError("v37 metric candidate identity or sequence mismatch")
        if record.get("status") not in {"complete", "ok", "success"}:
            raise ValueError("v37 required metric contains a failed candidate record")
        observed_names = {item["metric_name"] for item in record["observations"]}
        if observed_names != expected_metrics:
            raise ValueError("v37 metric plugin emitted undeclared or missing observations")
        for observation in record["observations"]:
            rows.append(
                {
                    "candidate_id": record["candidate_id"],
                    "metric_name": observation["metric_name"],
                    "numeric_value": observation["numeric_value"],
                    "text_value": observation["text_value"],
                    "unit": observation["unit"],
                    "status": "succeeded",
                    "out_of_domain": False,
                    "limitations": limitations,
                    "raw": {
                        "plugin": plugin,
                        "contract": result["contract"],
                        "adapter_version": result.get("adapter_version"),
                        "raw_row": record["raw"],
                    },
                }
            )
    expected_pairs = {
        (candidate_id, metric_name)
        for candidate_id in candidates
        for metric_name in expected_metrics
    }
    if {(row["candidate_id"], row["metric_name"]) for row in rows} != expected_pairs:
        raise ValueError("v37 metric plugin candidate coverage is incomplete")
    rows.sort(key=lambda item: (item["candidate_id"], item["metric_name"]))
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        source_runtime_receipt = {
            "provenance": provenance,
            "plugin": plugin,
            "contract": result["contract"],
        }
        call = await _persist_v37_node(
            session,
            run_id=run_id,
            manifest=manifest,
            logical_id=logical_id,
            environment_sha256=provenance["environment_sha256"],
            input_payload={"candidate_ids": sorted(candidates)},
            parameters={
                "plugin": plugin,
                "contract_reliability": result["contract"]["reliability"],
                "registry_sha256": result.get("registry_sha256"),
            },
            output_payload=request["metric_result"],
            artifacts={
                "evaluation_vector": {"evaluations": rows},
                "source_runtime_receipt": source_runtime_receipt,
            },
            model_uri=provenance["model_uri"],
            weights_sha256=provenance.get("weights_sha256"),
        )
        db_candidates = {
            str(item.id): item
            for item in await session.scalars(select(Candidate).where(Candidate.run_id == run_id))
        }
        for row in rows:
            await repository.record_evaluation(
                db_candidates[row["candidate_id"]].id,
                call.id,
                row["metric_name"],
                row["numeric_value"],
                row["unit"],
                row["raw"],
                text_value=row["text_value"],
                out_of_domain=False,
                limitations=row["limitations"],
            )
        physical_generator_ids = {
            db_candidates[candidate_id].generator_call_id for candidate_id in candidates
        }
        for parent_id in sorted(physical_generator_ids, key=str):
            if parent_id is not None:
                await repository.record_tool_dependency(
                    call.id, parent_id, "evaluates_generated_candidate"
                )
        if provenance.get("live_launch_receipt") is not None:
            await _persist_v37_committed_runtime_receipt(
                session,
                run_id=run_id,
                tool_call_id=call.id,
                payload={
                    "runtime_id": f"metric:{plugin['name']}",
                    "artifact_sha256": sha256_json(source_runtime_receipt),
                    "launch_receipt_sha256": provenance["live_launch_receipt"][
                        "launch_receipt_sha256"
                    ],
                },
            )
    return {"plugin": plugin["name"], "evaluation_count": len(rows), "tool_call_id": str(call.id)}


async def _candidate_payloads(session: Any, run_id: uuid.UUID) -> list[dict[str, Any]]:
    candidates = list(
        await session.scalars(
            select(Candidate).where(Candidate.run_id == run_id).order_by(Candidate.proposal_rank)
        )
    )
    evaluations = list(
        await session.scalars(
            select(Evaluation).where(Evaluation.candidate_id.in_([item.id for item in candidates]))
        )
    )
    numeric: dict[uuid.UUID, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    labels: dict[uuid.UUID, dict[str, str]] = defaultdict(dict)
    for item in evaluations:
        if item.numeric_value is not None:
            numeric[item.candidate_id][item.metric_name].append(float(item.numeric_value))
        if item.text_value is not None:
            labels[item.candidate_id][item.metric_name] = item.text_value
    payloads = []
    for item in candidates:
        metrics = {
            name: float(statistics.median(values))
            for name, values in numeric[item.id].items()
        }
        aliases = {
            "median_pair_iptm": ("boltz2_pair_iptm_median", statistics.median),
            "median_pocket_coverage": ("pocket_coverage_fraction", statistics.median),
            "maximum_geometric_clash_count": ("interface_clash_count", max),
            "peptide_backbone_displacement_range": (
                "rosetta_peptide_bb_rmsd_angstrom",
                lambda values: max(values) - min(values),
            ),
            "median_representative_rosetta_interface_delta_g": (
                "rosetta_dg_separated_reu",
                statistics.median,
            ),
        }
        for alias, (source, reducer) in aliases.items():
            values = numeric[item.id].get(source)
            if values:
                metrics[alias] = float(reducer(values))
        payloads.append(
            {
            "id": str(item.id),
            "sequence": item.sequence,
            "sequence_sha256": item.sequence_sha256,
            "generator_id": item.metadata_json["generator_id"],
            "seed": item.metadata_json["generator_seed"],
            "source_ordinal": item.metadata_json["raw_rank"],
            "metrics": metrics,
            "labels": labels[item.id],
            }
        )
    return payloads


async def _validate_stage1_observations(
    session: Any,
    *,
    run_id: uuid.UUID,
    candidate_ids: list[uuid.UUID],
    manifest: dict[str, Any],
) -> None:
    required = list(
        manifest["stage_1_sequence_evaluation"]["required_metric_names"]
    )
    rows = list(
        await session.scalars(
            select(Evaluation).where(
                Evaluation.candidate_id.in_(candidate_ids),
                Evaluation.metric_name.in_(required),
            )
        )
    )
    by_key: dict[tuple[uuid.UUID, str], list[Evaluation]] = defaultdict(list)
    for row in rows:
        by_key[(row.candidate_id, row.metric_name)].append(row)
    label_values = {
        "toxinpred3_label": {"Toxin", "Non-Toxin"},
        "macrel_hemolysis_label": {"high", "low"},
    }
    for candidate_id in candidate_ids:
        for metric_name in required:
            observed = by_key[(candidate_id, metric_name)]
            if len(observed) != 1:
                raise ValueError(
                    "v37 stage-1 requires exactly one observation per "
                    f"candidate and metric: {candidate_id}/{metric_name}"
                )
            row = observed[0]
            if row.status != "succeeded" or row.out_of_domain:
                raise ValueError("v37 stage-1 contains failed or out-of-domain evidence")
            if metric_name in label_values:
                if row.text_value not in label_values[metric_name]:
                    raise ValueError("v37 stage-1 categorical label is outside its enum")
            elif row.numeric_value is None or not math.isfinite(row.numeric_value):
                raise ValueError("v37 stage-1 numeric evidence is missing or non-finite")
    calls = list(
        await session.scalars(select(ToolCall).where(ToolCall.run_id == run_id))
    )
    metric_calls = {
        str(call.input_json.get("v37_logical_id")): call
        for call in calls
        if str(call.input_json.get("v37_logical_id", "")).startswith("v37:metric:")
    }
    expected_logical_ids = {
        f"v37:metric:{item['name']}"
        for item in manifest["stage_1_sequence_evaluation"]["metric_plugins"]
    }
    if set(metric_calls) != expected_logical_ids:
        raise ValueError("v37 stage-1 metric ToolCall set differs from five-plugin contract")
    expected_owner_by_metric = {
        metric_name: f"v37:metric:{plugin['name']}"
        for plugin in manifest["stage_1_sequence_evaluation"]["metric_plugins"]
        for metric_name in plugin["observation_names"]
    }
    for (candidate_id, metric_name), observed in by_key.items():
        expected_logical_id = expected_owner_by_metric[metric_name]
        call = metric_calls[expected_logical_id]
        expected_tool_call_id = str(getattr(call, "id", expected_logical_id))
        if str(observed[0].tool_call_id) != expected_tool_call_id:
            raise ValueError(
                "v37 stage-1 plugin ToolCall ownership mismatch: "
                f"{candidate_id}/{metric_name}"
            )


def _stage1_lanes(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    shortlist = manifest["stage_1_sequence_evaluation"]["shortlist"]
    return [
        {
            "name": name,
            "quota": quota,
            "objective_families": shortlist["lane_objective_families"][name],
        }
        for name, quota in shortlist["lane_quotas"].items()
    ]


@activity.defn(name="persist_v37_stage1_shortlist")
async def persist_v37_stage1_shortlist(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    manifest = request["manifest"]
    async with SessionFactory() as session, session.begin():
        candidates = await _candidate_payloads(session, run_id)
        await _validate_stage1_observations(
            session,
            run_id=run_id,
            candidate_ids=[uuid.UUID(item["id"]) for item in candidates],
            manifest=manifest,
        )
        async def select_stage1() -> dict[str, Any]:
            return select_v37_lanes(
                candidates,
                lanes=_stage1_lanes(manifest),
                family_objectives=manifest["stage_1_sequence_evaluation"][
                    "endpoint_families"
                ],
                maximum_similarity=0.80,
                maximum_per_generator=6,
                maximum_per_generator_seed=2,
            )

        result = await execute_v37_durable_attempt(
            select_stage1,
            context=V37AttemptContext(
                run_id=run_id,
                logical_id="v37:stage1-shortlist",
                activity_name="persist_v37_stage1_shortlist",
                attempt=activity.info().attempt,
            ),
        )
        repository = ExperimentRepository(session)
        logical_id = "v37:stage1-shortlist"
        stop_payload = await _persist_v37_stop(
            session,
            run_id=run_id,
            logical_id=logical_id,
            stop_reason="completed_frozen_budget",
        )
        decision_payload = {"selection": result, "weighted_total_used": False}
        call = await _persist_v37_node(
            session,
            run_id=run_id,
            manifest=manifest,
            logical_id=logical_id,
            environment_sha256=sha256_json({"policy": "v37-stage1-pareto-maximin"}),
            input_payload={"candidate_ids": [item["id"] for item in candidates]},
            parameters={"weighted_total": False},
            output_payload=result,
            artifacts={
                "shortlist_manifest": {
                    "candidate_ids": result["selected_ids"],
                    "selection": result,
                },
                "risk_exclusion_witness": result["risk_guard_witness"],
                **_selection_witness_payloads(result),
                "agent_decision": decision_payload,
                "stop_event": stop_payload,
            },
            model_uri="deterministic://v37-stage1-pareto-maximin",
        )
        observed_calls = list(
            await session.scalars(
                select(ToolCall).where(
                    ToolCall.run_id == run_id,
                    ToolCall.input_json["v37_logical_id"].astext.in_(
                        [
                            "v37:knowledge",
                            *[
                                item["logical_id"]
                                for item in _v37_plan(manifest)["metric_calls"]
                            ],
                        ]
                    ),
                )
            )
        )
        decision = await persist_v37_agent_decision(
            session,
            run_id=run_id,
            logical_id=logical_id,
            tool_call_id=call.id,
            observed_tool_call_ids=[item.id for item in observed_calls],
            prompt_text="Apply frozen lane-local Pareto and diversity rules without scalarization.",
            response_text=json.dumps(result, sort_keys=True),
            structured=decision_payload,
        )
        by_id = {item["id"]: item for item in candidates}
        shortlisted = [by_id[item] for item in result["selected_ids"]]
        for item in shortlisted:
            await repository.transition_candidate(
                uuid.UUID(item["id"]),
                CandidateStatus.STRUCTURE_QUEUED,
                "v37-stage1-shortlist",
                "selected by frozen v37 stage-1 portfolio",
            )
    return {"candidates": shortlisted, "decision_id": str(decision.id)}


@activity.defn(name="run_and_persist_v37_knowledge")
async def run_and_persist_v37_knowledge(request: dict[str, Any]) -> dict[str, Any]:
    runtime = request["runtime"]
    settings = get_settings()
    work = Path(settings.work_root) / request["run_id"] / "v37" / "knowledge"
    await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
    output_path = work / "context-pack.json"
    command = build_knowledge_command(
        python_executable=Path(runtime["python_path"]),
        kbctl_path=Path(runtime["kbctl_path"]),
        target_key="AceA",
        query=str(request["query"]),
        application="v37_rapid_champion_generation",
        output_path=output_path,
        config_path=Path(runtime["config_path"]) if runtime.get("config_path") else None,
    )
    run_id = uuid.UUID(request["run_id"])
    context = V37AttemptContext(
        run_id=run_id,
        logical_id="v37:knowledge",
        activity_name="run_and_persist_v37_knowledge",
        attempt=activity.info().attempt,
    )

    async def operation() -> dict[str, Any]:
        stdout, launch_receipt = await _run_guarded_generic_runtime(
            command=command,
            runtime=runtime,
            context=context,
            cwd=Path(runtime["cwd"]),
            input_paths={
                "knowledge_runtime_manifest": Path(
                    runtime["execution_guard"]["paths"]["runtime_manifest_path"]
                )
            },
        )
        return {
            "context_pack": json.loads(
                await asyncio.to_thread(output_path.read_text, encoding="utf-8")
            ),
            "live_launch_receipt": launch_receipt,
            "stdout_tail": stdout[-8000:],
        }

    executed = await execute_v37_durable_attempt(operation, context=context)
    payload = executed["context_pack"]
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            "v37-knowledge",
            str(runtime["release_revision"]),
            str(runtime["runtime_manifest_sha256"]),
            {"stage": "v37_knowledge_provider_detail", "query": request["query"]},
            {"positive_support_is_not_score": True},
            payload,
        )
        artifact = await _store_json(payload)
        await _register_artifact(
            session, call.id, asdict(artifact), "v37_knowledge_context_pack", {}
        )
        runtime_artifact = await _store_json(executed["live_launch_receipt"])
        await _register_artifact(
            session,
            call.id,
            asdict(runtime_artifact),
            "v37_runtime_receipts",
            {"runtime_id": "knowledge-provider"},
        )
        await _persist_v37_committed_runtime_receipt(
            session,
            run_id=run_id,
            tool_call_id=call.id,
            payload={
                "runtime_id": "knowledge-provider",
                "artifact_sha256": runtime_artifact.sha256,
                "launch_receipt_sha256": executed["live_launch_receipt"][
                    "launch_receipt_sha256"
                ],
            },
        )
    return {
        "tool_call_id": str(call.id),
        "provider_input_sha256": call.input_sha256,
        "provider_output_sha256": call.output_sha256,
        "context_pack_artifact_sha256": artifact.sha256,
        "runtime_receipt_artifact_sha256": runtime_artifact.sha256,
        "context_pack": payload,
        "live_launch_receipt": executed["live_launch_receipt"],
    }


@activity.defn(name="persist_v37_knowledge_projection")
async def persist_v37_knowledge_projection(request: dict[str, Any]) -> dict[str, Any]:
    """Bind the run-level provider result to every frozen candidate explicitly."""
    run_id = uuid.UUID(request["run_id"])
    manifest = request["manifest"]
    knowledge = request["knowledge"]
    pack = knowledge["context_pack"]
    candidates = request["candidates"]
    evidence_index = pack.get("evidence_index")
    if isinstance(evidence_index, dict):
        evidence_items = [
            {"card_id": str(key), "content": value}
            for key, value in sorted(evidence_index.items())
        ]
    elif isinstance(evidence_index, list):
        evidence_items = [dict(item) for item in evidence_index]
    else:
        raise ValueError("v37 knowledge context pack lacks a typed evidence index")
    cards = []
    for ordinal, item in enumerate(evidence_items, start=1):
        card_id = str(item.get("card_id") or item.get("id") or f"card-{ordinal}")
        cards.append(
            {
                "card_id": card_id,
                "revision": str(item.get("revision") or pack.get("policy_version")),
                "passage_manifest_sha256": sha256_json(item),
            }
        )
    if not cards:
        raise ValueError("v37 knowledge context pack contains no evidence cards")
    candidate_ids = [item["id"] for item in candidates]
    adoption_edges = []
    candidate_id_set = set(candidate_ids)
    for card, item in zip(cards, evidence_items, strict=True):
        explicit_ids = item.get("candidate_ids")
        if explicit_ids is None:
            disposition = "not_applicable"
            applicable_ids = candidate_ids
            reason = "provider supplied no candidate-specific applicability mapping"
        else:
            applicable_ids = [str(value) for value in explicit_ids]
            if not applicable_ids or not set(applicable_ids).issubset(candidate_id_set):
                raise ValueError("v37 knowledge candidate applicability mapping is invalid")
            disposition = str(item.get("disposition", "used"))
            if disposition not in {"used", "rejected", "not_applicable"}:
                raise ValueError("v37 knowledge disposition is invalid")
            reason = str(item.get("reason") or "provider-explicit candidate applicability")
        adoption_edges.append(
            {
                "evidence_id": f"{card['card_id']}:candidate-applicability",
                "disposition": disposition,
                "reason": reason,
                "candidate_ids": applicable_ids,
            }
        )
    knowledge_evidence = {
        "schema_version": "1.0",
        "query_sha256": sha256_json(request["query"]),
        "query_pack_sha256": sha256_json(pack),
        "trace_sha256": sha256_json(pack.get("retrieval_trace_id")),
        "policy_sha256": manifest["verified_auxiliaries"]["knowledge"][
            "active_policy_sha256"
        ],
        "cards": cards,
        "adoption_edges": adoption_edges,
    }
    logical_id = "v37:knowledge"
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        stop_payload = await _persist_v37_stop(
            session,
            run_id=run_id,
            logical_id=logical_id,
            stop_reason="completed_frozen_query_and_candidate_projection",
        )
        decision_payload = {
            "candidate_count": len(candidate_ids),
            "card_count": len(cards),
            "positive_support_is_not_a_selection_score": True,
        }
        call = await _persist_v37_node(
            session,
            run_id=run_id,
            manifest=manifest,
            logical_id=logical_id,
            environment_sha256=manifest["verified_auxiliaries"]["knowledge"][
                "runtime_manifest_sha256"
            ],
            input_payload={"query": request["query"], "candidate_ids": candidate_ids},
            parameters={"positive_support_is_not_score": True},
            output_payload=pack,
            artifacts={
                "knowledge_evidence": knowledge_evidence,
                "provider_release_receipt": {
                    **manifest["verified_auxiliaries"]["knowledge"],
                    "provider_tool_call_id": knowledge["tool_call_id"],
                    "provider_input_sha256": knowledge["provider_input_sha256"],
                    "provider_output_sha256": knowledge["provider_output_sha256"],
                    "context_pack_sha256": sha256_json(pack),
                    "context_pack_artifact_sha256": knowledge[
                        "context_pack_artifact_sha256"
                    ],
                    "runtime_receipt_artifact_sha256": knowledge[
                        "runtime_receipt_artifact_sha256"
                    ],
                },
                "agent_decision": decision_payload,
                "stop_event": stop_payload,
            },
            model_uri="provider://amp-kb/context",
        )
        await repository.record_tool_dependency(
            call.id,
            uuid.UUID(knowledge["tool_call_id"]),
            "projects_knowledge_provider_output",
        )
        await persist_v37_agent_decision(
            session,
            run_id=run_id,
            logical_id=logical_id,
            tool_call_id=call.id,
            observed_tool_call_ids=[uuid.UUID(knowledge["tool_call_id"])],
            prompt_text="Project the frozen verified knowledge pack onto all candidates.",
            response_text=json.dumps(decision_payload, sort_keys=True),
            structured=decision_payload,
        )
    return {"tool_call_id": str(call.id), "knowledge_evidence": knowledge_evidence}


@activity.defn(name="run_and_persist_v37_pepshot")
async def run_and_persist_v37_pepshot(request: dict[str, Any]) -> dict[str, Any]:
    runtime = request["runtime"]
    settings = get_settings()
    provider_contract = request["provider_contract"]
    if (
        provider_contract.get("required_route") != "deterministic_inspect"
        or provider_contract.get("fallback_allowed") is not False
        or runtime.get("release_id") != provider_contract.get("release_id")
        or runtime.get("release_manifest_sha256")
        != provider_contract.get("release_manifest_sha256")
        or runtime.get("runtime_manifest_sha256")
        != provider_contract.get("runtime_manifest_sha256")
        or runtime.get("contract_sha256") != provider_contract.get("contract_sha256")
    ):
        raise ValueError("v37 PepShot runtime differs from the frozen provider contract")
    attempt = activity.info().attempt
    run_id = uuid.UUID(request["run_id"])
    root = Path(settings.work_root) / request["run_id"] / "v37" / "pepshot"
    contract_dir = root / f"contract-attempt-{attempt}"
    await asyncio.to_thread(contract_dir.mkdir, parents=True, exist_ok=True)
    contract_path = contract_dir / "inspect-contract.json"
    contract_command = [
            str(runtime["executable"]),
            "contract",
            "--task",
            "inspect",
            "--out",
            str(contract_path),
        ]

    contract_context = V37AttemptContext(
        run_id=run_id,
        logical_id="v37:physical:pepshot:contract",
        activity_name="run_and_persist_v37_pepshot",
        attempt=attempt,
    )

    async def execute_contract() -> dict[str, Any]:
        stdout, launch_receipt = await _run_guarded_generic_runtime(
            command=contract_command,
            runtime=runtime,
            context=contract_context,
            cwd=Path(runtime["cwd"]),
        )
        return {"stdout_tail": stdout[-8000:], "launch_receipt": launch_receipt}

    contract_execution = await execute_v37_durable_attempt(
        execute_contract,
        context=contract_context,
    )
    inspect_contract = json.loads(
        await asyncio.to_thread(contract_path.read_text, encoding="utf-8")
    )
    if (
        inspect_contract.get("task") != "inspect"
        or inspect_contract.get("fallback_allowed") is not False
        or inspect_contract.get("route", {}).get("task") != "inspect"
    ):
        raise ValueError("v37 PepShot inspect contract is not fallback-free")
    inspections = []
    detailed_outputs: list[dict[str, Any]] = []
    for candidate in request["candidates"]:
        candidate_id = candidate["id"]
        structures = request["structures_by_candidate"].get(candidate_id)
        if not isinstance(structures, list):
            raise ValueError("v37 PepShot structure mapping is incomplete")
        provider_poses = []
        coordinates_by_seed: dict[int, dict[str, Any]] = {}
        for structure in structures:
            seed = int(structure["input"]["seed"])
            coordinate = _select_v37_coordinate_artifact(structure)
            suffix = Path(coordinate["path"]).suffix.lower()
            coordinates_by_seed[seed] = coordinate
            provider_poses.append(
                {
                    "run_id": request["run_id"],
                    "candidate_id": candidate_id,
                    "pose_id": structure["tool_call_id"],
                    "boltz_seed": seed,
                    "pair_iptm": structure["boltz2"]["pair_iptm"],
                    "coordinate_path": f"poses/{seed}{suffix}",
                    "coordinate_sha256": coordinate["sha256"],
                }
            )
        spec = build_v37_pepshot_inspect_request(
            candidate={
                "run_id": request["run_id"],
                "candidate_id": candidate_id,
                "sequence": candidate["sequence"],
                "sequence_sha256": candidate["sequence_sha256"],
            },
            poses=provider_poses,
            receptor_chains=["A"],
            peptide_chains=["B"],
            pocket_residues=[
                {"chain": "A", "number": int(number)}
                for number in request["experiment_spec"]["target"].get(
                    "pocket_residues", []
                )
            ],
        )
        selected_seed = int(spec["seed"])
        coordinate = coordinates_by_seed[selected_seed]
        coordinate_bytes = await asyncio.to_thread(
            ContentAddressedObjectStore().get_bytes, coordinate["uri"]
        )
        if sha256_bytes(coordinate_bytes) != coordinate["sha256"]:
            raise ValueError("v37 PepShot coordinate object hash drifted")
        work = root / candidate_id / f"attempt-{attempt}"
        await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
        spec_path = work / "spec.json"
        coordinate_path = work / Path(spec["structure_path"])
        await asyncio.to_thread(coordinate_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(coordinate_path.write_bytes, coordinate_bytes)
        await asyncio.to_thread(
            spec_path.write_text,
            json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        inspection_path = work / "inspection.json"
        inspect_command = (
                str(runtime["executable"]),
                "inspect",
                "--spec",
                str(spec_path),
                "--out",
                str(inspection_path),
            )

        detail_context = V37AttemptContext(
            run_id=run_id,
            logical_id=f"v37:physical:pepshot:{candidate_id}",
            activity_name="run_and_persist_v37_pepshot",
            attempt=attempt,
        )

        async def execute_inspection(
            command: tuple[str, ...] = inspect_command,
            context: V37AttemptContext = detail_context,
            request_path: Path = spec_path,
            coordinate_input: Path = coordinate_path,
        ) -> dict[str, Any]:
            stdout, launch_receipt = await _run_guarded_generic_runtime(
                command=list(command),
                runtime=runtime,
                context=context,
                cwd=Path(runtime["cwd"]),
                input_paths={
                    "inspection_spec": request_path,
                    "coordinate": coordinate_input,
                },
            )
            return {"stdout_tail": stdout[-8000:], "launch_receipt": launch_receipt}

        receipt = await execute_v37_durable_attempt(
            execute_inspection,
            context=detail_context,
        )
        inspection = json.loads(
            await asyncio.to_thread(inspection_path.read_text, encoding="utf-8")
        )
        provider_result = consume_v37_pepshot_inspection(
            request=spec,
            inspection=inspection,
            contract_receipt=inspect_contract,
            provider_release_receipt={
                "provider_contract_verified": True,
                "release_id": runtime["release_id"],
                "release_manifest_sha256": runtime["release_manifest_sha256"],
                "runtime_manifest_sha256": runtime["runtime_manifest_sha256"],
            },
        )
        audit = inspection.get("audit", {})
        findings = audit.get("spatial_findings")
        if not isinstance(findings, list) or audit.get("spatial_finding_count") != len(
            findings
        ):
            raise ValueError("v37 PepShot inspection finding manifest is inconsistent")
        plausibility = audit.get("interface_plausibility", {})
        summary = {
            **asdict(provider_result),
            "spatial_finding_count": len(findings),
            "blocking_finding_types": list(
                plausibility.get("blocking_finding_types", [])
            ),
        }
        inspections.append(summary)
        detailed_outputs.append(
            {
                "candidate_id": candidate_id,
                "spec": spec,
                "inspection": inspection,
                "stdout_receipt": receipt,
                "summary": summary,
            }
        )
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        contract_call = await repository.record_completed_tool_call(
            run_id,
            "pepshot-contract",
            str(runtime["release_id"]),
            str(runtime["runtime_manifest_sha256"]),
            {"task": "inspect"},
            {"fallback_allowed": False},
            inspect_contract,
            model_uri="provider://pepshot/contract/inspect",
            attempt=attempt,
        )
        contract_artifact = await _store_json(inspect_contract)
        await _register_artifact(
            session,
            contract_call.id,
            asdict(contract_artifact),
            "pepshot_inspect_contract",
            {"release_id": runtime["release_id"]},
        )
        contract_runtime_artifact = await _store_json(
            contract_execution["launch_receipt"]
        )
        await _register_artifact(
            session,
            contract_call.id,
            asdict(contract_runtime_artifact),
            "v37_runtime_receipts",
            {"runtime_id": "pepshot", "route": "contract"},
        )
        await _persist_v37_committed_runtime_receipt(
            session,
            run_id=run_id,
            tool_call_id=contract_call.id,
            payload={
                "runtime_id": "pepshot",
                "route": "contract",
                "artifact_sha256": contract_runtime_artifact.sha256,
                "launch_receipt_sha256": contract_execution["launch_receipt"][
                    "launch_receipt_sha256"
                ],
            },
        )
        detail_calls = []
        for detail in detailed_outputs:
            summary = detail["summary"]
            detail_call = await repository.record_completed_tool_call(
                run_id,
                "pepshot-inspect",
                str(runtime["release_id"]),
                str(runtime["runtime_manifest_sha256"]),
                {
                    "candidate_id": detail["candidate_id"],
                    "request_sha256": sha256_json(detail["spec"]),
                    "coordinate_sha256": summary["source_sha256"],
                },
                {
                    "route": "inspect",
                    "fallback_allowed": False,
                    "provider_contract_sha256": runtime["contract_sha256"],
                },
                detail["inspection"],
                model_uri="provider://pepshot/inspect",
                random_seed=int(summary["boltz_seed"]),
                attempt=attempt,
            )
            summary["detail_tool_call_id"] = str(detail_call.id)
            summary["detail_input_sha256"] = detail_call.input_sha256
            summary["detail_output_sha256"] = detail_call.output_sha256
            await repository.record_tool_dependency(
                detail_call.id, contract_call.id, "uses_verified_inspect_contract"
            )
            request_artifact = await _store_json(detail["spec"])
            inspection_artifact = await _store_json(detail["inspection"])
            receipt_artifact = await _store_json(
                {
                    "stdout_tail": detail["stdout_receipt"]["stdout_tail"],
                    "inspection_sha256": summary["inspection_sha256"],
                    "disposition": summary["disposition"],
                }
            )
            runtime_receipt_artifact = await _store_json(
                detail["stdout_receipt"]["launch_receipt"]
            )
            await _register_artifact(
                session,
                detail_call.id,
                asdict(request_artifact),
                "pepshot_inspect_request",
                {"candidate_id": detail["candidate_id"]},
            )
            await _register_artifact(
                session,
                detail_call.id,
                asdict(inspection_artifact),
                "pepshot_inspection",
                {"candidate_id": detail["candidate_id"]},
            )
            await _register_artifact(
                session,
                detail_call.id,
                asdict(receipt_artifact),
                "pepshot_inspect_receipt",
                {"candidate_id": detail["candidate_id"]},
            )
            await _register_artifact(
                session,
                detail_call.id,
                asdict(runtime_receipt_artifact),
                "v37_runtime_receipts",
                {"runtime_id": "pepshot", "candidate_id": detail["candidate_id"]},
            )
            await _persist_v37_committed_runtime_receipt(
                session,
                run_id=run_id,
                tool_call_id=detail_call.id,
                payload={
                    "runtime_id": "pepshot",
                    "candidate_id": detail["candidate_id"],
                    "artifact_sha256": runtime_receipt_artifact.sha256,
                    "launch_receipt_sha256": detail["stdout_receipt"][
                        "launch_receipt"
                    ]["launch_receipt_sha256"],
                },
            )
            detail_calls.append(detail_call)
        output = {"inspections": inspections}

        async def project_pepshot() -> dict[str, Any]:
            if len(inspections) != len(request["candidates"]):
                raise ValueError("v37 PepShot projection coverage drifted")
            return output

        output = await execute_v37_durable_attempt(
            project_pepshot,
            context=V37AttemptContext(
                run_id=run_id,
                logical_id="v37:pepshot",
                activity_name="run_and_persist_v37_pepshot",
                attempt=attempt,
            ),
        )
        logical_id = "v37:pepshot"
        stop_payload = await _persist_v37_stop(
            session,
            run_id=run_id,
            logical_id=logical_id,
            stop_reason="completed_frozen_inspection_budget",
        )
        decision_payload = {
            "inspections": inspections,
            "fallback_allowed": False,
            "provider_contract_verified": True,
        }
        call = await _persist_v37_node(
            session,
            run_id=run_id,
            manifest=request["manifest"],
            logical_id=logical_id,
            environment_sha256=str(runtime["runtime_manifest_sha256"]),
            input_payload={
                "candidate_ids": [item["id"] for item in request["candidates"]],
                "detail_tool_call_ids": [str(item.id) for item in detail_calls],
            },
            parameters={
                "route": "deterministic_inspect",
                "fallback_allowed": False,
                "provider_contract_sha256": runtime["contract_sha256"],
            },
            output_payload=output,
            artifacts={
                "pepshot_evidence": output,
                "provider_release_receipt": {
                    **provider_contract,
                    "contract_tool_call_id": str(contract_call.id),
                    "detail_tool_call_ids": [str(item.id) for item in detail_calls],
                },
                "agent_decision": decision_payload,
                "stop_event": stop_payload,
            },
            model_uri="provider://pepshot/inspect-aggregate",
        )
        for detail_call in detail_calls:
            await repository.record_tool_dependency(
                call.id, detail_call.id, "aggregates_candidate_inspection"
            )
        structure_call = await session.scalar(
            select(ToolCall).where(
                ToolCall.run_id == run_id,
                ToolCall.input_json["v37_logical_id"].astext == "v37:structure",
            )
        )
        if structure_call is None:
            raise ValueError("v37 PepShot requires the canonical structure node")
        await persist_v37_agent_decision(
            session,
            run_id=run_id,
            logical_id=logical_id,
            tool_call_id=call.id,
            observed_tool_call_ids=[structure_call.id, *[item.id for item in detail_calls]],
            prompt_text="Apply the frozen PepShot structural inspection contract.",
            response_text=json.dumps(decision_payload, sort_keys=True),
            structured=decision_payload,
        )
    return {"tool_call_id": str(call.id), **output}


@activity.defn(name="persist_v37_structure_stage_summaries")
async def persist_v37_structure_stage_summaries(
    request: dict[str, Any],
) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    manifest = request["manifest"]
    structure_contract = manifest["stage_2_structure_confirmation"]
    poses_per_candidate = int(structure_contract["poses_per_candidate"])
    rosetta_decoys_per_pose = int(structure_contract["rosetta_decoys_per_pose"])
    candidate_ids = [str(value) for value in request["candidate_ids"]]
    structures_by_candidate = request["structures_by_candidate"]
    for candidate_id in candidate_ids:
        poses = structures_by_candidate.get(candidate_id)
        if not isinstance(poses, list) or len(poses) != poses_per_candidate:
            raise ValueError(f"v37 pose coverage mismatch for {candidate_id}")
    if set(structures_by_candidate) != set(candidate_ids):
        raise ValueError("v37 pose coverage contains an unknown or missing candidate")
    rosetta_results = request["rosetta_results"]
    if len(rosetta_results) != len(candidate_ids) * poses_per_candidate:
        raise ValueError("v37 decoy coverage has the wrong pose count")
    for result in rosetta_results:
        decoys = result.get("rosetta", {}).get("decoys")
        if not isinstance(decoys, list) or len(decoys) != rosetta_decoys_per_pose:
            raise ValueError("v37 decoy coverage mismatch for a frozen pose")
    flattened_poses = [
        pose for candidate_id in candidate_ids for pose in structures_by_candidate[candidate_id]
    ]
    pose_rows: list[dict[str, Any]] = []
    decoy_rows: list[dict[str, Any]] = []
    rosetta_by_pose = {
        str(item["provenance"]["parent_tool_call_id"]): item
        for item in rosetta_results
    }
    if set(rosetta_by_pose) != {str(item["tool_call_id"]) for item in flattened_poses}:
        raise ValueError("v37 Rosetta-to-pose identity mapping is not one-to-one")
    for pose in flattened_poses:
        rosetta_result = rosetta_by_pose[str(pose["tool_call_id"])]
        candidate_id = str(pose["candidate"]["id"])
        pose_id = str(pose["tool_call_id"])
        seed = int(pose["input"]["seed"])
        coordinate = _select_v37_coordinate_artifact(pose)
        audits = pose["interface_audit"]["sample_audits"]
        audit = next(
            item
            for item in audits
            if str(item["tool_call_id"]) == pose_id and int(item["seed"]) == seed
        )
        decoys = rosetta_result["rosetta"]["decoys"]
        displacement = statistics.median(
            float(item["peptide_bb_rmsd"]) for item in decoys
        )
        pose_rows.append(
            {
                "candidate_id": candidate_id,
                "pose_id": pose_id,
                "boltz_seed": seed,
                "interface_audit_tool_call_id": str(
                    pose["interface_audit_tool_call_id"]
                ),
                "structure_sha256": coordinate["sha256"],
                "coordinate_audit_sha256": sha256_json(audit),
                "pair_iptm": float(pose["boltz2"]["pair_iptm"]),
                "pocket_coverage_fraction": float(audit["pocket_coverage_fraction"]),
                "geometric_clash_count": float(audit["cross_chain_clash_count"]),
                "peptide_backbone_displacement": displacement,
            }
        )
        for ordinal, decoy in enumerate(decoys, start=1):
            required_hashes = ("input_sha256", "output_sha256", "score_terms_sha256")
            if not all(
                isinstance(decoy.get(field), str) and len(decoy[field]) == 64
                for field in required_hashes
            ):
                raise ValueError("v37 Rosetta decoy lacks exact input/output/score hashes")
            decoy_id = str(decoy.get("decoy_id") or f"{pose_id}:decoy:{ordinal}")
            decoy_rows.append(
                {
                    "candidate_id": candidate_id,
                    "pose_id": pose_id,
                    "decoy_id": decoy_id,
                    "boltz_seed": seed,
                    "rosetta_tool_call_id": str(rosetta_result["tool_call_id"]),
                    "interface_delta_g_reu": float(decoy["dG_separated"]),
                    "input_sha256": decoy["input_sha256"],
                    "output_sha256": decoy["output_sha256"],
                    "score_terms_sha256": decoy["score_terms_sha256"],
                }
            )
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        calls = list(await session.scalars(select(ToolCall).where(ToolCall.run_id == run_id)))
        calls_by_id = {str(item.id): item for item in calls}
        boltz_ids = {str(item["pose_id"]) for item in pose_rows}
        audit_ids = {str(item["interface_audit_tool_call_id"]) for item in pose_rows}
        rosetta_ids = {str(item["rosetta_tool_call_id"]) for item in decoy_rows}
        if not boltz_ids or not audit_ids or not rosetta_ids:
            raise ValueError("v37 structure lineage has an empty physical source set")
        if not (boltz_ids | audit_ids | rosetta_ids).issubset(calls_by_id):
            raise ValueError("v37 structure lineage references a missing physical ToolCall")
        if any(calls_by_id[item].tool_name != "boltz2" for item in boltz_ids):
            raise ValueError("v37 structure lineage references a non-Boltz pose ToolCall")
        if any(
            calls_by_id[item].tool_name != "coordinate-interface-audit"
            for item in audit_ids
        ):
            raise ValueError("v37 structure lineage references a non-audit ToolCall")
        if any(
            calls_by_id[item].tool_name
            != "pyrosetta-flexpepdock-interface-analyzer"
            for item in rosetta_ids
        ):
            raise ValueError("v37 structure lineage references a non-Rosetta ToolCall")
        for pose_row in pose_rows:
            boltz_call = calls_by_id[str(pose_row["pose_id"])]
            audit_call = calls_by_id[str(pose_row["interface_audit_tool_call_id"])]
            if (
                int(boltz_call.random_seed or -1) != int(pose_row["boltz_seed"])
                or boltz_call.input_json.get("peptide_sequence")
                != next(
                    item["candidate"]["sequence"]
                    for item in flattened_poses
                    if str(item["tool_call_id"]) == str(pose_row["pose_id"])
                )
            ):
                raise ValueError("v37 Boltz ToolCall candidate or seed lineage drifted")
            pose_row["boltz_input_sha256"] = boltz_call.input_sha256
            pose_row["boltz_output_sha256"] = boltz_call.output_sha256
            pose_row["interface_audit_input_sha256"] = audit_call.input_sha256
            pose_row["interface_audit_output_sha256"] = audit_call.output_sha256
        for decoy_row in decoy_rows:
            rosetta_call = calls_by_id[str(decoy_row["rosetta_tool_call_id"])]
            decoy_row["rosetta_call_input_sha256"] = rosetta_call.input_sha256
            decoy_row["rosetta_call_output_sha256"] = rosetta_call.output_sha256
        structure_sources = [calls_by_id[item] for item in sorted(boltz_ids | audit_ids)]
        rosetta_sources = [calls_by_id[item] for item in sorted(rosetta_ids)]
        structure_logical = "v37:structure"
        structure_stop = await _persist_v37_stop(
            session,
            run_id=run_id,
            logical_id=structure_logical,
            stop_reason="completed_all_frozen_poses",
        )
        structure_output = {
            "source_tool_call_ids": sorted(str(item.id) for item in structure_sources),
            "pose_count": len(pose_rows),
        }

        async def project_structure() -> dict[str, Any]:
            if len(pose_rows) != len(candidate_ids) * poses_per_candidate:
                raise ValueError("v37 structure projection coverage drifted")
            return structure_output

        structure_output = await execute_v37_durable_attempt(
            project_structure,
            context=V37AttemptContext(
                run_id=run_id,
                logical_id=structure_logical,
                activity_name="persist_v37_structure_stage_summaries",
                attempt=activity.info().attempt,
            ),
        )
        structure = await _persist_v37_node(
            session,
            run_id=run_id,
            manifest=manifest,
            logical_id=structure_logical,
            environment_sha256=sha256_json({"summary": "v37-structure"}),
            input_payload={"candidate_ids": candidate_ids},
            parameters={"all_poses_required": True},
            output_payload=structure_output,
            artifacts={
                "pose_manifest": {"poses": pose_rows},
                "coordinate_audit": {
                    "pose_audits": [pose["interface_audit"] for pose in flattened_poses]
                },
                "structure_inputs_outputs": structure_output,
                "stop_event": structure_stop,
            },
            model_uri="deterministic://v37-structure-projection",
        )
        rosetta_logical = "v37:rosetta"
        rosetta_stop = await _persist_v37_stop(
            session,
            run_id=run_id,
            logical_id=rosetta_logical,
            stop_reason="completed_all_frozen_decoys",
        )
        rosetta_output = {
            "source_tool_call_ids": sorted(str(item.id) for item in rosetta_sources),
            "decoy_count": len(decoy_rows),
        }

        async def project_rosetta() -> dict[str, Any]:
            if len(decoy_rows) != len(pose_rows) * rosetta_decoys_per_pose:
                raise ValueError("v37 Rosetta projection coverage drifted")
            return rosetta_output

        rosetta_output = await execute_v37_durable_attempt(
            project_rosetta,
            context=V37AttemptContext(
                run_id=run_id,
                logical_id=rosetta_logical,
                activity_name="persist_v37_structure_stage_summaries",
                attempt=activity.info().attempt,
            ),
        )
        rosetta = await _persist_v37_node(
            session,
            run_id=run_id,
            manifest=manifest,
            logical_id=rosetta_logical,
            environment_sha256=sha256_json({"summary": "v37-rosetta"}),
            input_payload={"pose_ids": [item["pose_id"] for item in pose_rows]},
            parameters={"all_decoys_required": True, "same_protocol_relative_only": True},
            output_payload=rosetta_output,
            artifacts={
                "decoy_manifest": {"decoys": decoy_rows},
                "rosetta_inputs_outputs": rosetta_output,
                "stop_event": rosetta_stop,
            },
            model_uri="deterministic://v37-rosetta-projection",
        )
        for item in structure_sources:
            await repository.record_tool_dependency(
                structure.id, item.id, "summarizes_structure_evidence"
            )
        for item in rosetta_sources:
            await repository.record_tool_dependency(
                rosetta.id, item.id, "summarizes_rosetta_evidence"
            )
        await repository.record_tool_dependency(
            rosetta.id, structure.id, "scores_frozen_structure_stage"
        )
    return {
        "structure_call_id": str(structure.id),
        "rosetta_call_id": str(rosetta.id),
        "poses": pose_rows,
        "decoys": decoy_rows,
    }


@activity.defn(name="persist_v37_final_portfolio_and_replay")
async def persist_v37_final_portfolio_and_replay(
    request: dict[str, Any],
) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    manifest = request["manifest"]
    async with SessionFactory() as session, session.begin():
        candidates = await _candidate_payloads(session, run_id)
        eligible_ids = {
            str(item["candidate_id"])
            for item in request["pepshot"]["inspections"]
            if item["interface_verdict"] == "PASS" and item["disposition"] == "retain"
        }
        if eligible_ids != set(request["structurally_eligible_candidate_ids"]):
            raise ValueError("v37 final eligibility differs from PepShot evidence")
        poses_by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for pose in request["structure_summary"]["poses"]:
            poses_by_candidate[str(pose["candidate_id"])].append(pose)
        scores_by_pose: dict[str, list[float]] = defaultdict(list)
        for decoy in request["structure_summary"]["decoys"]:
            scores_by_pose[str(decoy["pose_id"])].append(
                float(decoy["interface_delta_g_reu"])
            )
        eligible = []
        for item in candidates:
            if item["id"] not in eligible_ids:
                continue
            poses = poses_by_candidate[item["id"]]
            displacements = [float(pose["peptide_backbone_displacement"]) for pose in poses]
            metrics = {
                **item["metrics"],
                "median_pair_iptm": statistics.median(float(pose["pair_iptm"]) for pose in poses),
                "median_pocket_coverage": statistics.median(
                    float(pose["pocket_coverage_fraction"]) for pose in poses
                ),
                "maximum_geometric_clash_count": max(
                    float(pose["geometric_clash_count"]) for pose in poses
                ),
                "peptide_backbone_displacement_range": max(displacements) - min(displacements),
                "median_representative_rosetta_interface_delta_g": statistics.median(
                    statistics.median(scores_by_pose[str(pose["pose_id"])])
                    for pose in poses
                ),
            }
            eligible.append({**item, "metrics": metrics})
        lanes = [
            {
                "name": lane["name"],
                "quota": lane["quota"],
                "objective_families": lane["Pareto_objective_families"],
                "required_soft_labels": lane.get("required_soft_labels", {}),
            }
            for lane in manifest["final_portfolio"]["lanes"]
        ]
        families = dict(manifest["stage_1_sequence_evaluation"]["endpoint_families"])
        families["structure"] = manifest["stage_2_structure_confirmation"][
            "Pareto_objectives"
        ]
        async def select_final() -> dict[str, Any]:
            return select_v37_lanes(
                eligible,
                lanes=lanes,
                family_objectives=families,
                maximum_similarity=0.80,
                maximum_per_generator=2,
                maximum_per_generator_seed=1,
            )

        portfolio = await execute_v37_durable_attempt(
            select_final,
            context=V37AttemptContext(
                run_id=run_id,
                logical_id="v37:final-portfolio",
                activity_name="persist_v37_final_portfolio_and_replay",
                attempt=activity.info().attempt,
            ),
        )
        summaries = {
            item["id"]: {"metrics": item["metrics"], "labels": item["labels"]}
            for item in eligible
        }
        final_payload = {
            "candidate_ids": portfolio["selected_ids"],
            "selection": portfolio,
            "candidate_summaries": summaries,
        }
        final_logical = "v37:final-portfolio"
        final_stop = await _persist_v37_stop(
            session, run_id=run_id, logical_id=final_logical,
            stop_reason="completed_frozen_final_portfolio_budget",
        )
        final_call = await _persist_v37_node(
            session,
            run_id=run_id,
            manifest=manifest,
            logical_id=final_logical,
            environment_sha256=sha256_json({"policy": "v37-final-pareto-maximin"}),
            input_payload={"eligible_candidate_ids": sorted(eligible_ids)},
            parameters={"manifest_sha256": sha256_json(manifest), "weighted_total": False},
            output_payload=portfolio,
            artifacts={
                "eligibility_and_exclusions": {
                    "eligible_candidate_ids": sorted(eligible_ids),
                    "excluded_candidate_ids": sorted(
                        {item["id"] for item in candidates} - eligible_ids
                    ),
                },
                "final_portfolio": final_payload,
                **_selection_witness_payloads(portfolio),
                "candidate_evidence_cards": {"candidate_summaries": summaries},
                "agent_decision": portfolio,
                "stop_event": final_stop,
            },
            model_uri="deterministic://v37-final-pareto-maximin",
        )
        all_calls = list(await session.scalars(select(ToolCall).where(ToolCall.run_id == run_id)))
        by_logical = {
            str(item.input_json.get("v37_logical_id")): item
            for item in all_calls if item.input_json.get("v37_logical_id")
        }
        await persist_v37_agent_decision(
            session, run_id=run_id, logical_id=final_logical,
            tool_call_id=final_call.id,
            observed_tool_call_ids=[
                by_logical[item].id
                for item in ("v37:stage1-shortlist", "v37:structure", "v37:rosetta", "v37:pepshot")
            ],
            prompt_text="Apply the frozen final v37 lane portfolio without scalarization.",
            response_text=json.dumps(portfolio, sort_keys=True),
            structured=portfolio,
        )
        replay_logical = "v37:replay"
        existing_graph = await build_database_evidence_graph(session, run_id)
        existing_replay = next(
            (
                item
                for item in existing_graph["tool_calls"]
                if item["input_json"].get("v37_logical_id") == replay_logical
            ),
            None,
        )
        if existing_replay is not None:
            replay_roles = {
                item["role"]
                for item in existing_graph["evidence_artifacts"]
                if item["tool_call_id"] == existing_replay["id"]
            }
            replay_decision_present = any(
                item["decision_type"] == "v37_stage_decision"
                and item["structured_json"].get("v37_logical_id") == replay_logical
                for item in existing_graph["agent_decisions"]
            )
            required_replay_roles = {
                "database_object_replay",
                "committed_graph_snapshot",
                "agent_decision",
                "stop_event",
                "attempt_ledger",
                "failure_ledger",
            }
            if replay_roles != required_replay_roles or not replay_decision_present:
                raise ValueError("v37 replay recovery found an incomplete committed closure")
            validation, existing_graph = await validate_v37_database_object_replay(
                session=session,
                run_id=run_id,
                manifest=manifest,
                graph=existing_graph,
            )
            return {
                "portfolio": portfolio,
                "portfolio_sha256": final_call.output_sha256,
                "replay_sha256": existing_replay["output_sha256"],
                "exact_database_replay": bool(validation["exact_replay"]),
                "database_graph_sha256": sha256_json(existing_graph),
            }
        replay_stop = await _persist_v37_stop(
            session, run_id=run_id, logical_id=replay_logical,
            stop_reason="completed_database_object_replay",
        )
        plan = _v37_plan(manifest)
        replay_call = await _get_or_create_pending_v37_replay_call(
            session,
            run_id=run_id,
            final_call_id=final_call.id,
            plan_sha256=plan["plan_sha256"],
            attempt=activity.info().attempt,
        )
        await persist_v37_dependencies(session, run_id=run_id, plan=plan)
        preclosure_graph = await build_database_evidence_graph(session, run_id)

        async def validate_preclosure() -> dict[str, Any]:
            result, _ = await validate_v37_database_object_replay(
                session=session,
                run_id=run_id,
                manifest=manifest,
                graph=preclosure_graph,
                allow_incomplete_replay=True,
            )
            return result

        validation = await execute_v37_durable_attempt(
            validate_preclosure,
            context=V37AttemptContext(
                run_id=run_id,
                logical_id=replay_logical,
                activity_name="persist_v37_final_portfolio_and_replay",
                attempt=activity.info().attempt,
            ),
        )
        committed_graph = await build_database_evidence_graph(session, run_id)
        validation, committed_graph = await validate_v37_database_object_replay(
            session=session,
            run_id=run_id,
            manifest=manifest,
            graph=committed_graph,
            allow_incomplete_replay=True,
        )
        committed_graph_snapshot = {
            "schema_version": "v37.committed-graph-snapshot.1",
            "committed_graph_sha256": committed_graph["graph_sha256"],
            "graph": committed_graph,
        }
        replay_payload = {
            "schema_version": "v37.database-object-replay.2",
            "database_only": True,
            "manifest_sha256": sha256_json(manifest),
            "validation_contract": "v37.database-object-replay.1",
            "preclosure_graph_sha256": preclosure_graph["graph_sha256"],
            "committed_graph_sha256": committed_graph["graph_sha256"],
            "committed_graph_snapshot_sha256": sha256_json(
                committed_graph_snapshot
            ),
            "validation": validation,
            "portfolio_sha256": final_call.output_sha256,
        }
        await _complete_v37_replay_call(
            session, call=replay_call, output_payload=replay_payload
        )
        replay_ledgers = await _durable_attempt_artifacts(
            run_id=run_id, logical_id=replay_logical
        )
        for role, payload in {
            "database_object_replay": replay_payload,
            "committed_graph_snapshot": committed_graph_snapshot,
            "agent_decision": replay_payload,
            "stop_event": replay_stop,
            **replay_ledgers,
        }.items():
            artifact = await _store_json(payload)
            await _register_artifact(
                session, replay_call.id, asdict(artifact), role, {"v37_logical_id": replay_logical}
            )
        await persist_v37_agent_decision(
            session, run_id=run_id, logical_id=replay_logical,
            tool_call_id=replay_call.id,
            observed_tool_call_ids=[final_call.id],
            prompt_text="Validate the frozen v37 evidence graph from database and objects only.",
            response_text=json.dumps(replay_payload, sort_keys=True),
            structured=replay_payload,
        )
        graph = await build_database_evidence_graph(session, run_id)
        validation, graph = await validate_v37_database_object_replay(
            session=session, run_id=run_id, manifest=manifest, graph=graph
        )
    return {
        "portfolio": portfolio,
        "portfolio_sha256": final_call.output_sha256,
        "replay_sha256": replay_call.output_sha256,
        "exact_database_replay": bool(validation["exact_replay"]),
        "database_graph_sha256": graph["graph_sha256"],
    }


@activity.defn(name="finalize_v37_run")
async def finalize_v37_run(request: dict[str, Any]) -> dict[str, Any]:
    """Mark success only after reconstructing the complete frozen closure."""
    run_id = uuid.UUID(request["run_id"])
    manifest = request["manifest"]
    async with SessionFactory() as session, session.begin():
        run = await session.scalar(
            select(ExperimentRun).where(ExperimentRun.id == run_id).with_for_update()
        )
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        validation, graph = await validate_v37_database_object_replay(
            session=session, run_id=run_id, manifest=manifest
        )
        if not validation["exact_replay"]:
            raise ValueError("v37 formal closure did not validate exactly")
        if run.status != RunStatus.SUCCEEDED:
            run.status = RunStatus.SUCCEEDED
            run.finished_at = datetime.now(UTC)
            await ExperimentRepository(session).append_event(
                "run",
                run_id,
                "run.succeeded",
                "v37-formal-closure",
                {
                    **validation,
                    "database_graph_sha256": sha256_json(graph),
                },
            )
    return {
        "run_id": str(run_id),
        "status": "succeeded",
        "validation": validation,
        "database_graph_sha256": sha256_json(graph),
    }


async def validate_v37_database_object_replay(
    *,
    session: Any,
    run_id: uuid.UUID,
    manifest: dict[str, Any],
    graph: dict[str, Any] | None = None,
    allow_incomplete_replay: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reconstruct and validate the closure from persisted bytes only."""
    if graph is None:
        graph = await build_database_evidence_graph(session, run_id)
    object_store = ContentAddressedObjectStore()
    artifact_payloads: dict[str, dict[str, Any]] = {}
    for artifact_row in graph.get("artifacts", []):
        try:
            raw = await asyncio.to_thread(
                object_store.get_bytes, artifact_row["storage_uri"]
            )
        except (KeyError, TypeError) as error:
            raise ValueError("v37 artifact object is missing or unreadable") from error
        if sha256_bytes(raw) != artifact_row["sha256"]:
            raise ValueError("v37 artifact object bytes differ from the database SHA")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            artifact_payloads[str(artifact_row["sha256"])] = payload
    plan = build_v37_evidence_plan(V37Manifest.model_validate(manifest))
    validation = _validate_v37_replay_graph(
        manifest=manifest,
        plan=plan,
        graph=graph,
        artifact_payloads_by_sha256=artifact_payloads,
        allow_incomplete_replay=allow_incomplete_replay,
    )
    return validation, graph
