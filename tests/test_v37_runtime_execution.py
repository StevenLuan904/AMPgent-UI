from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_runtime_execution import (
    V37LiveRuntimePaths,
    build_v37_live_launch_receipt,
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
    adapter.write_bytes(b"print('adapter')\n")
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
        manifest=manifest, expectation=expectation, paths=paths, command=[sys.executable]
    )
    (paths.model_root / "weights.bin").write_bytes(b"mutated")
    with pytest.raises(ValueError, match="live model release bytes drifted"):
        build_v37_live_launch_receipt(
            manifest=manifest,
            expectation=expectation,
            paths=paths,
            command=[sys.executable],
        )


def test_guarded_launch_persists_receipt_before_process_creation(tmp_path: Path) -> None:
    manifest, expectation, paths = _fixture(tmp_path)
    receipts: list[dict[str, object]] = []

    async def writer(receipt: dict[str, object]) -> None:
        receipts.append(receipt)

    output, receipt = asyncio.run(
        run_v37_guarded_subprocess(
            [sys.executable, "-c", "print('ok')"],
            manifest=manifest,
            expectation=expectation,
            paths=paths,
            receipt_writer=writer,
        )
    )
    assert output.strip() == "ok"
    assert receipts == [receipt]
    assert receipt["preflight_revalidated_at_launch_boundary"] is True
    assert receipt["launch_receipt_sha256"] == sha256_json(
        {key: value for key, value in receipt.items() if key != "launch_receipt_sha256"}
    )
