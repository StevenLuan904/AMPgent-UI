from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import WorkflowAlreadyStartedError

from pepagent.db.models import Artifact, ExperimentRun, Target
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.enums import RunStatus
from pepagent.domain.schemas import ExperimentSpec
from pepagent.provenance.hashing import (
    sha256_bytes,
    sha256_file,
    sha256_json,
    sha256_text,
)
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore, StoredObject
from pepagent.v37_preregistration import (
    load_v37_preregistration,
    validate_v37_experiment_spec,
)
from pepagent.v37_provider_consumers import (
    KNOWLEDGE_ACTIVE_POLICY_SHA256,
    KNOWLEDGE_RELEASE_MANIFEST_SHA256,
    KNOWLEDGE_RELEASE_REVISION,
    KNOWLEDGE_RUNTIME_MANIFEST_SHA256,
    PEPSHOT_RELEASE_ID,
    PEPSHOT_RELEASE_MANIFEST_SHA256,
    PEPSHOT_RUNTIME_MANIFEST_SHA256,
)
from pepagent.v37_runtime_execution import (
    V37GenericRuntimeExpectation,
    V37GenericRuntimePaths,
    build_v37_generic_launch_receipt,
)
from pepagent.v37_runtime_manifests import (
    V37GeneratorRuntimeExpectation,
    verify_v37_generator_runtime_manifest,
)

_WORKFLOW_TYPE = "RapidChampionGenerationV37Workflow"
_WORKFLOW_TASK_QUEUE = "pepagent-control-v37"
_WORKFLOW_MEMO_KEY = "v37_submission_identity"


async def ensure_no_existing_v37_run(
    session: Any, *, benchmark_id: str, benchmark_version: str
) -> None:
    duplicate = await session.scalar(
        select(ExperimentRun).where(
            ExperimentRun.spec_json["benchmark_id"].astext == benchmark_id,
            ExperimentRun.spec_json["benchmark_version"].astext == benchmark_version,
        )
    )
    if duplicate is not None:
        raise ValueError(f"v37 formal run already exists: {duplicate.id}")


def build_v37_formal_submission_key(
    *, benchmark_id: str, benchmark_version: str, manifest_sha256: str
) -> str:
    return sha256_json(
        {
            "benchmark_id": benchmark_id,
            "benchmark_version": benchmark_version,
            "manifest_sha256": manifest_sha256,
        }
    )


def build_v37_workflow_id(formal_submission_key: str) -> str:
    return f"pepagent-rapid-champion-v37-{formal_submission_key}"


def _same_v37_submission(existing: ExperimentRun, raw_spec: dict[str, Any]) -> bool:
    immutable_keys = (
        "benchmark_id",
        "benchmark_version",
        "manifest_sha256",
        "submission_preflight_sha256",
        "execution_bundle_sha256",
        "experiment_spec_sha256",
        "formal_submission_key",
    )
    return all(existing.spec_json.get(key) == raw_spec[key] for key in immutable_keys)


