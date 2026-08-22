from __future__ import annotations

import json
from pathlib import Path

import pytest

from pepagent.commensal_selectivity_atcc_material_audit import (
    acceptance_artifacts,
    validate_commensal_selectivity_atcc_material_audit,
)


def _witness() -> dict:
    root = Path(__file__).parents[1]
    return json.loads(
        (
            root
            / "config/enterprise/commensal_selectivity_atcc_material_source_audit_v39.json"
        ).read_text(encoding="utf-8")
    )


def test_frozen_atcc_material_source_audit_is_valid() -> None:
    witness = _witness()
    assert validate_commensal_selectivity_atcc_material_audit(
        witness
    ) == acceptance_artifacts(witness)


def test_atcc_material_audit_rejects_panel_and_catalog_drift() -> None:
    witness = _witness()
    witness["strain_catalog_identity_matrix"].pop()
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="seven-strain panel"):
        validate_commensal_selectivity_atcc_material_audit(witness)

    witness = _witness()
    witness["strain_catalog_identity_matrix"][0]["catalog_number"] = "wrong"
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="catalog identity"):
        validate_commensal_selectivity_atcc_material_audit(witness)


def test_atcc_material_audit_rejects_rights_overclaim() -> None:
    witness = _witness()
    witness["material_and_data_rights_audit"]["commercial_internal_material_use"] = (
        "licensed"
    )
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="rights status"):
        validate_commensal_selectivity_atcc_material_audit(witness)

    witness = _witness()
    witness["decision"]["CRO_use_and_transfer_qualified"] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="overclaims qualification"):
        validate_commensal_selectivity_atcc_material_audit(witness)


def test_atcc_material_audit_rejects_logistics_and_candidate_overclaim() -> None:
    witness = _witness()
    witness["strain_catalog_identity_matrix"][0][
        "current_stock_shipping_and_export_status"
    ] = "ready_to_ship"
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="logistics"):
        validate_commensal_selectivity_atcc_material_audit(witness)

    witness = _witness()
    witness["candidate_and_execution_boundary"][
        "current_773_candidate_sequences_disclosed"
    ] = True
    witness["acceptance_artifacts"] = acceptance_artifacts(witness)
    with pytest.raises(ValueError, match="leaks candidates"):
        validate_commensal_selectivity_atcc_material_audit(witness)
