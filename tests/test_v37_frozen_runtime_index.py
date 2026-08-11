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


def test_frozen_v37_runtime_index_has_three_verified_provider_runtimes() -> None:
    index = _load(RUNTIME_ROOT / "runtime-index.json")
    assert index["schema_version"] == "v37.generator-runtime-index.1"
    assert index["overall_status"] == "verified"
    assert index["runtime_index_sha256"] == sha256_json(
        {key: value for key, value in index.items() if key != "runtime_index_sha256"}
    )
    entries = {item["generator_id"]: item for item in index["entries"]}  # type: ignore[index]
    assert set(entries) == {"hydramp", "ampgan_v2", "amp_designer"}
    assert entries["hydramp"]["status"] == "verified"
    assert entries["hydramp"]["blocker_code"] is None
    assert entries["hydramp"]["historical_blocker_receipt_path"].endswith(
        "hydramp.blocker.json"
    )
    assert entries["hydramp"]["acceptance_receipt_path"].endswith(
        "hydramp.acceptance.json"
    )
    assert entries["hydramp"]["formal_seed_acceptance_receipt_path"].endswith(
        "hydramp.formal-seed-acceptance.json"
    )
    assert entries["ampgan_v2"]["status"] == "verified"
    assert entries["amp_designer"]["status"] == "verified"


@pytest.mark.parametrize("generator_id", ["hydramp", "ampgan_v2", "amp_designer"])
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


def test_frozen_hydramp_runtime_preserves_blocker_and_accepts_provider_release() -> None:
    index = _load(RUNTIME_ROOT / "runtime-index.json")
    entry = next(
        item for item in index["entries"] if item["generator_id"] == "hydramp"  # type: ignore[index]
    )
    manifest = _load(ROOT / entry["manifest_path"])
    verified = verify_v37_generator_runtime_manifest(
        manifest,
        expectation=_expectation(entry["expectation"]),
    )
    assert verified["verified"] is True
    assert manifest["adapter"]["entrypoint"].startswith(
        "var/releases/v37-generator-runtimes-v1/hydramp/provider/"
    )
    assert not manifest["adapter"]["entrypoint"].startswith("src/pepagent/")
    assert manifest["source_release"]["revision"] == (
        "36b18003122f0d73323f9644b07e1ed267255c11"
    )
    assert manifest["upstream_source_release"]["revision"] == (
        "6590d2f4c2963f25d30669052a4c4a857e0e7279"
    )
    historical = _load(ROOT / entry["historical_blocker_receipt_path"])
    assert historical["status"] == "blocked"
    assert historical["ampgent_compatibility_patch_forbidden"] is True
    assert historical["blocker_receipt_sha256"] == sha256_json(
        {
            key: value
            for key, value in historical.items()
            if key != "blocker_receipt_sha256"
        }
    )
    acceptance = _load(ROOT / entry["acceptance_receipt_path"])
    assert acceptance["status"] == "accepted"
    assert acceptance["ampgent_compatibility_patch_applied"] is False
    assert acceptance["provider_release"]["release_manifest_sha256"] == (
        "5b66bd0c4364e26cf629af27620789408cdb7765448d63765434fe97ed21d822"
    )
    assert acceptance["provider_release"]["release_verifier_receipt_sha256"] == (
        "3f4282501403d2e6386836eaeae3e8f6985f83de7ee66fd2601eb63111f5103b"
    )
    assert [
        item["seed"] for item in acceptance["independent_process_exact_order_receipts"]
    ] == [20260809, 20260810, 20260811]
    assert all(
        item["cross_process_exact_order"] is True
        for item in acceptance["independent_process_exact_order_receipts"]
    )
    formal = _load(ROOT / entry["formal_seed_acceptance_receipt_path"])
    assert formal["status"] == "accepted"
    assert formal["formal_generation_contract"]["seeds"] == [
        20270371,
        20270372,
        20270373,
    ]
    assert formal["formal_generation_contract"]["processes_per_seed"] == 2
    assert [item["ordered_sequence_sha256"] for item in formal["seed_receipts"]] == [
        "ad66f0a934fad88f1d5332dce69c7232183f1f032af9b7c1eda69f933a639026",
        "e56e2edbbfcb62e9f4ffd8b5bf531ffbdcca17068a93dc8c07052cb68a239cc9",
        "856ca98a2383b5c603832fae61ea180a65d4efe8f15e5dcbda692c81765408da",
    ]
    assert all(
        item["process_a_output_sha256"] == item["process_b_output_sha256"]
        and item["cross_process_exact_order"] is True
        for item in formal["seed_receipts"]
    )
    assert formal["provider_snapshot_hygiene"] == {
        "validation_generated_bytecode_cache": True,
        "generated_cache_removed_after_path_validation": True,
        "post_cleanup_snapshot_cache_count": 0,
        "future_execution_requires_PYTHONDONTWRITEBYTECODE": True,
    }


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


def test_frozen_ampgan_source_is_an_exact_clean_workspace_release() -> None:
    manifest = _load(RUNTIME_ROOT / "ampgan_v2.runtime.json")
    source = manifest["source_release"]
    assert isinstance(source, dict)
    assert source["uri"] == (
        "workspace-release://var/releases/v37-generator-runtimes-v1/"
        "ampgan_v2/source-1a2ac60bdc268f99"
    )
    declared = [item["path"] for item in source["files"]]  # type: ignore[index]
    assert len(declared) == 369
    assert all(not path.startswith(".git/") for path in declared)
    release_root = ROOT / str(source["uri"]).removeprefix("workspace-release://")
    observed = sorted(
        path.relative_to(release_root).as_posix()
        for path in release_root.rglob("*")
        if path.is_file()
    )
    assert observed == declared


def test_local_release_assets_match_frozen_runtime_index() -> None:
    result = verify_local_runtime_set(ROOT, RUNTIME_ROOT)
    assert result["verified_generators"] == [
        "hydramp",
        "ampgan_v2",
        "amp_designer",
    ]
    assert result["blocked_generators"] == []
    assert result["formal_runtime_set_ready"] is True