async def _reserve_v37_formal_run(
    session: Any,
    *,
    spec: ExperimentSpec,
    raw_spec: dict[str, Any],
    formal_submission_key: str,
    workflow_id: str,
) -> ExperimentRun:
    """Atomically create or recover the one database identity for this submission."""

    target_digest = sha256_text(spec.target.sequence)
    target_insert = (
        postgresql_insert(Target)
        .values(
            id=uuid.uuid4(),
            name=spec.target.name,
            organism=spec.target.organism,
            accession=spec.target.accession,
            sequence=spec.target.sequence,
            sequence_sha256=target_digest,
            metadata_json={
                "pocket_residues": spec.target.pocket_residues,
                "source_database": spec.target.source_database,
                "source_uri": spec.target.source_uri,
                "source_version": spec.target.source_version,
                "source_retrieved_at": (
                    spec.target.source_retrieved_at.isoformat()
                    if spec.target.source_retrieved_at
                    else None
                ),
            },
        )
        .on_conflict_do_nothing(index_elements=[Target.sequence_sha256])
        .returning(Target.id)
    )
    target_id = (await session.execute(target_insert)).scalar_one_or_none()
    if target_id is None:
        target_id = await session.scalar(
            select(Target.id).where(Target.sequence_sha256 == target_digest)
        )
    if target_id is None:
        raise RuntimeError("v37 target reservation did not materialize")

    proposed_run_id = uuid.uuid4()
    run_insert = (
        postgresql_insert(ExperimentRun)
        .values(
            id=proposed_run_id,
            target_id=target_id,
            spec_json=raw_spec,
            spec_sha256=sha256_json(raw_spec),
            formal_submission_key=formal_submission_key,
            status=RunStatus.CREATED,
            temporal_workflow_id=workflow_id,
        )
        .on_conflict_do_nothing(index_elements=[ExperimentRun.formal_submission_key])
        .returning(ExperimentRun.id)
    )
    inserted_run_id = (await session.execute(run_insert)).scalar_one_or_none()
    run = await session.scalar(
        select(ExperimentRun).where(ExperimentRun.formal_submission_key == formal_submission_key)
    )
    if run is None:
        raise RuntimeError("v37 formal run reservation did not materialize")
    if not _same_v37_submission(run, raw_spec):
        raise ValueError(f"different v37 formal submission owns key: {run.id}")
    if run.temporal_workflow_id != workflow_id:
        raise ValueError("v37 database workflow reservation drifted")
    if inserted_run_id is not None:
        repository = ExperimentRepository(session)
        await repository.append_event(
            "run",
            run.id,
            "run.created",
            "v37-exact-once-submission-cli",
            raw_spec,
        )
        await repository.append_event(
            "run",
            run.id,
            "run.workflow_reserved",
            "v37-exact-once-submission-cli",
            {"workflow_id": workflow_id},
        )
    return run


