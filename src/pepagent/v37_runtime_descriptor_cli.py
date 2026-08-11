from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_runtime_execution import (
    V37GenericRuntimeExpectation,
    V37GenericRuntimePaths,
    build_v37_generic_launch_receipt,
)

_FORBIDDEN_TEXT_FRAGMENTS = ("\ufffd", "Ã", "Â", "â€", "锛", "鈥", "绉", "鎵")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_text(value: str, *, label: str, allow_empty: bool = False) -> str:
    if (not value and not allow_empty) or value != unicodedata.normalize("NFC", value):
        raise ValueError(f"v37 {label} is empty or not NFC-normalized")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise ValueError(f"v37 {label} contains a control character")
    if any(fragment in value for fragment in _FORBIDDEN_TEXT_FRAGMENTS):
        raise ValueError(f"v37 {label} appears mojibake-corrupted")
    return value


def _load_utf8_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = path.read_bytes()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"v37 {label} is not strict UTF-8") from error
    _validate_text(text, label=label)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f"v37 {label} must be a JSON object")
    _validate_json_texts(value, label=label)
    return value


def _validate_json_texts(value: Any, *, label: str) -> None:
    if isinstance(value, str):
        _validate_text(value, label=label, allow_empty=True)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _validate_text(str(key), label=f"{label} key")
            _validate_json_texts(item, label=f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_texts(item, label=f"{label}[{index}]")


def _assert_no_symlink(path: Path, *, label: str) -> None:
    absolute = path.absolute()
    for component in reversed((absolute, *absolute.parents)):
        if not component.exists():
            continue
        file_attributes = getattr(component.lstat(), "st_file_attributes", 0)
        if component.is_symlink() or file_attributes & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        ):
            raise ValueError(f"v37 {label} path contains a symlink or reparse point")


def _regular_file(path: Path, *, label: str) -> Path:
    _validate_text(str(path), label=f"{label} path")
    _assert_no_symlink(path, label=label)
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"v37 {label} is not a regular file")
    return resolved


def _directory(path: Path, *, label: str) -> Path:
    _validate_text(str(path), label=f"{label} path")
    _assert_no_symlink(path, label=label)
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"v37 {label} is not a directory")
    return resolved


