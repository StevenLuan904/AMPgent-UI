from __future__ import annotations

from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_bytes, sha256_file, sha256_json
from pepagent.v37_evidence import build_v37_evidence_plan
from pepagent.v37_preregistration import (
    load_v37_preregistration,
    validate_v37_experiment_spec,
)


def bind_v37_submission_inputs(
    *,
    manifest_path: Path,
    experiment_spec_path: Path,
    execution_bundle_path: Path,
    metric_registry_path: Path,
    object_store: Any,
) -> dict[str, dict[str, Any]]:
    """Persist the exact submission bytes before an execution gate can turn green."""

    sources = {
        "manifest": (manifest_path, "application/yaml"),
        "experiment_spec": (experiment_spec_path, "application/yaml"),
        "execution_bundle": (execution_bundle_path, "application/json"),
        "metric_registry": (metric_registry_path, "application/yaml"),
    }
    result: dict[str, dict[str, Any]] = {}
    for role, (path, media_type) in sources.items():
        payload = path.read_bytes()
        stored = object_store.put_bytes(payload, media_type)
        if stored.sha256 != sha256_bytes(payload) or stored.size_bytes != len(payload):
            raise OSError(f"v37 object store returned a false {role} identity")
        result[role] = {
            "sha256": stored.sha256,
            "size_bytes": stored.size_bytes,
            "media_type": stored.media_type,
            "storage_uri": stored.uri,
        }
    return result


def build_v37_static_preflight(config_path: Path) -> dict[str, Any]:
    manifest = load_v37_preregistration(config_path)
    source_contracts = manifest.generators["frozen_source_contracts"]
    verified_sources = {}
    for prefix in ("v23", "v24", "v32"):
        path = config_path.parent / source_contracts[f"{prefix}_path"]
        observed = sha256_file(path)
        expected = source_contracts[f"{prefix}_sha256"]
        if observed != expected:
            raise ValueError(f"v37 frozen {prefix} source contract drifted")
        verified_sources[prefix] = observed
    plan = build_v37_evidence_plan(manifest)
    experiment_spec = validate_v37_experiment_spec(manifest, config_path)
    manifest_sha256 = sha256_json(manifest.model_dump(mode="json"))
    formal_submission_key = sha256_json(
        {
            "benchmark_id": manifest.benchmark_id,
            "benchmark_version": manifest.version,
            "manifest_sha256": manifest_sha256,
        }
    )
    result: dict[str, Any] = {
        "schema_version": "1.2",
        "benchmark_id": manifest.benchmark_id,
        "benchmark_version": manifest.version,
        "manifest_sha256": manifest_sha256,
        "formal_submission_key": formal_submission_key,
        "config_sha256": sha256_bytes(config_path.read_bytes()),
        "evidence_plan_sha256": plan["plan_sha256"],
        "source_contract_sha256": verified_sources,
        "experiment_spec": experiment_spec,
        "config_execution_authorized": manifest.formal_run.execution_authorized,
        "implementation_revision": manifest.formal_run.implementation_revision,
        "direction_authorized": True,
        "execution_authorized": False,
        "formal_run_submitted": False,
        "host_or_service_probe_performed": False,
        "status": "direction_authorized_pending_dynamic_preexecution_gates",
    }
    result["preflight_sha256"] = sha256_json(result)
    return result


def authorize_v37_submission_preflight(
    static_record: dict[str, Any],
    *,
    dynamic_gates: dict[str, bool],
    immutable_inputs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    required = {
        "implementation_committed_pushed_archived",
        "database_schema_exact",
        "services_healthy_zero_active_user_workflows",
        "provider_releases_exact",
        "worker_host_gpu_pid_role_queue_release_exact",
        "forbidden_resources_absent",
        "no_existing_v37_run_or_workflow",
    }
    if set(dynamic_gates) != required:
        raise ValueError("v37 dynamic gate set differs from submission contract")
    failed = sorted(key for key, passed in dynamic_gates.items() if not passed)
    if static_record.get("config_execution_authorized") is not True:
        failed.append("config_execution_authorized")
    revision = static_record.get("implementation_revision")
    if not isinstance(revision, str) or not revision.strip():
        failed.append("implementation_revision_frozen")
    required_inputs = {
        "manifest",
        "experiment_spec",
        "execution_bundle",
        "metric_registry",
    }
    if immutable_inputs is None or set(immutable_inputs) != required_inputs:
        failed.append("immutable_submission_inputs_bound")
    else:
        for role in sorted(required_inputs):
            binding = immutable_inputs[role]
            required_fields = {"sha256", "size_bytes", "media_type", "storage_uri"}
            if set(binding) != required_fields:
                raise ValueError(f"v37 {role} artifact binding keys drifted")
            digest = str(binding["sha256"])
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError(f"v37 {role} artifact SHA-256 is invalid")
            if int(binding["size_bytes"]) < 1:
                raise ValueError(f"v37 {role} artifact is empty")
            if not str(binding["media_type"]) or not str(binding["storage_uri"]).startswith(
                "s3://"
            ):
                raise ValueError(f"v37 {role} artifact identity is incomplete")
            if not str(binding["storage_uri"]).endswith(f"/{digest}"):
                raise ValueError(f"v37 {role} artifact URI is not content addressed")
    failed = sorted(set(failed))
    result = {
        **static_record,
        "dynamic_gates": dict(sorted(dynamic_gates.items())),
        "immutable_inputs": immutable_inputs,
        "execution_authorized": not failed,
        "formal_run_submitted": False,
        "status": "ready_to_submit_unique_run" if not failed else "blocked",
        "failed_gates": failed,
    }
    result["submission_preflight_sha256"] = sha256_json(result)
    return result