async def _start_or_recover_workflow(
    client: Client,
    *,
    workflow_id: str,
    request: dict[str, Any],
    request_sha256: str,
    run_id: str,
    formal_submission_key: str,
) -> WorkflowHandle:
    identity = {
        "workflow_type": _WORKFLOW_TYPE,
        "request_sha256": request_sha256,
        "run_id": run_id,
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
            raise ValueError("existing v37 workflow type differs from reservation") from error
        memo = getattr(description, "memo", None)
        if not isinstance(memo, dict) or memo.get(_WORKFLOW_MEMO_KEY) != identity:
            raise ValueError("existing v37 workflow submission identity drifted") from error
        return handle


def _validate_content_addressed_binding(
    *, role: str, payload: bytes, binding: dict[str, Any]
) -> None:
    digest = sha256_bytes(payload)
    if binding.get("sha256") != digest or binding.get("size_bytes") != len(payload):
        raise ValueError(f"v37 {role} bytes differ from preflight artifact")
    uri = str(binding.get("storage_uri", ""))
    if not uri.startswith("s3://") or not uri.endswith(f"/{digest}"):
        raise ValueError(f"v37 {role} preflight artifact URI is invalid")


def _validate_self_hashed_runtime(value: Any, *, label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"v37 {label} runtime must be an object")
    identity = value.get("runtime_identity_sha256")
    payload = {key: item for key, item in value.items() if key != "runtime_identity_sha256"}
    if identity != sha256_json(payload):
        raise ValueError(f"v37 {label} runtime identity drifted")


def _validate_generic_execution_guard(runtime: dict[str, Any], *, label: str) -> None:
    guard = runtime.get("execution_guard")
    if not isinstance(guard, dict) or set(guard) != {"contract", "expectation", "paths"}:
        raise ValueError(f"v37 {label} runtime lacks an exact execution guard")
    expectation = V37GenericRuntimeExpectation(**guard["expectation"])
    paths_payload = guard["paths"]
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
    entities = guard["contract"]["command_entities"]
    command = ["v37-guard-placeholder"] * (
        max(
            int(entities["executable_index"]),
            int(entities["adapter_index"] or 0),
        )
        + 1
    )
    command[int(entities["executable_index"])] = str(paths.executable_path)
    if entities["adapter_index"] is not None:
        command[int(entities["adapter_index"])] = str(paths.adapter_path)
    build_v37_generic_launch_receipt(
        contract=guard["contract"],
        expectation=expectation,
        paths=paths,
        command=command,
        cwd=Path(runtime.get("cwd") or Path.cwd()),
        env={},
        input_paths={},
    )


def _verify_generator_runtime_bytes(
    *, workspace: Path, runtime_root: Path, generator_id: str, runtime: dict[str, Any]
) -> None:
    def check(path: Path, expected_sha256: str, label: str) -> None:
        if not path.is_file() or sha256_file(path) != expected_sha256:
            raise ValueError(f"v37 {generator_id} {label} bytes drifted")

    check(
        workspace / runtime["runtime"]["python_executable"],
        runtime["runtime"]["python_executable_sha256"],
        "Python executable",
    )
    check(
        runtime_root / f"{generator_id}.packages.lock.txt",
        runtime["runtime"]["packages_lock_sha256"],
        "package lock",
    )
    check(
        workspace / runtime["adapter"]["entrypoint"],
        runtime["adapter"]["sha256"],
        "adapter",
    )
    for release_name in ("source_release", "model_release"):
        release = runtime[release_name]
        uri = str(release["uri"])
        prefix = "workspace-release://"
        if not uri.startswith(prefix):
            # External source URIs are still frozen by their exact manifest; their
            # local materialization root is carried by each file list only when
            # provider-owned. The model releases used here are workspace releases.
            if release_name == "source_release" and generator_id != "hydramp":
                definitions = {
                    "ampgan_v2": "var/research/amp_gan",
                    "amp_designer": (
                        "var/generator-sources/"
                        "amp-designer-b554b1ac1507040d9d50356e037098e652ce4719"
                    ),
                }
                root = workspace / definitions[generator_id]
            else:
                raise ValueError(f"v37 {generator_id} {release_name} has no local byte root")
        else:
            root = workspace / uri.removeprefix(prefix)
        declared_paths = {str(item["path"]) for item in release["files"]}
        observed_paths = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.relative_to(root).parts
            and path.suffix != ".pyc"
        }
        if observed_paths != declared_paths:
            unexpected = sorted(observed_paths - declared_paths)
            missing = sorted(declared_paths - observed_paths)
            raise ValueError(
                f"v37 {generator_id} {release_name} inventory drifted; "
                f"missing={missing}, unexpected={unexpected}"
            )
        for item in release["files"]:
            path = root / item["path"]
            check(path, item["sha256"], f"{release_name} file {item['path']}")
            if path.stat().st_size != int(item["size_bytes"]):
                raise ValueError(f"v37 {generator_id} {release_name} file size drifted")


