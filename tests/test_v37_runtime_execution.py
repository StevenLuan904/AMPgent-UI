from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_runtime_execution import (
    V37GenericRuntimeExpectation,
    V37GenericRuntimePaths,
    V37LiveRuntimePaths,
    build_v37_frozen_adapter_command,
    build_v37_generic_launch_receipt,
    build_v37_live_launch_receipt,
    resolve_v37_frozen_invocation,
    run_v37_guarded_provider_subprocess,
    run_v37_guarded_subprocess,
)
from pepagent.v37_runtime_manifests import (
    V37_RUNTIME_MANIFEST_SCHEMA,
    V37GeneratorRuntimeExpectation,
)


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(
    tmp_path: Path,
) -> tuple[dict[str, object], V37GeneratorRuntimeExpectation, V37LiveRuntimePaths]:
    adapter = tmp_path / "adapter.py"
    lock = tmp_path / "packages.lock"
    source = tmp_path / "source"
    model = tmp_path / "model"
    source.mkdir()
    model.mkdir()
    adapter.write_bytes(
        b"import os, time\n"
        b"time.sleep(float(os.environ.get('V37_TEST_SLEEP', '0')))\n"
        b"print('adapter')\n"
    )
    lock.write_bytes(b"example==1\n")
    (source / "source.py").write_bytes(b"VALUE = 1\n")
    (model / "weights.bin").write_bytes(b"weights")
    source_files = [{"path": "source.py", "size_bytes": 10, "sha256": _sha(source / "source.py")}]
    model_files = [{"path": "weights.bin", "size_bytes": 7, "sha256": _sha(model / "weights.bin")}]
    source_identity = {
        "uri": "release://source",
        "revision": "source-rev",
        "files_sha256": sha256_json(source_files),
        "files": source_files,
    }
    model_identity = {
        "uri": "release://model",
        "revision": "model-rev",
        "files_sha256": sha256_json(model_files),
        "files": model_files,
    }
    source_release = {**source_identity, "manifest_sha256": sha256_json(source_identity)}
    model_release = {**model_identity, "manifest_sha256": sha256_json(model_identity)}
    request_contract = {"generator_id": "hydramp", "raw_proposal_budget": 1000}
    manifest: dict[str, object] = {
        "schema_version": V37_RUNTIME_MANIFEST_SCHEMA,
        "generator_id": "hydramp",
        "adapter": {
            "entrypoint": "adapter.py",
            "sha256": _sha(adapter),
            "adapter_version": "adapter-v1",
        },
        "runtime": {
            "python_executable": str(Path(sys.executable).name),
            "python_executable_sha256": _sha(Path(sys.executable)),
            "python_version": "test",
            "environment_sha256": "e" * 64,
            "packages_lock_sha256": _sha(lock),
        },
        "source_release": source_release,
        "model_release": model_release,
        "request_contract": request_contract,
        "request_contract_sha256": sha256_json(request_contract),
        "internal_score_filtering_enabled": False,
        "unsafe_deserialization_enabled": False,
    }
    manifest["runtime_manifest_sha256"] = sha256_json(manifest)
    expectation = V37GeneratorRuntimeExpectation(
        generator_id="hydramp",
        adapter_sha256=_sha(adapter),
        adapter_version="adapter-v1",
        source_revision="source-rev",
        source_manifest_sha256=source_release["manifest_sha256"],
        model_revision="model-rev",
        model_manifest_sha256=model_release["manifest_sha256"],
        request_contract_sha256=sha256_json(request_contract),
    )
    paths = V37LiveRuntimePaths(adapter, Path(sys.executable), lock, source, model)
    return manifest, expectation, paths


def test_live_launch_rehash_rejects_bytes_mutated_after_preflight(tmp_path: Path) -> None:
    manifest, expectation, paths = _fixture(tmp_path)
    build_v37_live_launch_receipt(
        manifest=manifest,
        expectation=expectation,
        paths=paths,
        command=[sys.executable, str(paths.adapter_path)],
        cwd=tmp_path,
    )
    (paths.model_root / "weights.bin").write_bytes(b"mutated")
    with pytest.raises(ValueError, match="live model release bytes drifted"):
        build_v37_live_launch_receipt(
            manifest=manifest,
            expectation=expectation,
            paths=paths,
            command=[sys.executable, str(paths.adapter_path)],
            cwd=tmp_path,
        )


