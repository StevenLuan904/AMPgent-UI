from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_runtime_manifest_cli import verify_local_runtime_set
from pepagent.v37_runtime_manifests import (
    V37GeneratorRuntimeExpectation,
    verify_v37_generator_runtime_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "config/environments/v37_generator_runtimes"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _expectation(value: dict[str, object]) -> V37GeneratorRuntimeExpectation:
    return V37GeneratorRuntimeExpectation(**value)  # type: ignore[arg-type]


def test_frozen_v37_runtime_index_has_two_verified_and_one_honest_blocker() -> None:
    index = _load(RUNTIME_ROOT / "runtime-index.json")
    assert index["schema_version"] == "v37.generator-runtime-index.1"
    assert index["overall_status"] == "blocked"
    assert index["runtime_index_sha256"] == sha256_json(
        {key: value for key, value in index.items() if key != "runtime_index_sha256"}
    )
    entries = {item["generator_id"]: item for item in index["entries"]}  # type: ignore[index]
    assert set(entries) == {"hydramp", "ampgan_v2", "amp_designer"}
    assert entries["hydramp"]["status"] == "blocked"
    assert entries["hydramp"]["blocker_code"] == "unsafe_joblib_deserialization"
    assert entries["ampgan_v2"]["status"] == "verified"
    assert entries["amp_designer"]["status"] == "verified"


@pytest.mark.parametrize("generator_id", ["ampgan_v2", "amp_designer"])
def test_frozen_v37_safe_runtime_manifests_pass_strict_verifier(
    generator_id: str,
) -> None:
    index = _load(RUNTIME_ROOT / "runtime-index.json")
    entry = next(
        item for item in index["entries"] if item["generator_id"] == generator_id  # type: ignore[index]
    )
    manifest = _load(ROOT / entry["manifest_path"])
    result = verify_v37_generator_runtime_manifest(
        manifest,
        expectation=_expectation(entry["expectation"]),
    )
    assert result["verified"] is True
    assert result["runtime_manifest_sha256"] == entry["runtime_manifest_sha256"]
    runtime_path = str(manifest["runtime"]["python_executable"])  # type: ignore[index]
    assert PurePosixPath(runtime_path).is_absolute() is False


def test_frozen_hydramp_runtime_is_rejected_and_blocker_is_self_hashed() -> None:
    index = _load(RUNTIME_ROOT / "runtime-index.json")
    entry = next(
        item for item in index["entries"] if item["generator_id"] == "hydramp"  # type: ignore[index]
    )
    manifest = _load(ROOT / entry["manifest_path"])
    with pytest.raises(ValueError, match="unsafe deserialization"):
        verify_v37_generator_runtime_manifest(
            manifest,
            expectation=_expectation(entry["expectation"]),
        )
    receipt = _load(ROOT / entry["blocker_receipt_path"])
    assert receipt["status"] == "blocked"
    assert receipt["ampgent_compatibility_patch_forbidden"] is True
    assert receipt["blocker_receipt_sha256"] == sha256_json(
        {
            key: value
            for key, value in receipt.items()
            if key != "blocker_receipt_sha256"
        }
    )


def test_frozen_model_manifests_lock_expected_upstream_weights() -> None:
    ampgan = _load(RUNTIME_ROOT / "ampgan_v2.runtime.json")
    ampgan_hashes = {
        item["sha256"] for item in ampgan["model_release"]["files"]  # type: ignore[index]
    }
    assert ampgan_hashes == {
        "a5e7cafa16c33010bd0fe22747c89f05d74f8781deac9eeeb91c6e4c371177e2",
        "df6b16408b05f21a6fdb1d675c12562257d619382d08af2fad391d7ed847084a",
        "012fc17624f1fe6b0441622bebc71ee968e4d7db80bb7910e22af93b49d69890",
        "7936298d0a7ac1802bbedc726d29e27911c3aa60778ac97395a24d5f82456902",
    }
    designer = _load(RUNTIME_ROOT / "amp_designer.runtime.json")
    designer_by_path = {
        item["path"]: item["sha256"]
        for item in designer["model_release"]["files"]  # type: ignore[index]
    }
    assert designer_by_path["pytorch_model.bin"] == (
        "47944ff42f7ea6a448340d44c2027329833205dd658ec4777e77777bdab1adc9"
    )


def test_local_release_assets_match_frozen_runtime_index() -> None:
    result = verify_local_runtime_set(ROOT, RUNTIME_ROOT)
    assert result["verified_generators"] == ["ampgan_v2", "amp_designer"]
    assert result["blocked_generators"] == ["hydramp"]
    assert result["formal_runtime_set_ready"] is False