def _validate_execution_runtime_identities(
    *, execution: dict[str, Any], manifest: Any, workspace: Path
) -> None:
    runtime_root = workspace / "config/environments/v37_generator_runtimes"
    index = json.loads((runtime_root / "runtime-index.json").read_text(encoding="utf-8"))
    if index.get("runtime_index_sha256") != sha256_json(
        {key: value for key, value in index.items() if key != "runtime_index_sha256"}
    ):
        raise ValueError("v37 generator runtime index identity drifted")
    by_id = {item["generator_id"]: item for item in index["entries"]}
    for generator_id, runtime in execution["generator_runtimes"].items():
        entry = by_id.get(generator_id)
        if entry is None or entry.get("status") != "verified":
            raise ValueError(f"v37 {generator_id} runtime is not frozen and verified")
        if runtime.get("runtime_manifest_sha256") != entry["runtime_manifest_sha256"]:
            raise ValueError(f"v37 {generator_id} execution runtime identity drifted")
        verify_v37_generator_runtime_manifest(
            runtime,
            expectation=V37GeneratorRuntimeExpectation(**entry["expectation"]),
        )
        _verify_generator_runtime_bytes(
            workspace=workspace,
            runtime_root=runtime_root,
            generator_id=generator_id,
            runtime=runtime,
        )

    for name, runtime in execution["metric_plugins_by_name"].items():
        _validate_self_hashed_runtime(runtime, label=f"metric {name}")
        if name != "physicochemical_developability":
            _validate_generic_execution_guard(runtime, label=f"metric {name}")
    metric_registry_path = workspace / "config/metrics/runtime.local.yaml"
    metric_registry_bytes = metric_registry_path.read_bytes()
    if execution.get("metric_registry_sha256") != sha256_bytes(metric_registry_bytes):
        raise ValueError("v37 metric registry bytes differ from execution bundle")
    metric_registry = yaml.safe_load(metric_registry_bytes).get("adapters", {})
    for name in execution["metric_plugins_by_name"]:
        if name == "physicochemical_developability":
            continue
        adapter = metric_registry.get(name)
        if not isinstance(adapter, dict) or adapter.get("enabled") is not True:
            raise ValueError(f"v37 metric {name} is absent or disabled in frozen registry")
    knowledge = execution["knowledge_runtime"]
    _validate_self_hashed_runtime(knowledge, label="knowledge provider")
    _validate_generic_execution_guard(knowledge, label="knowledge provider")
    expected_knowledge = {
        "release_revision": KNOWLEDGE_RELEASE_REVISION,
        "release_manifest_sha256": KNOWLEDGE_RELEASE_MANIFEST_SHA256,
        "runtime_manifest_sha256": KNOWLEDGE_RUNTIME_MANIFEST_SHA256,
        "active_policy_sha256": KNOWLEDGE_ACTIVE_POLICY_SHA256,
    }
    if any(knowledge.get(key) != value for key, value in expected_knowledge.items()):
        raise ValueError("v37 knowledge provider runtime identity drifted")
    pepshot = execution["pepshot_runtime"]
    _validate_self_hashed_runtime(pepshot, label="PepShot provider")
    _validate_generic_execution_guard(pepshot, label="PepShot provider")
    expected_pepshot = {
        "release_id": PEPSHOT_RELEASE_ID,
        "release_manifest_sha256": PEPSHOT_RELEASE_MANIFEST_SHA256,
        "runtime_manifest_sha256": PEPSHOT_RUNTIME_MANIFEST_SHA256,
    }
    if any(pepshot.get(key) != value for key, value in expected_pepshot.items()):
        raise ValueError("v37 PepShot provider runtime identity drifted")
    frozen_aux = manifest.verified_auxiliaries
    if (
        knowledge.get("runtime_manifest_sha256")
        != frozen_aux["knowledge_cards"]["runtime_manifest_sha256"]
        or pepshot.get("runtime_manifest_sha256")
        != frozen_aux["pepshot"]["runtime_manifest_sha256"]
    ):
        raise ValueError("v37 provider runtime differs from frozen benchmark")


async def _register_stored_artifact(session: Any, *, stored: StoredObject, role: str) -> None:
    statement = (
        postgresql_insert(Artifact)
        .values(
            id=uuid.uuid4(),
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            media_type=stored.media_type,
            storage_uri=stored.uri,
            metadata_json={"role": role, "stage": "v37_formal_submission"},
        )
        .on_conflict_do_nothing(index_elements=[Artifact.sha256])
    )
    await session.execute(statement)


