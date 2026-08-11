from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import pepagent.v37_preflight_cli as preflight_cli
from pepagent.provenance.hashing import sha256_json
from pepagent.v37_preflight_cli import (
    V37_DYNAMIC_GATES,
    _bind_original_bytes,
    _parse_named_paths,
    load_v37_dynamic_gates,
)


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_v37_dynamic_gates_require_exact_explicit_booleans(tmp_path: Path) -> None:
    gates = {name: False for name in V37_DYNAMIC_GATES}
    path = _write_json(tmp_path / "gates.json", gates)
    loaded = load_v37_dynamic_gates(path)
    assert loaded == dict(sorted(gates.items()))
    assert loaded["worker_host_gpu_pid_role_queue_release_exact"] is False
    assert loaded["forbidden_resources_absent"] is False

    missing = dict(gates)
    del missing["forbidden_resources_absent"]
    with pytest.raises(ValueError, match="gate set differs"):
        load_v37_dynamic_gates(_write_json(tmp_path / "missing.json", missing))

    text_value = {**gates, "forbidden_resources_absent": "false"}
    with pytest.raises(ValueError, match="explicit JSON booleans"):
        load_v37_dynamic_gates(_write_json(tmp_path / "text.json", text_value))


def test_v37_named_runtime_paths_are_exact_and_unique(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    parsed = _parse_named_paths(
        [f"one={first}", f"two={second}"], label="metric runtime"
    )
    assert parsed == {"one": first.resolve(), "two": second.resolve()}
    with pytest.raises(ValueError, match="repeats one"):
        _parse_named_paths(
            [f"one={first}", f"one={second}"], label="metric runtime"
        )
    with pytest.raises(ValueError, match="NAME=PATH"):
        _parse_named_paths(["one"], label="metric runtime")


def test_v37_original_runtime_bytes_are_content_addressed(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    first.write_bytes(b'{"runtime":"one"}')

    class Store:
        def put_bytes(self, payload: bytes, media_type: str) -> object:
            import hashlib

            digest = hashlib.sha256(payload).hexdigest()
            return SimpleNamespace(
                sha256=digest,
                size_bytes=len(payload),
                media_type=media_type,
                uri=f"s3://pepagent/sha256/{digest[:2]}/{digest}",
            )

    bound = _bind_original_bytes(
        role_paths={"metric_runtime:one": first}, object_store=Store()
    )
    binding = bound["metric_runtime:one"]
    assert binding["size_bytes"] == len(first.read_bytes())
    assert str(binding["storage_uri"]).endswith(str(binding["sha256"]))


def test_v37_false_object_store_identity_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_bytes(b"runtime")

    class Store:
        def put_bytes(self, payload: bytes, media_type: str) -> object:
            return SimpleNamespace(
                sha256="0" * 64,
                size_bytes=len(payload),
                media_type=media_type,
                uri=f"s3://pepagent/sha256/00/{'0' * 64}",
            )

    with pytest.raises(OSError, match="false runtime identity"):
        _bind_original_bytes(role_paths={"runtime": path}, object_store=Store())


def test_v37_execution_bundle_uses_only_explicit_frozen_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    runtime_path = workspace / "runtime.json"
    runtime_path.parent.mkdir(parents=True)
    generator_runtime = {"runtime_manifest_sha256": "a" * 64}
    runtime_path.write_text(json.dumps(generator_runtime), encoding="utf-8")
    index = {
        "schema_version": "v37.generator-runtime-index.1",
        "overall_status": "verified",
        "entries": [
            {
                "generator_id": "generator",
                "status": "verified",
                "manifest_path": "runtime.json",
                "runtime_manifest_sha256": "a" * 64,
            }
        ],
    }
    index["runtime_index_sha256"] = sha256_json(index)
    index_path = _write_json(workspace / "index.json", index)
    manifest_path = workspace / "manifest.yaml"
    manifest_path.write_text("benchmark_id: test\n", encoding="utf-8")
    registry_path = workspace / "metrics.yaml"
    registry_path.write_text("adapters: {}\n", encoding="utf-8")
    inputs = {}
    for name in ("metric", "knowledge", "query", "pepshot"):
        inputs[name] = _write_json(workspace / f"{name}.json", {"name": name})

    class Manifest:
        generators = {"engines": [{"generator_id": "generator"}]}
        stage_1_sequence_evaluation = {"metric_plugins": [{"name": "metric"}]}

    class Store:
        def put_bytes(self, payload: bytes, media_type: str) -> object:
            import hashlib

            digest = hashlib.sha256(payload).hexdigest()
            return SimpleNamespace(
                sha256=digest,
                size_bytes=len(payload),
                media_type=media_type,
                uri=f"s3://pepagent/sha256/{digest[:2]}/{digest}",
            )

    observed = {}
    monkeypatch.setattr(preflight_cli, "load_v37_preregistration", lambda _path: Manifest())
    monkeypatch.setattr(
        preflight_cli,
        "_validate_execution_runtime_identities",
        lambda **kwargs: observed.update(kwargs),
    )
    bundle = preflight_cli.build_v37_execution_bundle(
        workspace=workspace,
        manifest_path=manifest_path,
        runtime_index_path=index_path,
        metric_runtime_paths={"metric": inputs["metric"]},
        knowledge_runtime_path=inputs["knowledge"],
        knowledge_query_path=inputs["query"],
        pepshot_runtime_path=inputs["pepshot"],
        metric_registry_path=registry_path,
        object_store=Store(),
    )
    assert observed["execution"] is bundle
    assert set(bundle["runtime_source_artifacts"]) == {
        "generator_runtime:generator",
        "generator_runtime_index",
        "knowledge_query",
        "knowledge_runtime",
        "metric_runtime:metric",
        "pepshot_runtime",
    }
    identity = bundle.pop("execution_bundle_identity_sha256")
    assert identity == sha256_json(bundle)
