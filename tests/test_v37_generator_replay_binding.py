from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import pytest

from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.v37_persistence import _validate_generator_launch_binding_evidence


def _launch_binding() -> dict[str, Any]:
    binding: dict[str, Any] = {
        "generator_id": "hydramp",
        "runtime_index_sha256": "1" * 64,
        "runtime_manifest_sha256": "2" * 64,
        "paths": {
            "python_path": "C:/runtime/python.exe",
            "adapter_path": "C:/repo/hydramp_generator_cli.py",
            "packages_lock_path": "C:/repo/hydramp.lock",
            "source_root": "C:/release/source",
            "model_root": "C:/release/model",
        },
        "device": "cpu-only",
        "materialization": {
            "kind": "verified_zip_in_activity_workdir",
            "archive_path": "C:/release/model/models.zip",
            "archive_sha256": "a" * 64,
            "decomposer_path": "C:/release/model/pca_decomposer.safe.npz",
            "model_subdirectory": "models/HydrAMP/37",
            "member_inventory_sha256": "b" * 64,
            "extracted_tree_sha256": "c" * 64,
            "member_count": 10,
            "file_count": 8,
            "uncompressed_bytes": 1234,
        },
    }
    binding["launch_binding_sha256"] = sha256_json(binding)
    return binding


def _runtime() -> dict[str, Any]:
    return {
        "runtime_manifest_sha256": "2" * 64,
        "model_release": {
            "files": [
                {"path": "models.zip", "sha256": "a" * 64},
                {"path": "pca_decomposer.safe.npz", "sha256": "d" * 64},
            ]
        },
    }


def _receipt(identity_mutator: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    cwd = "C:/work/run/v37/hydramp/123"
    identity: dict[str, Any] = {
        "generator_id": "hydramp",
        "runtime_manifest_sha256": "2" * 64,
        "cwd": cwd,
        "command": [
            "C:/runtime/python.exe",
            "C:/repo/hydramp_generator_cli.py",
            "--request",
            f"{cwd}/request.json",
            "--output",
            f"{cwd}/raw-output.json",
            "--model-path",
            f"{cwd}/hydramp-{'a' * 16}-random/models/HydrAMP/37",
            "--decomposer-path",
            "C:/release/model/pca_decomposer.safe.npz",
            "--model-archive",
            "C:/release/model/models.zip",
        ],
        "inputs": [
            {"path": f"{cwd}/request.json", "sha256": "r" * 64},
            {"path": "C:/release/model/models.zip", "sha256": "a" * 64},
            {
                "path": "C:/release/model/pca_decomposer.safe.npz",
                "sha256": "d" * 64,
            },
        ],
    }
    if identity_mutator is not None:
        identity_mutator(identity)
    stages: dict[str, Any] = {}
    for stage_name in ("pre_snapshot", "prelaunch", "post_spawn", "completion"):
        stage = {
            "stage": stage_name,
            "identity": copy.deepcopy(identity),
            "byte_identity_sha256": sha256_json(identity),
            "preflight_revalidated_at_launch_boundary": True,
        }
        stage["launch_receipt_sha256"] = sha256_json(stage)
        stages[stage_name] = stage
    receipt = {
        "schema_version": "v37.guarded-runtime-receipts.2",
        **stages,
        "byte_identity_sha256": sha256_json(identity),
        "all_boundaries_match": True,
        "returncode": 0,
    }
    receipt["launch_receipt_sha256"] = sha256_json(receipt)
    return receipt


def _fixture(
    *,
    identity_mutator: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any]]:
    materialization_receipt = {
        "schema_version": "v37.hydramp-materialization-receipt.1",
        "archive_sha256": "a" * 64,
        "member_inventory_sha256": "b" * 64,
        "extracted_tree_sha256": "c" * 64,
        "member_count": 10,
        "file_count": 8,
        "uncompressed_bytes": 1234,
        "destination_name": f"hydramp-{'a' * 16}-random",
    }
    materialization_receipt["materialization_receipt_sha256"] = sha256_json(
        materialization_receipt
    )
    raw = json.dumps(
        {
            "live_launch_receipt": _receipt(identity_mutator),
            "materialization_receipt": materialization_receipt,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    digest = sha256_bytes(raw)
    graph = {
        "tool_calls": [
            {
                "id": "call-1",
                "tool_name": "v37-generate-hydramp",
                "input_json": {"generator": "hydramp"},
            }
        ],
        "artifacts": [{"id": "artifact-1", "sha256": digest}],
        "evidence_artifacts": [
            {
                "tool_call_id": "call-1",
                "artifact_id": "artifact-1",
                "role": "source_runtime_receipt",
            }
        ],
    }
    execution = {
        "generator_launch_bindings": {"hydramp": _launch_binding()},
        "generator_runtimes": {"hydramp": _runtime()},
    }
    return graph, {digest: raw}, execution


def _validate(
    graph: dict[str, Any], objects: dict[str, bytes], execution: dict[str, Any]
) -> None:
    _validate_generator_launch_binding_evidence(
        graph=graph,
        artifact_bytes_by_sha256=objects,
        execution=execution,
    )


def test_generator_replay_binds_receipt_to_frozen_launch_and_materialization() -> None:
    _validate(*_fixture())


def test_generator_replay_rejects_missing_launch_binding() -> None:
    graph, objects, execution = _fixture()
    execution.pop("generator_launch_bindings")
    with pytest.raises(ValueError, match="binding set is incomplete"):
        _validate(graph, objects, execution)


def test_generator_replay_rejects_missing_receipt_link() -> None:
    graph, objects, execution = _fixture()
    graph["evidence_artifacts"] = []
    with pytest.raises(ValueError, match="lacks one launch receipt artifact"):
        _validate(graph, objects, execution)


def test_generator_replay_rejects_tampered_binding_hash() -> None:
    graph, objects, execution = _fixture()
    execution["generator_launch_bindings"]["hydramp"]["materialization"][
        "archive_path"
    ] = "C:/release/model/other.zip"
    with pytest.raises(ValueError, match="self-hash drifted"):
        _validate(graph, objects, execution)


def test_generator_replay_rejects_tampered_manifest_binding() -> None:
    graph, objects, execution = _fixture()
    binding = execution["generator_launch_bindings"]["hydramp"]
    binding["runtime_manifest_sha256"] = "3" * 64
    binding["launch_binding_sha256"] = sha256_json(
        {key: value for key, value in binding.items() if key != "launch_binding_sha256"}
    )
    with pytest.raises(ValueError, match="runtime identity drifted"):
        _validate(graph, objects, execution)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda identity: identity.__setitem__("generator_id", "ampgan_v2"), "launch binding"),
        (
            lambda identity: identity["command"].__setitem__(0, "C:/other/python.exe"),
            "launch binding",
        ),
        (
            lambda identity: identity["command"].__setitem__(
                identity["command"].index("--model-archive") + 1,
                "C:/release/model/other.zip",
            ),
            "materialization inputs",
        ),
        (
            lambda identity: identity["inputs"][1].__setitem__("sha256", "f" * 64),
            "materialization input bytes",
        ),
    ],
)
def test_generator_replay_rejects_semantically_tampered_receipt(
    mutator: Callable[[dict[str, Any]], None], message: str
) -> None:
    graph, objects, execution = _fixture(identity_mutator=mutator)
    with pytest.raises(ValueError, match=message):
        _validate(graph, objects, execution)