def test_guarded_launch_persists_receipt_before_process_creation(tmp_path: Path) -> None:
    manifest, expectation, paths = _fixture(tmp_path)
    receipts: list[dict[str, object]] = []

    async def writer(receipt: dict[str, object]) -> None:
        receipts.append(receipt)

    output, receipt = asyncio.run(
        run_v37_guarded_subprocess(
            [sys.executable, str(paths.adapter_path)],
            manifest=manifest,
            expectation=expectation,
            paths=paths,
            receipt_writer=writer,
            cwd=tmp_path,
            env={"PYTHONPATH": str(paths.source_root), "V37_TEST": "stable"},
        )
    )
    assert output.strip() == "adapter"
    assert receipts == [receipt["pre_snapshot"]]
    assert receipt["all_boundaries_match"] is True
    assert [
        receipt[key]["stage"] for key in ("pre_snapshot", "prelaunch", "post_spawn", "completion")
    ] == ["pre_snapshot", "prelaunch", "post_spawn", "completion"]
    assert (
        len(
            {
                receipt[key]["byte_identity_sha256"]
                for key in ("pre_snapshot", "prelaunch", "post_spawn", "completion")
            }
        )
        == 1
    )
    identity = receipt["pre_snapshot"]["identity"]
    assert identity["environment_sha256"] == sha256_json(
        {"PYTHONPATH": str(paths.source_root), "V37_TEST": "stable"}
    )
    assert identity["cwd"] == str(tmp_path.resolve())
    assert receipt["launch_receipt_sha256"] == sha256_json(
        {key: value for key, value in receipt.items() if key != "launch_receipt_sha256"}
    )


def test_guarded_launch_emits_progress_while_process_is_running(tmp_path: Path) -> None:
    manifest, expectation, paths = _fixture(tmp_path)
    progress: list[str] = []

    async def writer(receipt: dict[str, object]) -> None:
        assert receipt["stage"] == "pre_snapshot"

    async def progress_writer() -> None:
        progress.append("heartbeat")

    output, _ = asyncio.run(
        run_v37_guarded_subprocess(
            [sys.executable, str(paths.adapter_path)],
            manifest=manifest,
            expectation=expectation,
            paths=paths,
            receipt_writer=writer,
            progress_writer=progress_writer,
            progress_interval_seconds=0.01,
            cwd=tmp_path,
            env={"V37_TEST_SLEEP": "0.05"},
        )
    )
    assert output.strip() == "adapter"
    assert progress


@pytest.mark.parametrize("failure_mode", ["cancel", "progress_error"])
def test_guarded_launch_terminates_spawned_process_on_cancel_or_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure_mode: str
) -> None:
    manifest, expectation, paths = _fixture(tmp_path)

    class FakeProcess:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.terminated = False
            self._finished = asyncio.Event()

        async def communicate(self) -> tuple[bytes, None]:
            await self._finished.wait()
            return b"", None

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15
            self._finished.set()

    process = FakeProcess()
    spawned = asyncio.Event()

    async def create_process(*_args: object, **_kwargs: object) -> FakeProcess:
        spawned.set()
        return process

    async def writer(_receipt: dict[str, object]) -> None:
        return None

    async def progress_writer() -> None:
        raise RuntimeError("progress persistence failed")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    async def exercise() -> None:
        task = asyncio.create_task(
            run_v37_guarded_subprocess(
                [sys.executable, str(paths.adapter_path)],
                manifest=manifest,
                expectation=expectation,
                paths=paths,
                receipt_writer=writer,
                progress_writer=progress_writer,
                progress_interval_seconds=(60.0 if failure_mode == "cancel" else 0.001),
                cwd=tmp_path,
            )
        )
        await spawned.wait()
        if failure_mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError, match="progress persistence failed"):
                await task

    asyncio.run(exercise())

    assert process.terminated is True


