from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_preflight import (
    authorize_v37_submission_preflight,
    bind_v37_submission_inputs,
    build_v37_static_preflight,
)
from pepagent.v37_submit_cli import (
    _start_or_recover_workflow,
    _validate_content_addressed_binding,
    _validate_self_hashed_runtime,
    _verify_generator_runtime_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/benchmarks/amp_rapid_champion_generation_v37.yaml"


def _immutable_inputs() -> dict[str, dict[str, object]]:
    return {
        role: {
            "sha256": character * 64,
            "size_bytes": 1,
            "media_type": media_type,
            "storage_uri": f"s3://pepagent/sha256/{character * 2}/{character * 64}",
        }
        for role, character, media_type in (
            ("manifest", "a", "application/yaml"),
            ("experiment_spec", "b", "application/yaml"),
            ("execution_bundle", "c", "application/json"),
            ("metric_registry", "d", "application/yaml"),
        )
    }


def _all_dynamic_gates() -> dict[str, bool]:
    return {
        "implementation_committed_pushed_archived": True,
        "database_schema_exact": True,
        "services_healthy_zero_active_user_workflows": True,
        "provider_releases_exact": True,
        "worker_host_gpu_pid_role_queue_release_exact": True,
        "forbidden_resources_absent": True,
        "no_existing_v37_run_or_workflow": True,
    }


def test_v37_preflight_cannot_override_frozen_config_authorization() -> None:
    static = build_v37_static_preflight(CONFIG)
    blocked = authorize_v37_submission_preflight(
        static,
        dynamic_gates=_all_dynamic_gates(),
        immutable_inputs=_immutable_inputs(),
    )
    assert blocked["status"] == "blocked"
    assert blocked["failed_gates"] == [
        "config_execution_authorized",
        "implementation_revision_frozen",
    ]


def test_v37_preflight_binds_exact_source_bytes(tmp_path: Path) -> None:
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

    paths = []
    for name, payload in (
        ("manifest.yaml", b"m"),
        ("spec.yaml", b"s"),
        ("run.json", b"e"),
        ("metrics.yaml", b"r"),
    ):
        path = tmp_path / name
        path.write_bytes(payload)
        paths.append(path)
    bindings = bind_v37_submission_inputs(
        manifest_path=paths[0],
        experiment_spec_path=paths[1],
        execution_bundle_path=paths[2],
        metric_registry_path=paths[3],
        object_store=Store(),
    )
    assert set(bindings) == {
        "manifest",
        "experiment_spec",
        "execution_bundle",
        "metric_registry",
    }


def test_v37_content_binding_rejects_replaced_bytes() -> None:
    binding = {
        "sha256": "559aead08264d5795d3909718cdd05abd49572e84fe55590eef31a88a08fdffd",
        "size_bytes": 1,
        "storage_uri": (
            "s3://pepagent/sha256/55/"
            "559aead08264d5795d3909718cdd05abd49572e84fe55590eef31a88a08fdffd"
        ),
    }
    _validate_content_addressed_binding(role="manifest", payload=b"A", binding=binding)
    with pytest.raises(ValueError, match="bytes differ"):
        _validate_content_addressed_binding(role="manifest", payload=b"B", binding=binding)


def test_v37_runtime_self_hash_rejects_replaced_command() -> None:
    runtime = {"command": ["python", "metric.py"]}
    runtime["runtime_identity_sha256"] = sha256_json(runtime)
    _validate_self_hashed_runtime(runtime, label="metric")
    runtime["command"][1] = "replacement.py"
    with pytest.raises(ValueError, match="identity drifted"):
        _validate_self_hashed_runtime(runtime, label="metric")


def test_v37_generator_runtime_rehash_rejects_replaced_adapter(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime_root = workspace / "config/environments/v37_generator_runtimes"
    runtime_root.mkdir(parents=True)
    paths = {
        "python": workspace / "runtime/python.exe",
        "lock": runtime_root / "hydramp.packages.lock.txt",
        "adapter": workspace / "adapter.py",
        "source": workspace / "source/source.py",
        "model": workspace / "model/weights.bin",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
    import hashlib

    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()  # noqa: E731
    runtime = {
        "runtime": {
            "python_executable": "runtime/python.exe",
            "python_executable_sha256": digest(paths["python"]),
            "packages_lock_sha256": digest(paths["lock"]),
        },
        "adapter": {"entrypoint": "adapter.py", "sha256": digest(paths["adapter"])},
        "source_release": {
            "uri": "workspace-release://source",
            "files": [
                {
                    "path": "source.py",
                    "size_bytes": paths["source"].stat().st_size,
                    "sha256": digest(paths["source"]),
                }
            ],
        },
        "model_release": {
            "uri": "workspace-release://model",
            "files": [
                {
                    "path": "weights.bin",
                    "size_bytes": paths["model"].stat().st_size,
                    "sha256": digest(paths["model"]),
                }
            ],
        },
    }
    _verify_generator_runtime_bytes(
        workspace=workspace,
        runtime_root=runtime_root,
        generator_id="hydramp",
        runtime=runtime,
    )
    cache = paths["source"].parent / "__pycache__/source.cpython-311.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"cache")
    _verify_generator_runtime_bytes(
        workspace=workspace,
        runtime_root=runtime_root,
        generator_id="hydramp",
        runtime=runtime,
    )
    unknown = paths["source"].parent / "replacement.py"
    unknown.write_bytes(b"unknown executable source")
    with pytest.raises(ValueError, match="inventory drifted"):
        _verify_generator_runtime_bytes(
            workspace=workspace,
            runtime_root=runtime_root,
            generator_id="hydramp",
            runtime=runtime,
        )
    unknown.unlink()
    paths["adapter"].write_bytes(b"replacement")
    with pytest.raises(ValueError, match="adapter bytes drifted"):
        _verify_generator_runtime_bytes(
            workspace=workspace,
            runtime_root=runtime_root,
            generator_id="hydramp",
            runtime=runtime,
        )


@pytest.mark.asyncio
async def test_v37_already_started_requires_exact_server_identity() -> None:
    identity = {
        "workflow_type": "RapidChampionGenerationV37Workflow",
        "request_sha256": "a" * 64,
        "run_id": "run-1",
        "formal_submission_key": "b" * 64,
    }

    class Handle:
        async def describe(self) -> object:
            return SimpleNamespace(
                workflow_type="RapidChampionGenerationV37Workflow",
                memo={"v37_submission_identity": identity},
            )

    class Client:
        async def start_workflow(self, *_args: object, **_kwargs: object) -> object:
            raise WorkflowAlreadyStartedError("workflow", "type")

        def get_workflow_handle(self, _workflow_id: str) -> Handle:
            return Handle()

    recovered = await _start_or_recover_workflow(
        Client(),  # type: ignore[arg-type]
        workflow_id="workflow",
        request={"run_id": "run-1"},
        request_sha256="a" * 64,
        run_id="run-1",
        formal_submission_key="b" * 64,
    )
    assert isinstance(recovered, Handle)


@pytest.mark.parametrize(
    ("field", "drifted"),
    (
        ("request_sha256", "c" * 64),
        ("run_id", "other-run"),
        ("formal_submission_key", "d" * 64),
    ),
)
@pytest.mark.asyncio
async def test_v37_already_started_rejects_submission_identity_drift(
    field: str, drifted: str
) -> None:
    identity = {
        "workflow_type": "RapidChampionGenerationV37Workflow",
        "request_sha256": "a" * 64,
        "run_id": "run-1",
        "formal_submission_key": "b" * 64,
    }
    identity[field] = drifted

    class Handle:
        async def describe(self) -> object:
            return SimpleNamespace(
                workflow_type="RapidChampionGenerationV37Workflow",
                memo={"v37_submission_identity": identity},
            )

    class Client:
        async def start_workflow(self, *_args: object, **_kwargs: object) -> object:
            raise WorkflowAlreadyStartedError("workflow", "type")

        def get_workflow_handle(self, _workflow_id: str) -> Handle:
            return Handle()

    with pytest.raises(ValueError, match="submission identity drifted"):
        await _start_or_recover_workflow(
            Client(),  # type: ignore[arg-type]
            workflow_id="workflow",
            request={"run_id": "run-1"},
            request_sha256="a" * 64,
            run_id="run-1",
            formal_submission_key="b" * 64,
        )