def _inventory(root: Path, *, label: str) -> list[dict[str, Any]]:
    resolved_root = _directory(root, label=f"{label} root")
    result: list[dict[str, Any]] = []
    for path in resolved_root.rglob("*"):
        file_attributes = getattr(path.lstat(), "st_file_attributes", 0)
        if path.is_symlink() or file_attributes & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        ):
            raise ValueError(f"v37 {label} release contains a symlink or reparse point")
        if not path.is_file():
            continue
        relative = path.relative_to(resolved_root)
        if "__pycache__" in relative.parts or path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        relative_text = relative.as_posix()
        _validate_text(relative_text, label=f"{label} relative path")
        result.append(
            {
                "path": relative_text,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    result.sort(key=lambda item: str(item["path"]))
    return result


def _same_path(first: object, second: Path) -> bool:
    try:
        return os.path.samefile(Path(str(first)), second)
    except (FileNotFoundError, OSError, ValueError):
        return False


def _validate_base_path_bindings(
    base: Mapping[str, Any],
    *,
    executable: Path,
    adapter: Path | None,
    cwd: Path,
) -> None:
    executable_fields = [key for key in ("executable", "python_path") if key in base]
    if not executable_fields:
        raise ValueError("v37 base descriptor has no executable or python_path binding")
    if any(not _same_path(base[key], executable) for key in executable_fields):
        raise ValueError("v37 base descriptor executable path differs from physical runtime")
    if adapter is not None:
        adapter_fields = [key for key in ("adapter_path", "kbctl_path") if key in base]
        if not adapter_fields:
            raise ValueError("v37 base descriptor has no adapter_path or kbctl_path binding")
        if any(not _same_path(base[key], adapter) for key in adapter_fields):
            raise ValueError("v37 base descriptor adapter path differs from physical runtime")
    if "cwd" not in base or not _same_path(base["cwd"], cwd):
        raise ValueError("v37 base descriptor cwd differs from physical runtime")


def freeze_v37_generic_runtime_descriptor(
    *,
    base_runtime_path: Path,
    runtime_id: str,
    executable_path: Path,
    runtime_manifest_path: Path,
    packages_lock_path: Path,
    source_root: Path,
    model_root: Path,
    cwd: Path,
    adapter_path: Path | None = None,
    executable_index: int = 0,
    adapter_index: int | None = None,
) -> dict[str, Any]:
    """Freeze one descriptor from provider-owned metadata and actual local bytes.

    The function never invents release identities. Provider-specific fields must already
    exist in ``base_runtime_path``; this function only verifies their physical path/hash
    bindings and appends the generic four-boundary execution guard.
    """

    runtime_id = _validate_text(runtime_id, label="runtime ID")
    base_path = _regular_file(base_runtime_path, label="base runtime descriptor")
    base = _load_utf8_json(base_path, label="base runtime descriptor")
    if "execution_guard" in base or "runtime_identity_sha256" in base:
        raise ValueError("v37 base descriptor is already frozen")
    if base.get("runtime_id") != runtime_id:
        raise ValueError("v37 base descriptor runtime identity differs from request")

    executable = _regular_file(executable_path, label="runtime executable")
    runtime_manifest = _regular_file(runtime_manifest_path, label="runtime manifest")
    packages_lock = _regular_file(packages_lock_path, label="packages lock")
    source = _directory(source_root, label="source release")
    model = _directory(model_root, label="model release")
    working_directory = _directory(cwd, label="working directory")
    adapter = (
        _regular_file(adapter_path, label="runtime adapter")
        if adapter_path is not None
        else None
    )
    if (adapter is None) != (adapter_index is None):
        raise ValueError("v37 adapter path/index mapping is inconsistent")
    if executable_index != 0 or (adapter_index is not None and adapter_index < 1):
        raise ValueError("v37 runtime command entity indices are invalid")
    _validate_base_path_bindings(
        base,
        executable=executable,
        adapter=adapter,
        cwd=working_directory,
    )

    manifest_sha256 = _sha256_file(runtime_manifest)
    if base.get("runtime_manifest_sha256") != manifest_sha256:
        raise ValueError("v37 base descriptor runtime manifest SHA-256 differs from bytes")
    _load_utf8_json(runtime_manifest, label="runtime manifest")
    try:
        lock_text = packages_lock.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("v37 packages lock is not strict UTF-8") from error
    _validate_text(lock_text, label="packages lock")

    source_files = _inventory(source, label="source")
    model_files = _inventory(model, label="model")
    contract: dict[str, Any] = {
        "schema_version": "v37.generic-runtime-execution.1",
        "runtime_id": runtime_id,
        "runtime_manifest_sha256": manifest_sha256,
        "executable": {"path": executable.name, "sha256": _sha256_file(executable)},
        "adapter": (
            {"path": adapter.name, "sha256": _sha256_file(adapter)}
            if adapter is not None
            else None
        ),
        "packages_lock_sha256": _sha256_file(packages_lock),
        "source_release": {
            "files": source_files,
            "files_sha256": sha256_json(source_files),
        },
        "model_release": {
            "files": model_files,
            "files_sha256": sha256_json(model_files),
        },
        "command_entities": {
            "executable_index": executable_index,
            "adapter_index": adapter_index,
        },
    }
    contract["execution_contract_sha256"] = sha256_json(contract)
    expectation = {
        "runtime_id": runtime_id,
        "execution_contract_sha256": contract["execution_contract_sha256"],
    }
    paths = {
        "executable_path": str(executable),
        "runtime_manifest_path": str(runtime_manifest),
        "packages_lock_path": str(packages_lock),
        "source_root": str(source),
        "model_root": str(model),
        "adapter_path": str(adapter) if adapter is not None else None,
    }
    descriptor = {
        **base,
        "execution_guard": {
            "contract": contract,
            "expectation": expectation,
            "paths": paths,
        },
    }
    descriptor["runtime_identity_sha256"] = sha256_json(descriptor)

    command = ["v37-runtime-entity"] * (
        max(executable_index, adapter_index or 0) + 1
    )
    command[executable_index] = str(executable)
    if adapter_index is not None:
        command[adapter_index] = str(adapter)
    build_v37_generic_launch_receipt(
        contract=contract,
        expectation=V37GenericRuntimeExpectation(**expectation),
        paths=V37GenericRuntimePaths(
            executable_path=executable,
            runtime_manifest_path=runtime_manifest,
            packages_lock_path=packages_lock,
            source_root=source,
            model_root=model,
            adapter_path=adapter,
        ),
        command=command,
        cwd=working_directory,
        env={},
        input_paths={},
    )
    return descriptor


def write_v37_runtime_descriptor(*, descriptor: Mapping[str, Any], output_path: Path) -> None:
    parent = _directory(output_path.parent, label="descriptor output parent")
    output = parent / output_path.name
    _validate_text(output.name, label="descriptor output filename")
    payload = json.dumps(
        descriptor, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze a v37 generic runtime descriptor from provider-owned metadata and "
            "actual local bytes; never launch a provider or submit a workflow"
        )
    )
    parser.add_argument("--base-runtime", type=Path, required=True)
    parser.add_argument("--runtime-id", required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--packages-lock", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--executable-index", type=int, default=0)
    parser.add_argument("--adapter-index", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    descriptor = freeze_v37_generic_runtime_descriptor(
        base_runtime_path=args.base_runtime,
        runtime_id=args.runtime_id,
        executable_path=args.executable,
        runtime_manifest_path=args.runtime_manifest,
        packages_lock_path=args.packages_lock,
        source_root=args.source_root,
        model_root=args.model_root,
        cwd=args.cwd,
        adapter_path=args.adapter,
        executable_index=args.executable_index,
        adapter_index=args.adapter_index,
    )
    write_v37_runtime_descriptor(descriptor=descriptor, output_path=args.output)
    print(
        json.dumps(
            {
                "runtime_id": descriptor["runtime_id"],
                "runtime_identity_sha256": descriptor["runtime_identity_sha256"],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
