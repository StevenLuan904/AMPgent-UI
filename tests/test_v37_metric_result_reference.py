from __future__ import annotations

import json

import pytest

from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.storage.object_store import StoredObject
from pepagent.workers import v37_activities


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


@pytest.mark.asyncio
async def test_v37_metric_activity_returns_only_compact_content_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    full = {
        "result": {
            "plugin": {"name": "toxicity_risk"},
            "records": [{"candidate_id": str(index), "raw": "x" * 1000} for index in range(900)],
        },
        "provenance": {"environment_sha256": "a" * 64},
    }
    transition = {"schema_version": "v37.activity-transition-receipt.1", "attempt": 1}
    stored_payload: dict[str, object] = {}

    async def store(payload: dict[str, object]) -> StoredObject:
        stored_payload.update(payload)
        raw = _canonical_bytes(payload)
        return StoredObject(
            sha256=sha256_bytes(raw),
            size_bytes=len(raw),
            uri="s3://pepagent/sha256/metric-result",
            media_type="application/json",
        )

    monkeypatch.setattr(v37_activities, "_activity_transition_receipt", lambda: transition)
    monkeypatch.setattr(v37_activities, "_store_json", store)

    reference = await v37_activities._compact_v37_metric_result(full)

    assert set(reference) == {
        "schema_version",
        "plugin_name",
        "metric_result_sha256",
        "metric_result_artifact",
        "activity_transition_receipt",
    }
    assert "result" not in reference
    assert "provenance" not in reference
    assert reference["plugin_name"] == "toxicity_risk"
    assert stored_payload == {**full, "activity_transition_receipt": transition}
    assert len(_canonical_bytes(reference)) < 1000
    assert len(_canonical_bytes(stored_payload)) > 900_000


@pytest.mark.asyncio
async def test_v37_metric_persistence_resolves_and_verifies_compact_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "result": {"plugin": {"name": "mic_potency"}, "records": []},
        "provenance": {"environment_sha256": "b" * 64},
        "activity_transition_receipt": {"attempt": 2},
    }
    raw = _canonical_bytes(payload)

    class Store:
        def get_bytes(self, uri: str) -> bytes:
            assert uri == "s3://pepagent/sha256/metric-result"
            return raw

    monkeypatch.setattr(v37_activities, "ContentAddressedObjectStore", Store)
    reference = {
        "schema_version": v37_activities.V37_METRIC_RESULT_REFERENCE_SCHEMA,
        "plugin_name": "mic_potency",
        "metric_result_sha256": sha256_json(payload),
        "metric_result_artifact": {
            "sha256": sha256_bytes(raw),
            "size_bytes": len(raw),
            "uri": "s3://pepagent/sha256/metric-result",
            "media_type": "application/json",
        },
        "activity_transition_receipt": {"attempt": 2},
    }

    assert await v37_activities._resolve_v37_metric_result(reference) == payload

    reference["plugin_name"] = "wrong-plugin"
    with pytest.raises(ValueError, match="compact receipt differs"):
        await v37_activities._resolve_v37_metric_result(reference)


@pytest.mark.asyncio
async def test_v37_metric_reference_rejects_object_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Store:
        def get_bytes(self, _uri: str) -> bytes:
            return b"{}"

    monkeypatch.setattr(v37_activities, "ContentAddressedObjectStore", Store)
    reference = {
        "schema_version": v37_activities.V37_METRIC_RESULT_REFERENCE_SCHEMA,
        "plugin_name": "mic_potency",
        "metric_result_sha256": "a" * 64,
        "metric_result_artifact": {
            "sha256": "a" * 64,
            "size_bytes": 2,
            "uri": "s3://pepagent/sha256/tampered",
            "media_type": "application/json",
        },
        "activity_transition_receipt": {"attempt": 1},
    }

    with pytest.raises(ValueError, match="artifact identity"):
        await v37_activities._resolve_v37_metric_result(reference)
