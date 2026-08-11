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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_file(root: Path, relative_path: str, *, label: str) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved = (resolved_root / relative_path).resolve(strict=True)
    try:
        common = Path(os.path.commonpath((resolved_root, resolved)))
    except ValueError as error:
        raise ValueError(f"v37 {label} file escapes its release root") from error
    if common != resolved_root or not resolved.is_file():
        raise ValueError(f"v37 {label} file escapes its release root")
    return resolved


def _rehash_release(release: Mapping[str, Any], root: Path, *, label: str) -> list[dict[str, Any]]:
    expected = release["files"]
    if not isinstance(expected, list) or not expected:
        raise ValueError(f"v37 {label} release has no declared files")
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
    return observed


def build_v37_live_launch_receipt(
    *,
    manifest: Mapping[str, Any],
    expectation: V37GeneratorRuntimeExpectation,
    paths: V37LiveRuntimePaths,
    command: Sequence[str],
) -> dict[str, Any]:
    """Rehash every declared launch input immediately before process creation."""
    verified = verify_v37_generator_runtime_manifest(manifest, expectation=expectation)
    if not command:
        raise ValueError("v37 launch command is empty")
    adapter_sha256 = _sha256_file(paths.adapter_path.resolve(strict=True))
    python_sha256 = _sha256_file(paths.python_path.resolve(strict=True))
    lock_sha256 = _sha256_file(paths.packages_lock_path.resolve(strict=True))
    if adapter_sha256 != manifest["adapter"]["sha256"]:
        raise ValueError("v37 live adapter bytes drifted after preflight")
    if python_sha256 != manifest["runtime"]["python_executable_sha256"]:
        raise ValueError("v37 live Python executable bytes drifted after preflight")
    if lock_sha256 != manifest["runtime"]["packages_lock_sha256"]:
        raise ValueError("v37 live package lock bytes drifted after preflight")
    source_files = _rehash_release(manifest["source_release"], paths.source_root, label="source")
    model_files = _rehash_release(manifest["model_release"], paths.model_root, label="model")
    receipt: dict[str, Any] = {
        "schema_version": "v37.live-launch-receipt.1",
        "generator_id": expectation.generator_id,
        "runtime_manifest_sha256": manifest["runtime_manifest_sha256"],
        "adapter_sha256": adapter_sha256,
        "python_executable_sha256": python_sha256,
        "packages_lock_sha256": lock_sha256,
        "source_files_sha256": sha256_json(source_files),
        "model_files_sha256": sha256_json(model_files),
        "command_sha256": sha256_json([str(item) for item in command]),
        "preflight_revalidated_at_launch_boundary": True,
        "manifest_verified": verified["verified"],
    }
    receipt["launch_receipt_sha256"] = sha256_json(receipt)
    return receipt


async def run_v37_guarded_subprocess(
    command: Sequence[str],
    *,
    manifest: Mapping[str, Any],
    expectation: V37GeneratorRuntimeExpectation,
    paths: V37LiveRuntimePaths,
    receipt_writer: Callable[[dict[str, Any]], Awaitable[None]],
    cwd: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """Persist a live-byte receipt before launching the verified executable."""
    receipt = await asyncio.to_thread(
        build_v37_live_launch_receipt,
        manifest=manifest,
        expectation=expectation,
        paths=paths,
        command=command,
    )
    await receipt_writer(receipt)
    final_receipt = await asyncio.to_thread(
        build_v37_live_launch_receipt,
        manifest=manifest,
        expectation=expectation,
        paths=paths,
        command=command,
    )
    if final_receipt != receipt:
        raise ValueError("v37 live runtime bytes drifted while persisting launch receipt")
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )
    stdout, _ = await process.communicate()
    output = stdout.decode(errors="replace")
    if process.returncode:
        raise RuntimeError(f"v37 subprocess failed ({process.returncode}): {output[-8000:]}")
    return output, receipt