def test_guarded_launch_rejects_mutation_while_receipt_is_persisted(
    tmp_path: Path,
) -> None:
    manifest, expectation, paths = _fixture(tmp_path)

    async def mutating_writer(receipt: dict[str, object]) -> None:
        assert receipt["preflight_revalidated_at_launch_boundary"] is True
        (paths.source_root / "source.py").write_bytes(b"VALUE = 2\n")

    with pytest.raises(ValueError, match="live source release bytes drifted"):
        asyncio.run(
            run_v37_guarded_subprocess(
                [sys.executable, str(paths.adapter_path)],
                manifest=manifest,
                expectation=expectation,
                paths=paths,
                receipt_writer=mutating_writer,
                cwd=tmp_path,
            )
        )


def test_launch_rejects_command_entities_that_do_not_match_manifest(tmp_path: Path) -> None:
    manifest, expectation, paths = _fixture(tmp_path)
    other = tmp_path / "other.py"
    other.write_text("print('other')\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"command\[1\].*declared adapter"):
        build_v37_live_launch_receipt(
            manifest=manifest,
            expectation=expectation,
            paths=paths,
            command=[sys.executable, str(other)],
            cwd=tmp_path,
        )


def test_release_inventory_rejects_undeclared_files_but_ignores_pycache(
    tmp_path: Path,
) -> None:
    manifest, expectation, paths = _fixture(tmp_path)
    pycache = paths.source_root / "__pycache__"
    pycache.mkdir()
    (pycache / "source.cpython-311.pyc").write_bytes(b"cache")
    build_v37_live_launch_receipt(
        manifest=manifest,
        expectation=expectation,
        paths=paths,
        command=[sys.executable, str(paths.adapter_path)],
        cwd=tmp_path,
    )
    (paths.source_root / "undeclared.py").write_text("VALUE = 3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source release inventory drifted"):
        build_v37_live_launch_receipt(
            manifest=manifest,
            expectation=expectation,
            paths=paths,
            command=[sys.executable, str(paths.adapter_path)],
            cwd=tmp_path,
        )


def test_guarded_launch_binds_input_bytes_before_spawn(tmp_path: Path) -> None:
    manifest, expectation, paths = _fixture(tmp_path)
    request = tmp_path / "request.json"
    request.write_text('{"seed": 1}\n', encoding="utf-8")

    async def mutating_writer(_receipt: dict[str, object]) -> None:
        request.write_text('{"seed": 2}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="launch context drifted"):
        asyncio.run(
            run_v37_guarded_subprocess(
                [
                    sys.executable,
                    str(paths.adapter_path),
                    "--request",
                    str(request),
                ],
                manifest=manifest,
                expectation=expectation,
                paths=paths,
                receipt_writer=mutating_writer,
                cwd=tmp_path,
                env={"PYTHONPATH": str(paths.source_root)},
            )
        )


def test_guarded_launch_rehashes_again_after_spawn_and_completion(tmp_path: Path) -> None:
    manifest, expectation, paths = _fixture(tmp_path)
    paths.adapter_path.write_text(
        "import pathlib, sys\npathlib.Path(sys.argv[1]).write_text('VALUE = 9\\n')\n",
        encoding="utf-8",
    )
    manifest["adapter"]["sha256"] = _sha(paths.adapter_path)  # type: ignore[index]
    expectation = V37GeneratorRuntimeExpectation(
        **{**expectation.__dict__, "adapter_sha256": _sha(paths.adapter_path)}
    )
    manifest["runtime_manifest_sha256"] = sha256_json(
        {key: value for key, value in manifest.items() if key != "runtime_manifest_sha256"}
    )

    async def writer(_receipt: dict[str, object]) -> None:
        return None

    with pytest.raises(
        ValueError,
        match=(
            "live (?:source release bytes drifted|runtime bytes or launch context "
            "drifted across boundaries)"
        ),
    ):
        asyncio.run(
            run_v37_guarded_subprocess(
                [
                    sys.executable,
                    str(paths.adapter_path),
                    str(paths.source_root / "source.py"),
                ],
                manifest=manifest,
                expectation=expectation,
                paths=paths,
                receipt_writer=writer,
                cwd=tmp_path,
                env={"PYTHONPATH": str(paths.source_root)},
            )
        )


def _generic_fixture(
    tmp_path: Path,
) -> tuple[dict[str, object], V37GenericRuntimeExpectation, V37GenericRuntimePaths]:
    adapter = tmp_path / "provider.py"
    lock = tmp_path / "provider.lock"
    runtime_manifest = tmp_path / "runtime.json"
    source = tmp_path / "provider-source"
    model = tmp_path / "provider-model"
    source.mkdir()
    model.mkdir()
    adapter.write_text("print('provider')\n", encoding="utf-8")
    lock.write_text("provider==1\n", encoding="utf-8")
    runtime_manifest.write_text('{"release":"one"}\n', encoding="utf-8")
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    source_files = [
        {
            "path": "module.py",
            "size_bytes": (source / "module.py").stat().st_size,
            "sha256": _sha(source / "module.py"),
        }
    ]
    contract: dict[str, object] = {
        "schema_version": "v37.generic-runtime-execution.1",
        "runtime_id": "knowledge-provider",
        "runtime_manifest_sha256": _sha(runtime_manifest),
        "executable": {"path": Path(sys.executable).name, "sha256": _sha(Path(sys.executable))},
        "adapter": {"path": adapter.name, "sha256": _sha(adapter)},
        "packages_lock_sha256": _sha(lock),
        "source_release": {
            "files": source_files,
            "files_sha256": sha256_json(source_files),
        },
        "model_release": {"files": [], "files_sha256": sha256_json([])},
        "command_entities": {"executable_index": 0, "adapter_index": 1},
    }
    contract["execution_contract_sha256"] = sha256_json(contract)
    expectation = V37GenericRuntimeExpectation(
        runtime_id="knowledge-provider",
        execution_contract_sha256=str(contract["execution_contract_sha256"]),
    )
    paths = V37GenericRuntimePaths(
        executable_path=Path(sys.executable),
        runtime_manifest_path=runtime_manifest,
        packages_lock_path=lock,
        source_root=source,
        model_root=model,
        adapter_path=adapter,
    )
    return contract, expectation, paths


def test_generic_provider_guard_binds_all_four_boundaries(tmp_path: Path) -> None:
    contract, expectation, paths = _generic_fixture(tmp_path)
    persisted: list[dict[str, object]] = []
    aggregates: list[dict[str, object]] = []

    async def writer(receipt: dict[str, object]) -> None:
        persisted.append(receipt)

    async def aggregate_writer(receipt: dict[str, object]) -> None:
        aggregates.append(receipt)

    output, receipts = asyncio.run(
        run_v37_guarded_provider_subprocess(
            [sys.executable, str(paths.adapter_path)],
            contract=contract,
            expectation=expectation,
            paths=paths,
            receipt_writer=writer,
            aggregate_receipt_writer=aggregate_writer,
            cwd=tmp_path,
            env={"PYTHONPATH": str(paths.source_root)},
        )
    )
    assert output.strip() == "provider"
    assert persisted == [receipts["pre_snapshot"]]
    assert aggregates == [receipts]
    assert receipts["all_boundaries_match"] is True
    assert (
        len(
            {
                receipts[stage]["byte_identity_sha256"]
                for stage in ("pre_snapshot", "prelaunch", "post_spawn", "completion")
            }
        )
        == 1
    )


def test_generic_provider_guard_accepts_only_frozen_no_site_prefix(
    tmp_path: Path,
) -> None:
    contract, expectation, paths = _generic_fixture(tmp_path)
    contract["command_entities"] = {"executable_index": 0, "adapter_index": 2}
    contract["execution_contract_sha256"] = sha256_json(
        {key: value for key, value in contract.items() if key != "execution_contract_sha256"}
    )
    expectation = V37GenericRuntimeExpectation(
        runtime_id=expectation.runtime_id,
        execution_contract_sha256=str(contract["execution_contract_sha256"]),
    )

    receipt = build_v37_generic_launch_receipt(
        contract=contract,
        expectation=expectation,
        paths=paths,
        command=[sys.executable, "-S", str(paths.adapter_path)],
        cwd=tmp_path,
        env={},
        input_paths={},
    )
    assert receipt["identity"]["command"][1] == "-S"

    with pytest.raises(ValueError, match="must freeze Python -S"):
        build_v37_generic_launch_receipt(
            contract=contract,
            expectation=expectation,
            paths=paths,
            command=[sys.executable, "-E", str(paths.adapter_path)],
            cwd=tmp_path,
            env={},
            input_paths={},
        )


def test_generic_provider_guard_rejects_runtime_manifest_drift(tmp_path: Path) -> None:
    contract, expectation, paths = _generic_fixture(tmp_path)
    build_v37_generic_launch_receipt(
        contract=contract,
        expectation=expectation,
        paths=paths,
        command=[sys.executable, str(paths.adapter_path)],
        cwd=tmp_path,
    )
    paths.runtime_manifest_path.write_text('{"release":"two"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="runtime manifest bytes drifted"):
        build_v37_generic_launch_receipt(
            contract=contract,
            expectation=expectation,
            paths=paths,
            command=[sys.executable, str(paths.adapter_path)],
            cwd=tmp_path,
        )


def test_generic_provider_guard_rejects_declared_adapter_drift(tmp_path: Path) -> None:
    contract, expectation, paths = _generic_fixture(tmp_path)
    paths.adapter_path.write_text("print('changed')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="adapter bytes drifted"):
        build_v37_generic_launch_receipt(
            contract=contract,
            expectation=expectation,
            paths=paths,
            command=[sys.executable, str(paths.adapter_path)],
            cwd=tmp_path,
        )


def _frozen_descriptor(tmp_path: Path) -> dict:
    executable = str(tmp_path / "python.exe")
    adapter = str(tmp_path / "adapter.py")
    cwd = str(tmp_path)
    return {
        "cwd": cwd,
        "launch_argv": [executable, "-X", "utf8", adapter, "verify"],
        "invocations": {
            "formal_context_pack": {
                "argv": [
                    executable,
                    "-X",
                    "utf8",
                    adapter,
                    "context-pack",
                    "--json",
                ],
                "cwd": cwd,
            }
        },
        "execution_guard": {
            "contract": {"command_entities": {"executable_index": 0, "adapter_index": 3}},
            "paths": {"executable_path": executable, "adapter_path": adapter},
        },
    }


def test_frozen_invocation_is_consumed_without_rebuilding_provider_cli(
    tmp_path: Path,
) -> None:
    descriptor = _frozen_descriptor(tmp_path)

    command, cwd = resolve_v37_frozen_invocation(descriptor, "formal_context_pack")

    assert command == descriptor["invocations"]["formal_context_pack"]["argv"]
    assert cwd == tmp_path


def test_frozen_adapter_command_preserves_declared_adapter_prefix(
    tmp_path: Path,
) -> None:
    descriptor = _frozen_descriptor(tmp_path)

    command = build_v37_frozen_adapter_command(descriptor, ["inspect", "--spec", "request.json"])

    assert command[:4] == descriptor["launch_argv"][:4]
    assert command[4:] == ["inspect", "--spec", "request.json"]


def test_frozen_command_helpers_reject_descriptor_entity_drift(tmp_path: Path) -> None:
    descriptor = _frozen_descriptor(tmp_path)
    descriptor["execution_guard"]["paths"]["adapter_path"] = str(tmp_path / "other.py")

    with pytest.raises(ValueError, match="adapter differs"):
        resolve_v37_frozen_invocation(descriptor, "formal_context_pack")
    with pytest.raises(ValueError, match="adapter differs"):
        build_v37_frozen_adapter_command(descriptor, ["inspect"])
