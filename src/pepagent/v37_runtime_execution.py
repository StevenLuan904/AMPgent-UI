from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_runtime_manifests import (
    V37GeneratorRuntimeExpectation,
    verify_v37_generator_runtime_manifest,
)


@dataclass(frozen=True)
class V37LiveRuntimePaths:
    """Physical paths whose bytes are consumed by one generator launch."""

    adapter_path: Path
    python_path: Path
    packages_lock_path: Path
    source_root: Path
    model_root: Path


@dataclass(frozen=True)
class V37GenericRuntimeExpectation:
    """Frozen identity of a non-generator provider execution contract."""

    runtime_id: str
    execution_contract_sha256: str


@dataclass(frozen=True)
class V37GenericRuntimePaths:
    """Physical bytes consumed by one non-generator provider launch."""

    executable_path: Path
    runtime_manifest_path: Path
    packages_lock_path: Path
    source_root: Path
    model_root: Path
    adapter_path: Path | None = None


def resolve_v37_frozen_invocation(
    descriptor: Mapping[str, Any], invocation_name: str
) -> tuple[list[str], Path]:
    """Return an exact provider-owned invocation without rebuilding its CLI."""

    invocations = descriptor.get("invocations")
    if not isinstance(invocations, Mapping):
        raise ValueError("v37 runtime descriptor lacks frozen invocations")
    invocation = invocations.get(invocation_name)
    if not isinstance(invocation, Mapping):
        raise ValueError("v37 runtime descriptor lacks the requested frozen invocation")
    argv = invocation.get("argv")
    cwd = invocation.get("cwd")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(value, str) and value for value in argv)
        or not isinstance(cwd, str)
        or not cwd
    ):
        raise ValueError("v37 frozen invocation is incomplete")
    _validate_descriptor_command_entities(descriptor, argv)
    if cwd != descriptor.get("cwd"):
        raise ValueError("v37 frozen invocation cwd differs from the descriptor")
    return list(argv), Path(cwd)


def build_v37_frozen_adapter_command(
    descriptor: Mapping[str, Any], arguments: Sequence[str]
) -> list[str]:
    """Append provider arguments to the frozen executable/adapter launch prefix."""

    launch_argv = descriptor.get("launch_argv")
    if not isinstance(launch_argv, list) or not all(
        isinstance(value, str) and value for value in launch_argv
    ):
        raise ValueError("v37 runtime descriptor lacks its frozen launch argv")
    guard = descriptor.get("execution_guard")
    if not isinstance(guard, Mapping) or not isinstance(guard.get("contract"), Mapping):
        raise ValueError("v37 runtime descriptor lacks its execution guard")
    entities = guard["contract"].get("command_entities")
    if not isinstance(entities, Mapping) or entities.get("adapter_index") is None:
        raise ValueError("v37 runtime descriptor lacks a frozen adapter index")
    prefix_length = int(entities["adapter_index"]) + 1
    if len(launch_argv) < prefix_length:
        raise ValueError("v37 frozen launch argv does not contain its adapter")
    command = [*launch_argv[:prefix_length], *(str(value) for value in arguments)]
    _validate_descriptor_command_entities(descriptor, command)
    return command


def _validate_descriptor_command_entities(
    descriptor: Mapping[str, Any], command: Sequence[str]
) -> None:
    guard = descriptor.get("execution_guard")
    if not isinstance(guard, Mapping):
        raise ValueError("v37 runtime descriptor lacks its execution guard")
    contract = guard.get("contract")
    paths = guard.get("paths")
    if not isinstance(contract, Mapping) or not isinstance(paths, Mapping):
        raise ValueError("v37 runtime descriptor execution guard is incomplete")
    entities = contract.get("command_entities")
    if not isinstance(entities, Mapping):
        raise ValueError("v37 runtime descriptor lacks command entities")
    executable_index = int(entities.get("executable_index", -1))
    adapter_index = entities.get("adapter_index")
    if executable_index < 0 or len(command) <= executable_index:
        raise ValueError("v37 frozen command lacks its executable")
    if command[executable_index] != paths.get("executable_path"):
        raise ValueError("v37 frozen command executable differs from the descriptor")
    if adapter_index is not None:
        index = int(adapter_index)
        if len(command) <= index or command[index] != paths.get("adapter_path"):
            raise ValueError("v37 frozen command adapter differs from the descriptor")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_no_symlink(path: Path, *, label: str) -> None:
    absolute = path.absolute()
    for component in reversed((absolute, *absolute.parents)):
        if component.exists() and component.is_symlink():
            raise ValueError(f"v37 {label} path contains a symlink")


