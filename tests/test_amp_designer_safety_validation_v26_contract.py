from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from pepagent.safety_validation import SafetyValidationManifest

ROOT = Path(__file__).parents[1]
CONFIG_PATH = ROOT / "config" / "benchmarks" / "amp_designer_safety_validation_v26.yaml"
MODEL_MANIFEST_PATH = (
    ROOT / "config" / "models" / "manifests" / "hemopi2_zenodo_14676712.json"
)


def _payload() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_v26_is_preregistered_but_not_ready() -> None:
    manifest = SafetyValidationManifest.model_validate(_payload())
    assert manifest.execution_status == "archive_pending"
    assert manifest.input_cohort.row_count == 300
    assert manifest.input_cohort.selection_forbidden is True
    assert manifest.archive.local_sha256 is None
    assert manifest.archive.extracted_inventory_sha256 is None


def test_v26_binds_the_frozen_v25_full_cohort() -> None:
    manifest = SafetyValidationManifest.model_validate(_payload())
    cohort_path = ROOT / manifest.input_cohort.path
    assert cohort_path.stat().st_size == 115440
    import hashlib

    assert hashlib.sha256(cohort_path.read_bytes()).hexdigest() == manifest.input_cohort.sha256
    assert sum(1 for _ in cohort_path.open(encoding="utf-8")) - 1 == 300


def test_v26_training_overlap_audit_is_zero_for_both_frozen_datasets() -> None:
    manifest = SafetyValidationManifest.model_validate(_payload())
    assert {item.dataset_name for item in manifest.training_overlap_audit} == {
        "cross_val_dataset.csv",
        "independent_dataset.csv",
    }
    assert all(item.exact_sequence_overlap_count == 0 for item in manifest.training_overlap_audit)


def test_v26_cannot_be_ready_without_archive_and_inventory_sha256() -> None:
    payload = deepcopy(_payload())
    payload["execution_status"] = "ready"
    with pytest.raises(ValueError, match="archive and inventory SHA-256"):
        SafetyValidationManifest.model_validate(payload)


def test_v26_rejects_partial_cohort_or_selection() -> None:
    payload = deepcopy(_payload())
    payload["input_cohort"]["selection_forbidden"] = False
    with pytest.raises(ValueError, match="without selection"):
        SafetyValidationManifest.model_validate(payload)

    payload = deepcopy(_payload())
    payload["expected_output_rows"] = 299
    with pytest.raises(ValueError, match="every frozen input row"):
        SafetyValidationManifest.model_validate(payload)


def test_v26_requires_isolated_offline_untrusted_pickle_execution() -> None:
    for path in (
        ("archive", "untrusted_deserialization"),
        (None, "isolated_execution_required"),
        (None, "network_disabled_during_inference"),
    ):
        payload = deepcopy(_payload())
        parent, key = path
        if parent is None:
            payload[key] = False
        else:
            payload[parent][key] = False
        with pytest.raises(ValueError):
            SafetyValidationManifest.model_validate(payload)


def test_v26_model_manifest_matches_preregistration_and_is_blocked() -> None:
    payload = _payload()
    model_manifest = json.loads(MODEL_MANIFEST_PATH.read_text(encoding="utf-8"))
    archive = model_manifest["record"]["archive"]
    assert archive["path"] == payload["archive"]["archive_name"]
    assert archive["size_bytes"] == payload["archive"]["size_bytes"]
    assert archive["upstream_digest"] == payload["archive"]["upstream_digest"]
    assert model_manifest["security_gate"]["status"].startswith("blocked_")
    assert model_manifest["security_gate"]["model_run_started"] is False
