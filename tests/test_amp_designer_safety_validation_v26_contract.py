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
ENVIRONMENT_PATH = ROOT / "config" / "environments" / "hemopi2_v26_environment.json"


def _payload() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def test_v26_is_preregistered_but_not_ready() -> None:
    manifest = SafetyValidationManifest.model_validate(_payload())
    assert manifest.execution_status == "archive_pending"
    assert manifest.input_cohort.row_count == 300
    assert manifest.input_cohort.selection_forbidden is True
    assert manifest.archive.local_sha256 == (
        "ac0b5567fbb4bd08869bf3c43facb0883d96cf7dfd0afddef9919255e31e1c81"
    )
    assert manifest.archive.static_inventory_sha256 == (
        "6a3eb2d0db9d84030540e9f3d69fa9bd81f6c345c3db8651ec3836c6eaf13a85"
    )
    assert manifest.archive.extracted_inventory_sha256 == (
        "676ad220203c5ae8d4ba89696ea91f0ff784958cb3465e4840ddadcf413f7892"
    )


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
    payload["archive"]["extracted_inventory_sha256"] = None
    with pytest.raises(ValueError, match="archive and inventory SHA-256"):
        SafetyValidationManifest.model_validate(payload)


def test_v26_adapter_surface_is_rf_only_and_shell_free() -> None:
    manifest = SafetyValidationManifest.model_validate(_payload())
    adapter = manifest.adapter_contract
    assert adapter.classification_backend == "random_forest_model_1"
    assert adapter.regression_backend == "random_forest_hc50"
    assert adapter.upstream_cli_execution_forbidden is True
    assert adapter.shell_execution_forbidden is True
    assert adapter.merci_disabled is True
    assert adapter.esm_disabled is True


def test_v26_rejects_expanding_the_adapter_surface() -> None:
    for field in (
        "upstream_cli_execution_forbidden",
        "shell_execution_forbidden",
        "merci_disabled",
        "esm_disabled",
        "protein_scan_disabled",
        "design_and_mutation_disabled",
        "network_access_forbidden",
    ):
        payload = deepcopy(_payload())
        payload["adapter_contract"][field] = False
        with pytest.raises(ValueError, match="excluded surface"):
            SafetyValidationManifest.model_validate(payload)


def test_v26_rejects_pickle_global_allowlist_drift() -> None:
    payload = deepcopy(_payload())
    payload["adapter_contract"]["pickle_global_allowlist"].append("builtins.eval")
    with pytest.raises(ValueError, match="pickle global allowlist"):
        SafetyValidationManifest.model_validate(payload)


def test_v26_extraction_allowlist_excludes_upstream_risky_surfaces() -> None:
    manifest = SafetyValidationManifest.model_validate(_payload())
    paths = {item.path.lower() for item in manifest.adapter_contract.extraction_allowlist}
    assert len(paths) == 11
    assert not any("pytorch_model" in path for path in paths)
    assert not any("__macosx" in path for path in paths)
    assert not any(path.endswith("hemopi2_classification.py") for path in paths)
    assert not any(path.endswith("hemopi2_regression.py") for path in paths)
    assert "hemopi2/model/data/hemopi2_reg.sav" not in paths
    assert manifest.adapter_contract.sklearn_version == "1.3.1"
    assert manifest.adapter_contract.classification_feature_contract.feature_count == 1190
    assert manifest.adapter_contract.regression_feature_contract.feature_count == 1167


def test_v26_runtime_is_hash_locked_without_model_access() -> None:
    manifest = SafetyValidationManifest.model_validate(_payload())
    runtime = manifest.runtime_environment
    lock_path = ROOT / runtime.requirements_lock_path
    import hashlib

    assert hashlib.sha256(lock_path.read_bytes()).hexdigest() == (
        runtime.requirements_lock_sha256
    )
    environment = json.loads(ENVIRONMENT_PATH.read_text(encoding="utf-8"))
    assert environment["status"] == "installed_verified"
    assert environment["requirements_lock_sha256"] == runtime.requirements_lock_sha256
    assert environment["wheelhouse_inventory_sha256"] == (
        runtime.wheelhouse_inventory_sha256
    )
    assert environment["required_versions"]["scikit-learn"] == "1.3.1"
    assert environment["verification"]["model_deserialization_attempted"] is False
    assert environment["verification"]["formal_cohort_accessed"] is False


def test_v26_rejects_runtime_that_accessed_model_or_formal_cohort() -> None:
    for field in ("model_deserialization_attempted", "formal_cohort_accessed"):
        payload = deepcopy(_payload())
        payload["runtime_environment"][field] = True
        with pytest.raises(ValueError):
            SafetyValidationManifest.model_validate(payload)


def test_v26_records_only_partial_feature_implementation() -> None:
    manifest = SafetyValidationManifest.model_validate(_payload())
    blocks = {item.block_id: item for item in manifest.implemented_feature_blocks}
    assert blocks["mw_length_aac_dpc1"].feature_count == 422
    assert blocks["atc_btc_pcp_rri_pri_ddr"].feature_count == 104
    assert blocks["atc_btc_pcp_rri_pri_ddr"].reference_order_dependence_preserved
    assert blocks["ser_sep_entropy"].feature_count == 21
    assert sum(item.feature_count for item in blocks.values()) == 547
    assert all(item.implemented_without_upstream_execution for item in blocks.values())
    assert all(item.formal_cohort_accessed is False for item in blocks.values())


def test_v26_rejects_feature_block_span_drift() -> None:
    payload = deepcopy(_payload())
    payload["implemented_feature_blocks"][0]["model_offset_end_exclusive"] = 421
    with pytest.raises(ValueError, match="span"):
        SafetyValidationManifest.model_validate(payload)


def test_v26_rejects_forbidden_extraction_surface() -> None:
    payload = deepcopy(_payload())
    payload["adapter_contract"]["extraction_allowlist"].append(
        {
            "path": "hemopi2/Model/pytorch_model.bin",
            "size_bytes": 1,
            "sha256": "0" * 64,
        }
    )
    with pytest.raises(ValueError, match="forbidden HemoPI2 surface"):
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
