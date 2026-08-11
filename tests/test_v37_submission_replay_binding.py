from __future__ import annotations

import json
from copy import deepcopy

import pytest
import yaml

from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.v37_persistence import validate_v37_submission_replay_binding


def _yaml_bytes(value: object) -> bytes:
    return yaml.safe_dump(value, sort_keys=True).encode("utf-8")


def _fixture() -> tuple[dict, dict[str, bytes], dict]:
    manifest = {"benchmark_id": "v37-fixture", "version": "1", "execution": {}}
    parsed = {
        "manifest": manifest,
        "experiment_spec": {"target": {"name": "AceA"}},
        "capacity_contract": {"schema_version": "v37.capacity.1"},
        "worker_placement_snapshot": {"schema_version": "v37.worker.1"},
        "metric_registry": {"plugins": {"metric-a": {"version": "1"}}},
    }
    raw = {
        role: (
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if role == "worker_placement_snapshot"
            else _yaml_bytes(value)
        )
        for role, value in parsed.items()
    }
    parsed["execution_bundle"] = {
        "generator_runtimes": {"generator-a": {"sha": "a"}},
        "metric_registry_sha256": sha256_bytes(raw["metric_registry"]),
    }
    parsed["execution_bundle"]["execution_bundle_identity_sha256"] = sha256_json(
        parsed["execution_bundle"]
    )
    raw["execution_bundle"] = json.dumps(
        parsed["execution_bundle"], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    identities = {
        role: {
            "sha256": sha256_bytes(payload),
            "size_bytes": len(payload),
            "media_type": (
                "application/json"
                if role in {"worker_placement_snapshot", "execution_bundle"}
                else "application/yaml"
            ),
            "storage_uri": f"s3://fixture/{sha256_bytes(payload)}",
        }
        for role, payload in raw.items()
    }
    preflight = {
        "immutable_inputs": deepcopy(identities),
        "status": "ready_to_submit_unique_run",
        "config_sha256": sha256_bytes(raw["manifest"]),
    }
    preflight["submission_preflight_sha256"] = sha256_json(preflight)
    preflight_raw = json.dumps(
        preflight, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    raw["submission_preflight"] = preflight_raw
    identities["submission_preflight"] = {
        "sha256": sha256_bytes(preflight_raw),
        "size_bytes": len(preflight_raw),
        "media_type": "application/json",
        "storage_uri": f"s3://fixture/{sha256_bytes(preflight_raw)}",
    }
    run_id = "00000000-0000-0000-0000-000000000037"
    request = {
        "run_id": run_id,
        "manifest": manifest,
        "experiment_spec": parsed["experiment_spec"],
        "capacity_contract": parsed["capacity_contract"],
        "worker_placement_snapshot": parsed["worker_placement_snapshot"],
        "submission_preflight": preflight,
        **parsed["execution_bundle"],
    }
    request_raw = json.dumps(
        request, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    request_identity = {
        "sha256": sha256_bytes(request_raw),
        "size_bytes": len(request_raw),
        "media_type": "application/json",
        "storage_uri": f"s3://fixture/{sha256_bytes(request_raw)}",
    }
    spec = {
        "benchmark_id": manifest["benchmark_id"],
        "benchmark_version": manifest["version"],
        "manifest_sha256": sha256_json(manifest),
        "experiment_spec_sha256": sha256_bytes(raw["experiment_spec"]),
        "capacity_contract_sha256": sha256_bytes(raw["capacity_contract"]),
        "worker_placement_snapshot_sha256": sha256_bytes(
            raw["worker_placement_snapshot"]
        ),
        "execution_bundle_sha256": sha256_bytes(raw["execution_bundle"]),
        "submission_preflight_sha256": preflight["submission_preflight_sha256"],
        "submission_input_artifacts": identities,
        "workflow_request_sha256": request_identity["sha256"],
        "workflow_request_artifact": request_identity,
    }
    spec["formal_submission_key"] = sha256_json(
        {
            "benchmark_id": manifest["benchmark_id"],
            "benchmark_version": manifest["version"],
            "manifest_sha256": spec["manifest_sha256"],
        }
    )
    all_raw = {**raw, "workflow_request": request_raw}
    artifacts = [
        {"sha256": identity["sha256"], **identity}
        for identity in [*identities.values(), request_identity]
    ]
    graph = {
        "run": {
            "id": run_id,
            "spec_json": spec,
            "spec_sha256": sha256_json(spec),
            "temporal_workflow_id": (
                "pepagent-rapid-champion-v37-" + spec["formal_submission_key"]
            ),
        },
        "artifacts": artifacts,
    }
    raw_by_sha = {
        sha256_bytes(payload): payload for payload in all_raw.values()
    }
    return graph, raw_by_sha, manifest


def test_submission_replay_recovers_manifest_from_database_object_evidence() -> None:
    graph, raw_by_sha, manifest = _fixture()
    assert (
        validate_v37_submission_replay_binding(
            graph=graph, artifact_bytes_by_sha256=raw_by_sha
        )
        == manifest
    )


def test_submission_replay_rejects_workflow_request_semantic_drift() -> None:
    graph, raw_by_sha, _ = _fixture()
    mutated = deepcopy(raw_by_sha)
    request_sha = graph["run"]["spec_json"]["workflow_request_sha256"]
    request = json.loads(mutated[request_sha])
    request["generator_runtimes"] = {"generator-a": {"sha": "tampered"}}
    mutated[request_sha] = json.dumps(
        request, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    with pytest.raises(ValueError, match="workflow request artifact identity"):
        validate_v37_submission_replay_binding(
            graph=graph, artifact_bytes_by_sha256=mutated
        )


def test_submission_replay_rejects_manifest_binding_drift() -> None:
    graph, raw_by_sha, _ = _fixture()
    graph["run"]["spec_json"]["manifest_sha256"] = "0" * 64
    graph["run"]["spec_sha256"] = sha256_json(graph["run"]["spec_json"])
    with pytest.raises(ValueError, match="manifest submission binding"):
        validate_v37_submission_replay_binding(
            graph=graph, artifact_bytes_by_sha256=raw_by_sha
        )