def load_v37_submission_bundle(
    *,
    manifest_path: Path,
    experiment_spec_path: Path,
    execution_bundle_path: Path,
    preflight_path: Path,
    validate_live_runtimes: bool = False,
) -> tuple[dict[str, Any], ExperimentSpec, dict[str, Any], dict[str, Any]]:
    manifest = load_v37_preregistration(manifest_path)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    experiment_binding = validate_v37_experiment_spec(
        manifest,
        manifest_path,
        spec_path_override=experiment_spec_path,
    )
    spec = ExperimentSpec.model_validate(
        yaml.safe_load(experiment_spec_path.read_text(encoding="utf-8"))
    )
    execution = json.loads(execution_bundle_path.read_text(encoding="utf-8"))
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    formal_run = payload.get("formal_run", {})
    if formal_run.get("execution_authorized") is not True:
        raise ValueError("v37 benchmark has not authorized formal execution")
    implementation_revision = formal_run.get("implementation_revision")
    if not isinstance(implementation_revision, str) or not implementation_revision.strip():
        raise ValueError("v37 benchmark implementation revision is not frozen")
    if formal_run.get("submitted") is not False:
        raise ValueError("v37 benchmark already records a formal submission")
    if preflight.get("status") != "ready_to_submit_unique_run":
        raise ValueError("v37 submission preflight is not ready")
    if preflight.get("config_sha256") != sha256_bytes(manifest_path.read_bytes()):
        raise ValueError("v37 submission preflight belongs to another manifest")
    if preflight.get("experiment_spec") != experiment_binding:
        raise ValueError("v37 submission preflight experiment spec binding drifted")
    if preflight.get("formal_run_submitted") is not False:
        raise ValueError("v37 submission preflight already records a submission")
    if preflight.get("implementation_revision") != implementation_revision:
        raise ValueError("v37 preflight implementation revision drifted")
    if preflight.get("config_execution_authorized") is not True:
        raise ValueError("v37 preflight was created before execution authorization")
    preflight_identity = {
        key: value for key, value in preflight.items() if key != "submission_preflight_sha256"
    }
    if preflight.get("submission_preflight_sha256") != sha256_json(preflight_identity):
        raise ValueError("v37 submission preflight self-hash drifted")
    manifest_sha256 = sha256_json(payload)
    formal_submission_key = build_v37_formal_submission_key(
        benchmark_id=manifest.benchmark_id,
        benchmark_version=manifest.version,
        manifest_sha256=manifest_sha256,
    )
    if preflight.get("formal_submission_key") != formal_submission_key:
        raise ValueError("v37 submission preflight formal identity drifted")
    required_runtime_keys = {
        "generator_runtimes",
        "metric_plugins_by_name",
        "knowledge_runtime",
        "knowledge_query",
        "pepshot_runtime",
        "metric_registry_sha256",
    }
    if not required_runtime_keys.issubset(execution):
        raise ValueError("v37 execution bundle is incomplete")
    expected_generators = {item["generator_id"] for item in manifest.generators["engines"]}
    if set(execution["generator_runtimes"]) != expected_generators:
        raise ValueError("v37 generator runtime set drifted")
    expected_metrics = {
        item["name"] for item in manifest.stage_1_sequence_evaluation["metric_plugins"]
    }
    if set(execution["metric_plugins_by_name"]) != expected_metrics:
        raise ValueError("v37 metric plugin set drifted")
    bindings = preflight.get("immutable_inputs")
    if not isinstance(bindings, dict):
        raise ValueError("v37 preflight lacks immutable input artifacts")
    for role, path in {
        "manifest": manifest_path,
        "experiment_spec": experiment_spec_path,
        "execution_bundle": execution_bundle_path,
        "metric_registry": manifest_path.resolve().parents[1] / "metrics/runtime.local.yaml",
    }.items():
        binding = bindings.get(role)
        if not isinstance(binding, dict):
            raise ValueError(f"v37 preflight lacks {role} artifact binding")
        _validate_content_addressed_binding(role=role, payload=path.read_bytes(), binding=binding)
    if validate_live_runtimes:
        _validate_execution_runtime_identities(
            execution=execution,
            manifest=manifest,
            workspace=manifest_path.resolve().parents[2],
        )
    return payload, spec, execution, preflight


