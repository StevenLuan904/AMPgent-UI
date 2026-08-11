from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_runtime_descriptor_cli import (
    freeze_v37_generic_runtime_descriptor,
    write_v37_runtime_descriptor,
)


def _sha(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> dict[str, Path | str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source"
    model = tmp_path / "model"
    source.mkdir()
    model.mkdir()
    (source / "provider.py").write_text("VALUE = 1\n", encoding="utf-8")
    (model / "weights.bin").write_bytes(b"provider-model")
    adapter = tmp_path / "adapter.py"
    adapter.write_text("print('provider')\n", encoding="utf-8")
    runtime_manifest = tmp_path / "runtime.manifest.json"
    runtime_manifest.write_text('{"provider":"one"}\n', encoding="utf-8")
    packages_lock = tmp_path / "requirements.lock.txt"
    packages_lock.write_text("provider==1\n", encoding="utf-8")
    base = {
        "schema_version": "v37.provider-runtime.1",
        "runtime_id": "provider-one",
        "runtime_manifest_sha256": _sha(runtime_manifest),
        "python_path": sys.executable,
        "adapter_path": str(adapter),
        "cwd": str(tmp_path),
        "provider_release_id": "provider-owned-release-one",
    }
    base_path = tmp_path / "base.json"
    base_path.write_text(json.dumps(base), encoding="utf-8")
    return {
        "base": base_path,
        "adapter": adapter,
        "runtime_manifest": runtime_manifest,
        "packages_lock": packages_lock,
        "source": source,
        "model": model,
        "cwd": tmp_path,
    }


def _freeze(paths: dict[str, Path | str]) -> dict[str, object]:
    return freeze_v37_generic_runtime_descriptor(
        base_runtime_path=Path(paths["base"]),
        runtime_id="provider-one",
        executable_path=Path(sys.executable),
        runtime_manifest_path=Path(paths["runtime_manifest"]),
        packages_lock_path=Path(paths["packages_lock"]),
        source_root=Path(paths["source"]),
        model_root=Path(paths["model"]),
        cwd=Path(paths["cwd"]),
        adapter_path=Path(paths["adapter"]),
        executable_index=0,
        adapter_index=1,
    )


def test_v37_descriptor_freezer_hashes_actual_bytes_and_is_deterministic(
    tmp_path: Path,
) -> None:
    paths = _fixture(tmp_path)
    first = _freeze(paths)
    second = _freeze(paths)
    assert first == second
    identity = first["runtime_identity_sha256"]
    assert identity == sha256_json(
        {key: value for key, value in first.items() if key != "runtime_identity_sha256"}
    )
    contract = first["execution_guard"]["contract"]
    assert contract["execution_contract_sha256"] == sha256_json(
        {
            key: value
            for key, value in contract.items()
            if key != "execution_contract_sha256"
        }
    )
    assert contract["source_release"]["files"] == [
        {
            "path": "provider.py",
            "size_bytes": (Path(paths["source"]) / "provider.py").stat().st_size,
            "sha256": _sha(Path(paths["source"]) / "provider.py"),
        }
    ]
    assert contract["model_release"]["files"][0]["sha256"] == _sha(
        Path(paths["model"]) / "weights.bin"
    )


def test_v37_descriptor_freezer_preserves_provider_metadata(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    descriptor = _freeze(paths)
    assert descriptor["provider_release_id"] == "provider-owned-release-one"
    assert descriptor["runtime_id"] == "provider-one"
    assert descriptor["runtime_manifest_sha256"] == _sha(
        Path(paths["runtime_manifest"])
    )


def test_v37_descriptor_freezer_rejects_identity_and_path_drift(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    base_path = Path(paths["base"])
    base = json.loads(base_path.read_text(encoding="utf-8"))
    base["runtime_manifest_sha256"] = "0" * 64
    base_path.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest SHA-256 differs"):
        _freeze(paths)

    paths = _fixture(tmp_path / "second")
    base_path = Path(paths["base"])
    base = json.loads(base_path.read_text(encoding="utf-8"))
    base["adapter_path"] = str(tmp_path / "wrong-adapter.py")
    base_path.write_text(json.dumps(base), encoding="utf-8")
    with pytest.raises(ValueError, match="adapter path differs"):
        _freeze(paths)


def test_v37_descriptor_freezer_rejects_mojibake(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    base_path = Path(paths["base"])
    base_path.write_text(
        json.dumps(
            {
                "runtime_id": "provider-one",
                "runtime_manifest_sha256": _sha(Path(paths["runtime_manifest"])),
                "python_path": sys.executable,
                "adapter_path": str(paths["adapter"]),
                "cwd": str(paths["cwd"]),
                "note": "provider â€ corrupted",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="mojibake-corrupted"):
        _freeze(paths)

    paths = _fixture(tmp_path / "lock")
    Path(paths["packages_lock"]).write_text("provider==1 â€\n", encoding="utf-8")
    with pytest.raises(ValueError, match="packages lock appears mojibake"):
        _freeze(paths)


def test_v37_descriptor_writer_is_atomic_and_utf8(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    descriptor = _freeze(paths)
    output = tmp_path / "descriptor.json"
    write_v37_runtime_descriptor(descriptor=descriptor, output_path=output)
    assert json.loads(output.read_text(encoding="utf-8")) == descriptor
    assert not (tmp_path / ".descriptor.json.tmp").exists()
