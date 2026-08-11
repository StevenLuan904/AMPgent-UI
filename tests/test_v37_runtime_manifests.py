from __future__ import annotations

from copy import deepcopy

import pytest

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_runtime_manifests import (
    V37_GENERATOR_IDS,
    V37_RUNTIME_MANIFEST_SCHEMA,
    V37GeneratorRuntimeExpectation,
    blocked_v37_runtime_manifest_status,
    verify_v37_generator_runtime_manifest,
    verify_v37_generator_runtime_set,
)

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64


def _expectation(generator_id: str) -> V37GeneratorRuntimeExpectation:
    return V37GeneratorRuntimeExpectation(
        generator_id=generator_id,
        adapter_sha256=SHA_A,
        adapter_version=f"{generator_id}-adapter-v1",
        source_revision=f"{generator_id}-source-revision",
        source_manifest_sha256=SHA_B,
        model_revision=f"{generator_id}-model-revision",
        model_manifest_sha256=SHA_C,
        request_contract_sha256=sha256_json(
            {"generator_id": generator_id, "raw_proposal_budget": 1000}
        ),
    )


def _manifest(generator_id: str) -> dict[str, object]:
    request_contract = {"generator_id": generator_id, "raw_proposal_budget": 1000}
    manifest: dict[str, object] = {
        "schema_version": V37_RUNTIME_MANIFEST_SCHEMA,
        "generator_id": generator_id,
        "adapter": {
            "entrypoint": f"pepagent.model_workers.{generator_id}_generator_cli",
            "sha256": SHA_A,
            "adapter_version": f"{generator_id}-adapter-v1",
        },
        "runtime": {
            "python_executable": "/opt/runtime/bin/python",
            "python_executable_sha256": SHA_D,
            "python_version": "3.11.9",
            "environment_sha256": SHA_E,
            "packages_lock_sha256": SHA_F,
        },
        "source_release": {
            "uri": "provider://source",
            "revision": f"{generator_id}-source-revision",
            "manifest_sha256": SHA_B,
            "files": [{"path": "source.py", "size_bytes": 10, "sha256": SHA_D}],
        },
        "model_release": {
            "uri": "provider://model",
            "revision": f"{generator_id}-model-revision",
            "manifest_sha256": SHA_C,
            "files": [{"path": "weights.bin", "size_bytes": 20, "sha256": SHA_E}],
        },
        "request_contract": request_contract,
        "request_contract_sha256": sha256_json(request_contract),
        "internal_score_filtering_enabled": False,
        "unsafe_deserialization_enabled": False,
    }
    manifest["runtime_manifest_sha256"] = sha256_json(manifest)
    return manifest


def _rehash(manifest: dict[str, object]) -> None:
    manifest["runtime_manifest_sha256"] = sha256_json(
        {key: value for key, value in manifest.items() if key != "runtime_manifest_sha256"}
    )


def test_v37_runtime_status_is_blocked_without_real_manifests() -> None:
    status = blocked_v37_runtime_manifest_status()
    assert status["verified"] is False
    assert status["real_runtime_manifests_supplied"] == 0
    assert status["status"] == "blocked_real_generator_runtime_manifests_not_supplied"


def test_v37_runtime_manifest_verifies_complete_exact_identity() -> None:
    result = verify_v37_generator_runtime_manifest(
        _manifest("hydramp"), expectation=_expectation("hydramp")
    )
    assert result["verified"] is True
    assert result["source_file_count"] == 1
    assert result["model_file_count"] == 1


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda item: item.update(internal_score_filtering_enabled=True), "filtering"),
        (lambda item: item.update(unsafe_deserialization_enabled=True), "deserialization"),
        (
            lambda item: item["runtime"].update(environment_sha256="0" * 64),  # type: ignore[union-attr]
            "self-hash",
        ),
        (
            lambda item: item["model_release"].update(revision="wrong"),  # type: ignore[union-attr]
            "model revision",
        ),
        (
            lambda item: item["request_contract"].update(raw_proposal_budget=999),  # type: ignore[union-attr]
            "request contract hash",
        ),
    ],
)
def test_v37_runtime_manifest_rejects_identity_or_policy_drift(
    mutation: object, message: str
) -> None:
    manifest = _manifest("hydramp")
    mutation(manifest)  # type: ignore[operator]
    if message not in {"self-hash", "request contract hash"}:
        _rehash(manifest)
    with pytest.raises(ValueError, match=message):
        verify_v37_generator_runtime_manifest(
            manifest, expectation=_expectation("hydramp")
        )


def test_v37_runtime_manifest_rejects_unsorted_or_duplicate_file_identity() -> None:
    manifest = _manifest("hydramp")
    source = manifest["source_release"]
    assert isinstance(source, dict)
    source["files"] = [
        {"path": "z.py", "size_bytes": 1, "sha256": SHA_D},
        {"path": "a.py", "size_bytes": 1, "sha256": SHA_E},
    ]
    _rehash(manifest)
    with pytest.raises(ValueError, match="unique and sorted"):
        verify_v37_generator_runtime_manifest(
            manifest, expectation=_expectation("hydramp")
        )


def test_v37_runtime_set_requires_all_three_generators_exactly_once() -> None:
    expectations = {item: _expectation(item) for item in V37_GENERATOR_IDS}
    manifests = [_manifest(item) for item in V37_GENERATOR_IDS]
    result = verify_v37_generator_runtime_set(manifests, expectations=expectations)
    assert result["verified"] is True
    assert [item["generator_id"] for item in result["generator_runtime_manifests"]] == list(
        V37_GENERATOR_IDS
    )

    with pytest.raises(ValueError, match="incomplete or unexpected"):
        verify_v37_generator_runtime_set(manifests[:-1], expectations=expectations)


def test_v37_runtime_set_rejects_duplicate_generator_manifest() -> None:
    expectations = {item: _expectation(item) for item in V37_GENERATOR_IDS}
    manifests = [_manifest("hydramp"), _manifest("hydramp"), _manifest("amp_designer")]
    with pytest.raises(ValueError, match="duplicated"):
        verify_v37_generator_runtime_set(manifests, expectations=expectations)


def test_v37_runtime_manifest_rejects_unexpected_fields() -> None:
    manifest = deepcopy(_manifest("hydramp"))
    manifest["unfrozen_note"] = "not allowed"
    _rehash(manifest)
    with pytest.raises(ValueError, match="top-level keys drifted"):
        verify_v37_generator_runtime_manifest(
            manifest, expectation=_expectation("hydramp")
        )