def _checked_regular_file(path: Path, *, label: str) -> Path:
    _assert_no_symlink(path, label=label)
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"v37 {label} is not a regular file")
    return resolved


def _checked_directory(path: Path, *, label: str) -> Path:
    _assert_no_symlink(path, label=label)
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"v37 {label} is not a directory")
    return resolved


def _checked_file(root: Path, relative_path: str, *, label: str) -> Path:
    resolved_root = _checked_directory(root, label=f"{label} root")
    candidate = resolved_root / relative_path
    _assert_no_symlink(candidate, label=label)
    resolved = candidate.resolve(strict=True)
    try:
        common = Path(os.path.commonpath((resolved_root, resolved)))
    except ValueError as error:
        raise ValueError(f"v37 {label} file escapes its release root") from error
    if common != resolved_root or not resolved.is_file():
        raise ValueError(f"v37 {label} file escapes its release root")
    return resolved


def _is_ignored_pycache(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return "__pycache__" in relative.parts or path.suffix.lower() in {".pyc", ".pyo"}


def _inventory_release(root: Path, *, label: str) -> tuple[list[str], list[str]]:
    resolved_root = _checked_directory(root, label=f"{label} root")
    observed: list[str] = []
    ignored_pycache: list[str] = []
    for path in resolved_root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"v37 {label} release contains a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(resolved_root).as_posix()
        if _is_ignored_pycache(path, resolved_root):
            ignored_pycache.append(relative)
        else:
            observed.append(relative)
    return sorted(observed), sorted(ignored_pycache)


def _rehash_release(
    release: Mapping[str, Any], root: Path, *, label: str, allow_empty: bool = False
) -> tuple[list[dict[str, Any]], list[str]]:
    expected = release["files"]
    if not isinstance(expected, list) or (not expected and not allow_empty):
        raise ValueError(f"v37 {label} release has no declared files")
    declared = [str(item["path"]) for item in expected]
    inventory, ignored_pycache = _inventory_release(root, label=label)
    if inventory != declared:
        missing = sorted(set(declared) - set(inventory))
        unexpected = sorted(set(inventory) - set(declared))
        raise ValueError(
            f"v37 live {label} release inventory drifted; "
            f"missing={missing}, unexpected={unexpected}"
        )
    observed: list[dict[str, Any]] = []
    for item in expected:
        path = _checked_file(root, str(item["path"]), label=label)
        observed.append(
            {
                "path": str(item["path"]),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    if observed != expected:
        raise ValueError(f"v37 live {label} release bytes drifted after preflight")
    if sha256_json(observed) != release["files_sha256"]:
        raise ValueError(f"v37 live {label} release file-list hash drifted")
    return observed, ignored_pycache


def _require_sha256(value: object, *, label: str) -> str:
    text = str(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"v37 generic runtime has invalid {label}")
    return text


def verify_v37_generic_runtime_contract(
    contract: Mapping[str, Any], *, expectation: V37GenericRuntimeExpectation
) -> None:
    required = {
        "schema_version",
        "runtime_id",
        "runtime_manifest_sha256",
        "executable",
        "adapter",
        "packages_lock_sha256",
        "source_release",
        "model_release",
        "command_entities",
        "execution_contract_sha256",
    }
    if set(contract) != required:
        raise ValueError("v37 generic runtime execution contract keys drifted")
    if contract["schema_version"] != "v37.generic-runtime-execution.1":
        raise ValueError("v37 generic runtime execution contract schema drifted")
    if contract["runtime_id"] != expectation.runtime_id:
        raise ValueError("v37 generic runtime identity drifted")
    identity = {key: value for key, value in contract.items() if key != "execution_contract_sha256"}
    contract_sha256 = _require_sha256(
        contract["execution_contract_sha256"], label="execution contract SHA-256"
    )
    if contract_sha256 != sha256_json(identity):
        raise ValueError("v37 generic runtime execution contract self-hash drifted")
    if contract_sha256 != expectation.execution_contract_sha256:
        raise ValueError("v37 generic runtime execution contract differs from expectation")
    _require_sha256(contract["runtime_manifest_sha256"], label="runtime manifest SHA-256")
    _require_sha256(contract["packages_lock_sha256"], label="package lock SHA-256")
    executable = contract["executable"]
    if not isinstance(executable, Mapping) or set(executable) != {"path", "sha256"}:
        raise ValueError("v37 generic runtime executable identity is malformed")
    _require_sha256(executable["sha256"], label="executable SHA-256")
    adapter = contract["adapter"]
    if adapter is not None:
        if not isinstance(adapter, Mapping) or set(adapter) != {"path", "sha256"}:
            raise ValueError("v37 generic runtime adapter identity is malformed")
        _require_sha256(adapter["sha256"], label="adapter SHA-256")
    entities = contract["command_entities"]
    if not isinstance(entities, Mapping) or set(entities) != {
        "executable_index",
        "adapter_index",
    }:
        raise ValueError("v37 generic runtime command entity mapping is malformed")
    if int(entities["executable_index"]) != 0:
        raise ValueError("v37 generic runtime executable must be command[0]")
    adapter_index = entities["adapter_index"]
    if (adapter is None) != (adapter_index is None):
        raise ValueError("v37 generic runtime adapter command mapping is inconsistent")
    if adapter_index is not None and int(adapter_index) < 1:
        raise ValueError("v37 generic runtime adapter index is invalid")
    for label in ("source_release", "model_release"):
        release = contract[label]
        if not isinstance(release, Mapping) or set(release) != {"files", "files_sha256"}:
            raise ValueError(f"v37 generic runtime {label} identity is malformed")
        files = release["files"]
        if not isinstance(files, list):
            raise ValueError(f"v37 generic runtime {label} files are malformed")
        if [str(item.get("path", "")) for item in files] != sorted(
            str(item.get("path", "")) for item in files
        ):
            raise ValueError(f"v37 generic runtime {label} files are not sorted")
        for item in files:
            if not isinstance(item, Mapping) or set(item) != {"path", "size_bytes", "sha256"}:
                raise ValueError(f"v37 generic runtime {label} file identity is malformed")
            _require_sha256(item["sha256"], label=f"{label} file SHA-256")
        if _require_sha256(release["files_sha256"], label=f"{label} files SHA-256") != sha256_json(
            files
        ):
            raise ValueError(f"v37 generic runtime {label} file-list hash drifted")


def _path_ends_with_declared(path: Path, declared: object) -> bool:
    declared_parts = tuple(part for part in str(declared).replace("\\", "/").split("/") if part)
    path_parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in declared_parts)
    return bool(suffix) and path_parts[-len(suffix) :] == suffix


def _validate_command_entities(
    command: Sequence[str], *, manifest: Mapping[str, Any], paths: V37LiveRuntimePaths
) -> tuple[Path, Path]:
    if len(command) < 2:
        raise ValueError("v37 launch command must declare Python and adapter entities")
    python = _checked_regular_file(Path(command[0]), label="command Python")
    adapter = _checked_regular_file(Path(command[1]), label="command adapter")
    declared_python = _checked_regular_file(paths.python_path, label="declared Python")
    declared_adapter = _checked_regular_file(paths.adapter_path, label="declared adapter")
    if not os.path.samefile(python, declared_python):
        raise ValueError("v37 command[0] is not the declared Python executable")
    if not os.path.samefile(adapter, declared_adapter):
        raise ValueError("v37 command[1] is not the declared adapter entrypoint")
    if not _path_ends_with_declared(declared_python, manifest["runtime"]["python_executable"]):
        raise ValueError("v37 declared Python path does not match the runtime manifest")
    if not _path_ends_with_declared(declared_adapter, manifest["adapter"]["entrypoint"]):
        raise ValueError("v37 declared adapter path does not match the runtime manifest")
    return python, adapter


def _normalize_environment(env: Mapping[str, str] | None) -> dict[str, str]:
    source = os.environ if env is None else env
    normalized = {str(key): str(value) for key, value in source.items()}
    if any("\x00" in key or "\x00" in value for key, value in normalized.items()):
        raise ValueError("v37 launch environment contains a NUL byte")
    return dict(sorted(normalized.items()))


def _pythonpath_identity(env: Mapping[str, str]) -> dict[str, Any]:
    value = env.get("PYTHONPATH", "")
    entries: list[str] = []
    for raw in value.split(os.pathsep) if value else []:
        if not raw:
            continue
        path = Path(raw)
        _assert_no_symlink(path, label="PYTHONPATH")
        entries.append(str(path.resolve(strict=False)))
    return {
        "value_sha256": hashlib.sha256(value.encode()).hexdigest(),
        "resolved_entries": entries,
    }


def _infer_input_paths(command: Sequence[str]) -> dict[str, Path]:
    inferred: dict[str, Path] = {}
    skip_next = False
    argument_start = 3 if len(command) > 2 and command[1] == "-S" else 2
    for index, value in enumerate(command[argument_start:], start=argument_start):
        if skip_next:
            skip_next = False
            continue
        if value == "--output":
            skip_next = True
            continue
        path = Path(value)
        if path.exists() and path.is_file():
            inferred[f"command[{index}]"] = path
    return inferred


def _input_identity(inputs: Mapping[str, Path]) -> list[dict[str, Any]]:
    observed: list[dict[str, Any]] = []
    for label, raw_path in sorted(inputs.items()):
        path = _checked_regular_file(raw_path, label=f"input {label}")
        observed.append(
            {
                "label": str(label),
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return observed


def build_v37_live_launch_receipt(
    *,
    manifest: Mapping[str, Any],
    expectation: V37GeneratorRuntimeExpectation,
    paths: V37LiveRuntimePaths,
    command: Sequence[str],
    stage: str = "pre_snapshot",
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_paths: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Build one complete byte/environment snapshot at a launch boundary."""
    if stage not in {"pre_snapshot", "prelaunch", "post_spawn", "completion"}:
        raise ValueError("v37 runtime receipt has an unknown stage")
    verified = verify_v37_generator_runtime_manifest(manifest, expectation=expectation)
    command_values = [str(item) for item in command]
    _, _ = _validate_command_entities(command_values, manifest=manifest, paths=paths)
    adapter = _checked_regular_file(paths.adapter_path, label="adapter")
    python = _checked_regular_file(paths.python_path, label="Python")
    lock = _checked_regular_file(paths.packages_lock_path, label="package lock")
    adapter_sha256 = _sha256_file(adapter)
    python_sha256 = _sha256_file(python)
    lock_sha256 = _sha256_file(lock)
    if adapter_sha256 != manifest["adapter"]["sha256"]:
        raise ValueError("v37 live adapter bytes drifted after preflight")
    if python_sha256 != manifest["runtime"]["python_executable_sha256"]:
        raise ValueError("v37 live Python executable bytes drifted after preflight")
    if lock_sha256 != manifest["runtime"]["packages_lock_sha256"]:
        raise ValueError("v37 live package lock bytes drifted after preflight")
    source_files, source_pycache = _rehash_release(
        manifest["source_release"], paths.source_root, label="source"
    )
    model_files, model_pycache = _rehash_release(
        manifest["model_release"], paths.model_root, label="model"
    )
    working_directory = _checked_directory(cwd or Path.cwd(), label="working directory")
    environment = _normalize_environment(env)
    inputs = dict(input_paths or _infer_input_paths(command_values))
    identity: dict[str, Any] = {
        "schema_version": "v37.live-runtime-byte-identity.2",
        "generator_id": expectation.generator_id,
        "runtime_manifest_sha256": manifest["runtime_manifest_sha256"],
        "adapter_sha256": adapter_sha256,
        "python_executable_sha256": python_sha256,
        "packages_lock_sha256": lock_sha256,
        "source_files_sha256": sha256_json(source_files),
        "model_files_sha256": sha256_json(model_files),
        "source_inventory": [item["path"] for item in source_files],
        "model_inventory": [item["path"] for item in model_files],
        "ignored_pycache": {
            "source": source_pycache,
            "model": model_pycache,
        },
        "command": command_values,
        "command_sha256": sha256_json(command_values),
        "cwd": str(working_directory),
        "cwd_sha256": hashlib.sha256(str(working_directory).encode()).hexdigest(),
        "environment_sha256": sha256_json(environment),
        "environment_keys": sorted(environment),
        "pythonpath": _pythonpath_identity(environment),
        "inputs": _input_identity(inputs),
        "manifest_verified": verified["verified"],
    }
    identity["input_set_sha256"] = sha256_json(identity["inputs"])
    identity_sha256 = sha256_json(identity)
    receipt: dict[str, Any] = {
        "schema_version": "v37.live-runtime-snapshot.2",
        "stage": stage,
        "byte_identity_sha256": identity_sha256,
        "identity": identity,
        "preflight_revalidated_at_launch_boundary": True,
    }
    receipt["launch_receipt_sha256"] = sha256_json(receipt)
    return receipt


def build_v37_generic_launch_receipt(
    *,
    contract: Mapping[str, Any],
    expectation: V37GenericRuntimeExpectation,
    paths: V37GenericRuntimePaths,
    command: Sequence[str],
    stage: str = "pre_snapshot",
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_paths: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Build a byte-complete launch snapshot for a non-generator provider."""
    if stage not in {"pre_snapshot", "prelaunch", "post_spawn", "completion"}:
        raise ValueError("v37 runtime receipt has an unknown stage")
    verify_v37_generic_runtime_contract(contract, expectation=expectation)
    command_values = [str(item) for item in command]
    entities = contract["command_entities"]
    executable_index = int(entities["executable_index"])
    if len(command_values) <= executable_index:
        raise ValueError("v37 generic runtime command lacks its executable")
    command_executable = _checked_regular_file(
        Path(command_values[executable_index]), label="command executable"
    )
    executable = _checked_regular_file(paths.executable_path, label="declared executable")
    if not os.path.samefile(command_executable, executable):
        raise ValueError("v37 generic runtime command executable differs from declaration")
    if not _path_ends_with_declared(executable, contract["executable"]["path"]):
        raise ValueError("v37 generic runtime executable path differs from contract")
    adapter: Path | None = None
    adapter_index = entities["adapter_index"]
    if adapter_index is not None:
        index = int(adapter_index)
        if index not in {1, 2}:
            raise ValueError("v37 generic runtime adapter index is unsupported")
        if index == 2 and (len(command_values) <= 1 or command_values[1] != "-S"):
            raise ValueError("v37 no-site runtime must freeze Python -S before its adapter")
        if len(command_values) <= index or paths.adapter_path is None:
            raise ValueError("v37 generic runtime command lacks its declared adapter")
        command_adapter = _checked_regular_file(
            Path(command_values[index]), label="command adapter"
        )
        adapter = _checked_regular_file(paths.adapter_path, label="declared adapter")
        if not os.path.samefile(command_adapter, adapter):
            raise ValueError("v37 generic runtime command adapter differs from declaration")
        if not _path_ends_with_declared(adapter, contract["adapter"]["path"]):
            raise ValueError("v37 generic runtime adapter path differs from contract")
    runtime_manifest = _checked_regular_file(paths.runtime_manifest_path, label="runtime manifest")
    packages_lock = _checked_regular_file(paths.packages_lock_path, label="package lock")
    observed_hashes = {
        "runtime_manifest_sha256": _sha256_file(runtime_manifest),
        "executable_sha256": _sha256_file(executable),
        "adapter_sha256": _sha256_file(adapter) if adapter is not None else None,
        "packages_lock_sha256": _sha256_file(packages_lock),
    }
    if observed_hashes["runtime_manifest_sha256"] != contract["runtime_manifest_sha256"]:
        raise ValueError("v37 live runtime manifest bytes drifted after preflight")
    if observed_hashes["executable_sha256"] != contract["executable"]["sha256"]:
        raise ValueError("v37 live executable bytes drifted after preflight")
    expected_adapter_sha256 = (
        contract["adapter"]["sha256"] if contract["adapter"] is not None else None
    )
    if observed_hashes["adapter_sha256"] != expected_adapter_sha256:
        raise ValueError("v37 live adapter bytes drifted after preflight")
    if observed_hashes["packages_lock_sha256"] != contract["packages_lock_sha256"]:
        raise ValueError("v37 live package lock bytes drifted after preflight")
    source_files, source_pycache = _rehash_release(
        contract["source_release"], paths.source_root, label="source", allow_empty=True
    )
    model_files, model_pycache = _rehash_release(
        contract["model_release"], paths.model_root, label="model", allow_empty=True
    )
    working_directory = _checked_directory(cwd or Path.cwd(), label="working directory")
    environment = _normalize_environment(env)
    inputs = dict(input_paths or _infer_input_paths(command_values))
    identity: dict[str, Any] = {
        "schema_version": "v37.generic-live-runtime-byte-identity.1",
        "runtime_id": expectation.runtime_id,
        "execution_contract_sha256": expectation.execution_contract_sha256,
        **observed_hashes,
        "source_files_sha256": sha256_json(source_files),
        "model_files_sha256": sha256_json(model_files),
        "source_inventory": [item["path"] for item in source_files],
        "model_inventory": [item["path"] for item in model_files],
        "ignored_pycache": {"source": source_pycache, "model": model_pycache},
        "command": command_values,
        "command_sha256": sha256_json(command_values),
        "cwd": str(working_directory),
        "cwd_sha256": hashlib.sha256(str(working_directory).encode()).hexdigest(),
        "environment_sha256": sha256_json(environment),
        "environment_keys": sorted(environment),
        "pythonpath": _pythonpath_identity(environment),
        "inputs": _input_identity(inputs),
        "contract_verified": True,
    }
    identity["input_set_sha256"] = sha256_json(identity["inputs"])
    receipt: dict[str, Any] = {
        "schema_version": "v37.generic-live-runtime-snapshot.1",
        "stage": stage,
        "byte_identity_sha256": sha256_json(identity),
        "identity": identity,
        "preflight_revalidated_at_launch_boundary": True,
    }
    receipt["launch_receipt_sha256"] = sha256_json(receipt)
    return receipt


def _require_same_identity(reference: Mapping[str, Any], observed: Mapping[str, Any]) -> None:
    if observed["byte_identity_sha256"] != reference["byte_identity_sha256"]:
        raise ValueError("v37 live runtime bytes or launch context drifted across boundaries")


async def _terminate_process(process: Any) -> None:
    if getattr(process, "returncode", None) is None:
        process.terminate()
    await process.communicate()


async def _communicate_with_progress(
    process: Any,
    *,
    progress_writer: Callable[[], Awaitable[None]] | None,
    progress_interval_seconds: float,
) -> tuple[bytes, bytes | None]:
    if progress_interval_seconds <= 0:
        raise ValueError("v37 subprocess progress interval must be positive")
    communicate_task = asyncio.create_task(process.communicate())
    try:
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.shield(communicate_task), timeout=progress_interval_seconds
                )
            except TimeoutError:
                if progress_writer is not None:
                    await progress_writer()
    except BaseException:
        communicate_task.cancel()
        try:
            await communicate_task
        except asyncio.CancelledError:
            pass
        raise


async def run_v37_guarded_subprocess(
    command: Sequence[str],
    *,
    manifest: Mapping[str, Any],
    expectation: V37GeneratorRuntimeExpectation,
    paths: V37LiveRuntimePaths,
    receipt_writer: Callable[[dict[str, Any]], Awaitable[None]],
    aggregate_receipt_writer: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    progress_writer: Callable[[], Awaitable[None]] | None = None,
    progress_interval_seconds: float = 30.0,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_paths: Mapping[str, Path] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Guard a subprocess with durable pre-receipt and four byte snapshots."""
    environment = _normalize_environment(env)
    effective_cwd = cwd or Path.cwd()
    effective_inputs = dict(input_paths or _infer_input_paths(command))

    async def snapshot(stage: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            build_v37_live_launch_receipt,
            manifest=manifest,
            expectation=expectation,
            paths=paths,
            command=command,
            stage=stage,
            cwd=effective_cwd,
            env=environment,
            input_paths=effective_inputs,
        )

    pre_snapshot = await snapshot("pre_snapshot")
    await receipt_writer(pre_snapshot)
    prelaunch = await snapshot("prelaunch")
    _require_same_identity(pre_snapshot, prelaunch)
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(effective_cwd),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )
    try:
        post_spawn = await snapshot("post_spawn")
        _require_same_identity(pre_snapshot, post_spawn)
        stdout, _ = await _communicate_with_progress(
            process,
            progress_writer=progress_writer,
            progress_interval_seconds=progress_interval_seconds,
        )
        completion = await snapshot("completion")
        _require_same_identity(pre_snapshot, completion)
        output = stdout.decode(errors="replace")
        receipts: dict[str, Any] = {
            "schema_version": "v37.guarded-runtime-receipts.2",
            "pre_snapshot": pre_snapshot,
            "prelaunch": prelaunch,
            "post_spawn": post_spawn,
            "completion": completion,
            "byte_identity_sha256": pre_snapshot["byte_identity_sha256"],
            "all_boundaries_match": True,
        }
        receipts["launch_receipt_sha256"] = sha256_json(receipts)
        if aggregate_receipt_writer is not None:
            await aggregate_receipt_writer(receipts)
        if process.returncode:
            raise RuntimeError(f"v37 subprocess failed ({process.returncode}): {output[-8000:]}")
        return output, receipts
    except BaseException:
        await _terminate_process(process)
        raise


async def run_v37_guarded_provider_subprocess(
    command: Sequence[str],
    *,
    contract: Mapping[str, Any],
    expectation: V37GenericRuntimeExpectation,
    paths: V37GenericRuntimePaths,
    receipt_writer: Callable[[dict[str, Any]], Awaitable[None]],
    aggregate_receipt_writer: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    progress_writer: Callable[[], Awaitable[None]] | None = None,
    progress_interval_seconds: float = 30.0,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input_paths: Mapping[str, Path] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Launch a provider subprocess only after four matching byte snapshots."""
    environment = _normalize_environment(env)
    effective_cwd = cwd or Path.cwd()
    effective_inputs = dict(input_paths or _infer_input_paths(command))

    async def snapshot(stage: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            build_v37_generic_launch_receipt,
            contract=contract,
            expectation=expectation,
            paths=paths,
            command=command,
            stage=stage,
            cwd=effective_cwd,
            env=environment,
            input_paths=effective_inputs,
        )

    pre_snapshot = await snapshot("pre_snapshot")
    await receipt_writer(pre_snapshot)
    prelaunch = await snapshot("prelaunch")
    _require_same_identity(pre_snapshot, prelaunch)
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(effective_cwd),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    try:
        post_spawn = await snapshot("post_spawn")
        _require_same_identity(pre_snapshot, post_spawn)
    except BaseException:
        await _terminate_process(process)
        raise
    stdout, stderr = await _communicate_with_progress(
        process,
        progress_writer=progress_writer,
        progress_interval_seconds=progress_interval_seconds,
    )
    completion = await snapshot("completion")
    _require_same_identity(pre_snapshot, completion)
    output = stdout.decode(errors="replace")
    error_output = stderr.decode(errors="replace")
    receipts: dict[str, Any] = {
        "schema_version": "v37.guarded-runtime-receipts.2",
        "pre_snapshot": pre_snapshot,
        "prelaunch": prelaunch,
        "post_spawn": post_spawn,
        "completion": completion,
        "byte_identity_sha256": pre_snapshot["byte_identity_sha256"],
        "all_boundaries_match": True,
        "returncode": int(process.returncode or 0),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "stderr_tail": error_output[-8000:],
    }
    receipts["launch_receipt_sha256"] = sha256_json(receipts)
    if aggregate_receipt_writer is not None:
        await aggregate_receipt_writer(receipts)
    if process.returncode:
        raise RuntimeError(
            f"v37 subprocess failed ({process.returncode}): {(output + error_output)[-8000:]}"
        )
    return output, receipts