async def submit_v37_once(
    *,
    manifest_path: Path,
    experiment_spec_path: Path,
    execution_bundle_path: Path,
    preflight_path: Path,
) -> dict[str, Any]:
    manifest, spec, execution, preflight = load_v37_submission_bundle(
        manifest_path=manifest_path,
        experiment_spec_path=experiment_spec_path,
        execution_bundle_path=execution_bundle_path,
        preflight_path=preflight_path,
        validate_live_runtimes=True,
    )
    manifest_bytes = await asyncio.to_thread(manifest_path.read_bytes)
    execution_bundle_bytes = await asyncio.to_thread(execution_bundle_path.read_bytes)
    experiment_spec_bytes = await asyncio.to_thread(experiment_spec_path.read_bytes)
    preflight_bytes = await asyncio.to_thread(preflight_path.read_bytes)
    metric_registry_path = manifest_path.parents[1] / "metrics/runtime.local.yaml"
    metric_registry_bytes = await asyncio.to_thread(metric_registry_path.read_bytes)
    raw_experiment_spec = yaml.safe_load(experiment_spec_bytes)
    raw_spec = {
        **spec.model_dump(mode="json"),
        "run_mode": "v37_rapid_champion_generation",
        "benchmark_id": manifest["benchmark_id"],
        "benchmark_version": manifest["version"],
        "manifest_sha256": sha256_json(manifest),
        "submission_preflight_sha256": preflight["submission_preflight_sha256"],
        "execution_bundle_sha256": sha256_bytes(execution_bundle_bytes),
        "experiment_spec_sha256": sha256_bytes(experiment_spec_bytes),
        "all_agent_evidence_persisted": True,
        "database_object_store_replay_required": True,
    }
    formal_submission_key = build_v37_formal_submission_key(
        benchmark_id=raw_spec["benchmark_id"],
        benchmark_version=raw_spec["benchmark_version"],
        manifest_sha256=raw_spec["manifest_sha256"],
    )
    raw_spec["formal_submission_key"] = formal_submission_key
    workflow_id = build_v37_workflow_id(formal_submission_key)
    object_store = await asyncio.to_thread(ContentAddressedObjectStore)
    for role, binding in preflight["immutable_inputs"].items():
        persisted = await asyncio.to_thread(object_store.get_bytes, binding["storage_uri"])
        _validate_content_addressed_binding(role=role, payload=persisted, binding=binding)
    stored_inputs: dict[str, StoredObject] = {}
    for role, payload, media_type in (
        ("manifest", manifest_bytes, "application/yaml"),
        ("experiment_spec", experiment_spec_bytes, "application/yaml"),
        ("execution_bundle", execution_bundle_bytes, "application/json"),
        ("submission_preflight", preflight_bytes, "application/json"),
        ("metric_registry", metric_registry_bytes, "application/yaml"),
    ):
        stored_inputs[role] = await asyncio.to_thread(object_store.put_bytes, payload, media_type)
    raw_spec["submission_input_artifacts"] = {
        role: {
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
            "media_type": item.media_type,
            "storage_uri": item.uri,
        }
        for role, item in sorted(stored_inputs.items())
    }
    async with SessionFactory() as session, session.begin():
        for role, stored in stored_inputs.items():
            await _register_stored_artifact(session, stored=stored, role=role)
        run = await _reserve_v37_formal_run(
            session,
            spec=spec,
            raw_spec=raw_spec,
            formal_submission_key=formal_submission_key,
            workflow_id=workflow_id,
        )
        run_id = str(run.id)
        request = {
            "run_id": run_id,
            "manifest": manifest,
            "experiment_spec": raw_experiment_spec,
            "submission_preflight": preflight,
            **execution,
        }
        request_bytes = json.dumps(
            request, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        request_sha256 = sha256_bytes(request_bytes)
        stored_request = await asyncio.to_thread(
            object_store.put_bytes, request_bytes, "application/json"
        )
        await _register_stored_artifact(
            session, stored=stored_request, role="temporal_workflow_request"
        )
        updated_spec = {
            **run.spec_json,
            "workflow_request_sha256": request_sha256,
            "workflow_request_artifact": {
                "sha256": stored_request.sha256,
                "size_bytes": stored_request.size_bytes,
                "media_type": stored_request.media_type,
                "storage_uri": stored_request.uri,
            },
        }
        run.spec_json = updated_spec
        run.spec_sha256 = sha256_json(updated_spec)
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    await _start_or_recover_workflow(
        client,
        workflow_id=workflow_id,
        request=request,
        request_sha256=request_sha256,
        run_id=run_id,
        formal_submission_key=formal_submission_key,
    )
    return {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "manifest_sha256": raw_spec["manifest_sha256"],
        "formal_submission_key": formal_submission_key,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit the unique v37 formal run")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--experiment-spec", type=Path, required=True)
    parser.add_argument("--execution-bundle", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("v37 submission is inert without explicit --execute")
    result = asyncio.run(
        submit_v37_once(
            manifest_path=args.manifest.resolve(),
            experiment_spec_path=args.experiment_spec.resolve(),
            execution_bundle_path=args.execution_bundle.resolve(),
            preflight_path=args.preflight.resolve(),
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
