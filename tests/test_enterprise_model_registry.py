from __future__ import annotations

from pathlib import Path

import yaml

from pepagent.enterprise_model_registry import audit_model_assay_registry

SHA = "a" * 64


def _contract(*, minimum: int = 2, calibrated: bool = True) -> dict:
    return {
        "evidence_domains": {
            "potency": {
                "minimum_independent_models": minimum,
                "calibration_and_ood_required": calibrated,
            }
        }
    }


def _eligible(model_id: str, group: str) -> dict:
    return {
        "model_id": model_id,
        "version": "1",
        "evidence_domain": "potency",
        "endpoint_semantics": "conditioned MIC in micromolar",
        "independence_group": group,
        "training_domain": "frozen pathogen/strain assay panel",
        "status": "formal_eligible",
        "runtime_manifest_sha256": SHA,
        "license": {"license_id": "commercial-evaluation", "commercial_use_allowed": True},
        "validation": {
            "independent_validation_status": "passed",
            "independent_validation_artifact_sha256": SHA,
            "calibration": {"status": "passed", "artifact_sha256": SHA},
            "ood": {"status": "passed", "artifact_sha256": SHA},
        },
    }


def test_independence_groups_not_model_names_determine_readiness() -> None:
    registry = {
        "schema_version": "ampgent.model-assay-registry.1",
        "models": [_eligible("model_a", "shared_family"), _eligible("model_b", "shared_family")],
    }
    audit = audit_model_assay_registry(registry=registry, enterprise_contract=_contract())
    assert audit.formal_science_run_authorized is False
    assert audit.gaps == ("potency:independent_models=1,required=2",)


def test_missing_calibration_and_ood_fail_closed() -> None:
    model = _eligible("model_a", "family_a")
    model["validation"] = {
        "independent_validation_status": "passed",
        "independent_validation_artifact_sha256": SHA,
    }
    registry = {"schema_version": "ampgent.model-assay-registry.1", "models": [model]}
    audit = audit_model_assay_registry(
        registry=registry, enterprise_contract=_contract(minimum=1)
    )
    assert audit.formal_science_run_authorized is False
    assert audit.rejected_models["model_a@1"] == (
        "calibration_not_passed",
        "ood_not_passed",
    )


def test_retired_model_never_counts() -> None:
    model = _eligible("retired_model", "family_a")
    model["status"] = "retired"
    model["blockers"] = ["retired_by_user"]
    registry = {"schema_version": "ampgent.model-assay-registry.1", "models": [model]}
    audit = audit_model_assay_registry(
        registry=registry, enterprise_contract=_contract(minimum=1)
    )
    assert audit.formal_science_run_authorized is False
    assert audit.eligible_models_by_domain["potency"] == ()


def test_fully_qualified_independent_panel_passes() -> None:
    registry = {
        "schema_version": "ampgent.model-assay-registry.1",
        "models": [_eligible("model_a", "family_a"), _eligible("model_b", "family_b")],
    }
    audit = audit_model_assay_registry(registry=registry, enterprise_contract=_contract())
    assert audit.formal_science_run_authorized is True
    assert audit.gaps == ()


def test_current_inventory_remains_honestly_not_ready() -> None:
    root = Path(__file__).parents[1]
    registry = yaml.safe_load(
        (root / "config/enterprise/ampgent_model_assay_registry_v39.yaml").read_text(
            encoding="utf-8"
        )
    )
    contract = yaml.safe_load(
        (root / "config/enterprise/ampgent_core_pipeline_v39_audit.yaml").read_text(
            encoding="utf-8"
        )
    )
    audit = audit_model_assay_registry(registry=registry, enterprise_contract=contract)
    assert audit.formal_science_run_authorized is False
    assert "amp_likeness:independent_models=0,required=1" in audit.gaps
    assert "mammalian_cytotoxicity:independent_models=0,required=1" in audit.gaps
    assert "commensal_selectivity:independent_models=0,required=1" in audit.gaps
