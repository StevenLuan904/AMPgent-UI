from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.storage.object_store import ContentAddressedObjectStore
from pepagent.v37_capacity import (
    load_v37_capacity_contract,
    validate_v37_worker_placement_snapshot,
)
from pepagent.v37_generator_launch import build_v37_generator_launch_binding
from pepagent.v37_preflight import (
    authorize_v37_submission_preflight,
    bind_v37_submission_inputs,
    build_v37_static_preflight,
)
from pepagent.v37_preregistration import load_v37_preregistration
from pepagent.v37_submit_cli import _validate_execution_runtime_identities

V37_DYNAMIC_GATES = frozenset(
    {
        "implementation_committed_pushed_archived",
        "database_schema_exact",
        "services_healthy_zero_active_user_workflows",
        "provider_releases_exact",
        "worker_host_gpu_pid_role_queue_release_exact",
        "forbidden_resources_absent",
        "no_existing_v37_run_or_workflow",
    }
)


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"v37 {label} must be a JSON object")
    return value


def load_v37_dynamic_gates(path: Path) -> dict[str, bool]:
    """Load an exact, explicit gate record; no gate is inferred or defaulted."""

    value = _load_json_object(path, label="dynamic gates")
    if set(value) != V37_DYNAMIC_GATES:
        missing = sorted(V37_DYNAMIC_GATES - set(value))
        unexpected = sorted(set(value) - V37_DYNAMIC_GATES)
        raise ValueError(
            "v37 dynamic gate set differs from submission contract; "
            f"missing={missing}, unexpected={unexpected}"
        )
    if any(type(item) is not bool for item in value.values()):
        raise ValueError("v37 dynamic gates must be explicit JSON booleans")
    return {key: value[key] for key in sorted(value)}


def _parse_named_paths(values: list[str], *, label: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path:
            raise ValueError(f"v37 {label} must use NAME=PATH")
        if name in result:
            raise ValueError(f"v37 {label} repeats {name}")
        result[name] = Path(raw_path).resolve()
    return result


def _bind_original_bytes(
    *, role_paths: dict[str, Path], object_store: Any
) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    for role, path in sorted(role_paths.items()):
        payload = path.read_bytes()
        stored = object_store.put_bytes(payload, "application/json")
        digest = sha256_bytes(payload)
        if (
            stored.sha256 != digest
            or stored.size_bytes != len(payload)
            or not stored.uri.startswith("s3://")
            or not stored.uri.endswith(f"/{digest}")
        ):
            raise OSError(f"v37 object store returned a false {role} identity")
        bindings[role] = {
            "sha256": stored.sha256,
            "size_bytes": stored.size_bytes,
            "media_type": stored.media_type,
            "storage_uri": stored.uri,
        }
    return bindings


def build_v37_execution_bundle(
    *,
    workspace: Path,
    manifest_path: Path,
    runtime_index_path: Path,
    metric_runtime_paths: dict[str, Path],
    knowledge_runtime_path: Path,
    knowledge_query_path: Path,
    pepshot_runtime_path: Path,
    metric_registry_path: Path,
    object_store: Any,
) -> dict[str, Any]:
    """Compose one live-byte-verified execution bundle from frozen inputs only."""

    workspace = workspace.resolve()
    manifest_path = manifest_path.resolve()
    manifest = load_v37_preregistration(manifest_path)
    runtime_index = _load_json_object(runtime_index_path, label="generator runtime index")
    index_identity = {
        key: value for key, value in runtime_index.items() if key != "runtime_index_sha256"
    }
    if runtime_index.get("runtime_index_sha256") != sha256_json(index_identity):
        raise ValueError("v37 generator runtime index self-hash drifted")
    if runtime_index.get("overall_status") != "verified":
        raise ValueError("v37 generator runtime index is not fully verified")

    generator_runtimes: dict[str, dict[str, Any]] = {}
    generator_launch_bindings: dict[str, dict[str, Any]] = {}
    original_paths: dict[str, Path] = {"generator_runtime_index": runtime_index_path}
    for entry in runtime_index.get("entries", []):
        if entry.get("status") != "verified":
            raise ValueError("v37 generator runtime entry is not verified")
        generator_id = str(entry["generator_id"])
        runtime_path = workspace / str(entry["manifest_path"])
        runtime = _load_json_object(runtime_path, label=f"{generator_id} runtime")
        if runtime.get("runtime_manifest_sha256") != entry.get("runtime_manifest_sha256"):
            raise ValueError(f"v37 {generator_id} runtime/index identity drifted")
        generator_runtimes[generator_id] = runtime
        generator_launch_bindings[generator_id] = build_v37_generator_launch_binding(
            workspace=workspace,
            runtime_index=runtime_index,
            entry=entry,
            manifest=runtime,
        )
        original_paths[f"generator_runtime:{generator_id}"] = runtime_path
        if generator_id == "hydramp":
            for role, key in {
                "generator_evidence:hydramp:provider_acceptance": (
                    "acceptance_receipt_path"
                ),
                "generator_evidence:hydramp:formal_seed_acceptance": (
                    "formal_seed_acceptance_receipt_path"
                ),
                "generator_evidence:hydramp:historical_blocker": (
                    "historical_blocker_receipt_path"
                ),
            }.items():
                relative = entry.get(key)
                if not isinstance(relative, str) or not relative:
                    raise ValueError(f"v37 HydrAMP runtime index lacks {key}")
                original_paths[role] = workspace / relative
            engine = next(
                item
                for item in manifest.generators["engines"]
                if item["generator_id"] == "hydramp"
            )
            consumer_path = engine.get("consumer_launch_acceptance_path")
            if not isinstance(consumer_path, str) or not consumer_path:
                raise ValueError("v37 HydrAMP consumer acceptance path is absent")
            original_paths["generator_evidence:hydramp:consumer_launch"] = (
                manifest_path.parent / consumer_path
            ).resolve()

    expected_generators = {str(item["generator_id"]) for item in manifest.generators["engines"]}
    if set(generator_runtimes) != expected_generators:
        raise ValueError("v37 generator runtime set differs from frozen benchmark")
    expected_metrics = {
        str(item["name"]) for item in manifest.stage_1_sequence_evaluation["metric_plugins"]
    }
    if set(metric_runtime_paths) != expected_metrics:
        raise ValueError("v37 metric runtime set differs from frozen benchmark")
    metric_runtimes = {
        name: _load_json_object(path, label=f"metric {name} runtime")
        for name, path in sorted(metric_runtime_paths.items())
    }
    original_paths.update(
        {f"metric_runtime:{name}": path for name, path in metric_runtime_paths.items()}
    )
    knowledge_runtime = _load_json_object(knowledge_runtime_path, label="knowledge runtime")
    knowledge_query = _load_json_object(knowledge_query_path, label="knowledge query")
    frozen_knowledge = manifest.verified_auxiliaries["knowledge"]
    frozen_query_path = (manifest_path.parent / frozen_knowledge["query_path"]).resolve()
    if knowledge_query_path.resolve() != frozen_query_path:
        raise ValueError("v37 knowledge query path differs from frozen benchmark")
    if sha256_bytes(knowledge_query_path.read_bytes()) != frozen_knowledge["query_sha256"]:
        raise ValueError("v37 knowledge query bytes differ from frozen benchmark")
    if set(knowledge_query) != {
        "schema_version",
        "target_key",
        "application",
        "query",
    }:
        raise ValueError("v37 knowledge query schema drifted")
    if knowledge_query != {
        "schema_version": "v37.knowledge-query.1",
        "target_key": "AceA",
        "application": "v37_rapid_champion_generation",
        "query": (
            "AceA targeted antimicrobial short peptide sequence design positive "
            "negative variants intracellular delivery MIC selectivity"
        ),
    }:
        raise ValueError("v37 knowledge query content drifted")
    pepshot_runtime = _load_json_object(pepshot_runtime_path, label="PepShot runtime")
    original_paths.update(
        {
            "knowledge_runtime": knowledge_runtime_path,
            "knowledge_query": knowledge_query_path,
            "pepshot_runtime": pepshot_runtime_path,
        }
    )
    metric_registry_bytes = metric_registry_path.read_bytes()
    bundle: dict[str, Any] = {
        "schema_version": "v37.execution-bundle.1",
        "generator_runtimes": generator_runtimes,
        "generator_launch_bindings": generator_launch_bindings,
        "metric_plugins_by_name": metric_runtimes,
        "knowledge_runtime": knowledge_runtime,
        "knowledge_query": knowledge_query,
        "pepshot_runtime": pepshot_runtime,
        "metric_registry_sha256": sha256_bytes(metric_registry_bytes),
        "runtime_source_artifacts": _bind_original_bytes(
            role_paths=original_paths, object_store=object_store
        ),
    }
    _validate_execution_runtime_identities(execution=bundle, manifest=manifest, workspace=workspace)
    bundle["execution_bundle_identity_sha256"] = sha256_json(bundle)
    return bundle


def build_v37_preflight_files(
    *,
    workspace: Path,
    manifest_path: Path,
    experiment_spec_path: Path,
    capacity_contract_path: Path,
    worker_placement_snapshot_path: Path,
    runtime_index_path: Path,
    metric_runtime_paths: dict[str, Path],
    knowledge_runtime_path: Path,
    knowledge_query_path: Path,
    pepshot_runtime_path: Path,
    metric_registry_path: Path,
    gates_path: Path,
    execution_bundle_output: Path,
    static_preflight_output: Path,
    submission_preflight_output: Path,
    object_store: Any,
) -> dict[str, Any]:
    manifest = load_v37_preregistration(manifest_path)
    worker_snapshot = _load_json_object(
        worker_placement_snapshot_path, label="worker placement snapshot"
    )
    validate_v37_worker_placement_snapshot(
        worker_snapshot,
        contract=load_v37_capacity_contract(capacity_contract_path),
        expected_task_queues=manifest.execution["task_queues"],
        expected_source_revision=manifest.execution["worker_source_revision"],
        reference_time=datetime.now(UTC),
    )
    bundle = build_v37_execution_bundle(
        workspace=workspace,
        manifest_path=manifest_path,
        runtime_index_path=runtime_index_path,
        metric_runtime_paths=metric_runtime_paths,
        knowledge_runtime_path=knowledge_runtime_path,
        knowledge_query_path=knowledge_query_path,
        pepshot_runtime_path=pepshot_runtime_path,
        metric_registry_path=metric_registry_path,
        object_store=object_store,
    )
    execution_bundle_output.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    static = build_v37_static_preflight(manifest_path)
    static_preflight_output.write_text(
        json.dumps(static, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    immutable_inputs = bind_v37_submission_inputs(
        manifest_path=manifest_path,
        experiment_spec_path=experiment_spec_path,
        capacity_contract_path=capacity_contract_path,
        worker_placement_snapshot_path=worker_placement_snapshot_path,
        execution_bundle_path=execution_bundle_output,
        metric_registry_path=metric_registry_path,
        object_store=object_store,
    )
    submission = authorize_v37_submission_preflight(
        static,
        dynamic_gates=load_v37_dynamic_gates(gates_path),
        immutable_inputs=immutable_inputs,
    )
    submission_preflight_output.write_text(
        json.dumps(submission, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "execution_bundle": str(execution_bundle_output),
        "static_preflight": str(static_preflight_output),
        "submission_preflight": str(submission_preflight_output),
        "status": submission["status"],
        "failed_gates": submission["failed_gates"],
        "submission_preflight_sha256": submission["submission_preflight_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build and bind the v37 execution bundle and preflights; never submit"
    )
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--experiment-spec", type=Path, required=True)
    parser.add_argument("--capacity-contract", type=Path, required=True)
    parser.add_argument("--worker-placement-snapshot", type=Path, required=True)
    parser.add_argument("--runtime-index", type=Path, required=True)
    parser.add_argument(
        "--metric-runtime",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="repeat once for every frozen v37 metric runtime",
    )
    parser.add_argument("--knowledge-runtime", type=Path, required=True)
    parser.add_argument("--knowledge-query", type=Path, required=True)
    parser.add_argument("--pepshot-runtime", type=Path, required=True)
    parser.add_argument("--metric-registry", type=Path, required=True)
    parser.add_argument("--gates", type=Path, required=True)
    parser.add_argument("--execution-bundle-output", type=Path, required=True)
    parser.add_argument("--static-preflight-output", type=Path, required=True)
    parser.add_argument("--submission-preflight-output", type=Path, required=True)
    args = parser.parse_args()
    result = build_v37_preflight_files(
        workspace=args.workspace.resolve(),
        manifest_path=args.manifest.resolve(),
        experiment_spec_path=args.experiment_spec.resolve(),
        capacity_contract_path=args.capacity_contract.resolve(),
        worker_placement_snapshot_path=args.worker_placement_snapshot.resolve(),
        runtime_index_path=args.runtime_index.resolve(),
        metric_runtime_paths=_parse_named_paths(args.metric_runtime, label="metric runtime"),
        knowledge_runtime_path=args.knowledge_runtime.resolve(),
        knowledge_query_path=args.knowledge_query.resolve(),
        pepshot_runtime_path=args.pepshot_runtime.resolve(),
        metric_registry_path=args.metric_registry.resolve(),
        gates_path=args.gates.resolve(),
        execution_bundle_output=args.execution_bundle_output.resolve(),
        static_preflight_output=args.static_preflight_output.resolve(),
        submission_preflight_output=args.submission_preflight_output.resolve(),
        object_store=ContentAddressedObjectStore(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
