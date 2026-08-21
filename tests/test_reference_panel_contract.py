from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from pepagent.reference_panel_contract import qualify_reference_panel


def _cell_panel() -> dict:
    return {
        "panel_kind": "mammalian_cytotoxicity",
        "entries": [
            {
                "catalog_id": "ATCC_PCS-200-011",
                "source_uri": "https://www.atcc.org/products/pcs-200-011",
                "organism": "Homo sapiens",
                "tissue": "skin_epidermis",
                "normal_primary_cells": True,
                "orthogonal_endpoints": ["atp_viability", "ldh_membrane_integrity"],
            }
        ],
        "assay_contract": {
            "candidate_batch_threshold_fit_forbidden": True,
            "raw_measurements_and_controls_required": True,
            "required_observation_fields": [
                "concentration_um",
                "exposure_hours",
                "replicate",
                "raw_signal",
            ],
        },
    }


def _commensal_panel() -> dict:
    return {
        "panel_kind": "skin_commensal_counter_screen",
        "entries": [
            {
                "catalog_id": "ATCC_27844",
                "source_uri": "https://www.atcc.org/products/27844",
                "strain_designation": "DM 122",
                "isolation_source": "human skin",
                "medium": "ATCC Medium 18",
                "atmosphere": "aerobic",
            }
        ],
        "assay_contract": {
            "candidate_batch_threshold_fit_forbidden": True,
            "raw_measurements_and_controls_required": True,
            "protocol_family": "CLSI_M07",
        },
    }


def test_complete_cell_panel_qualifies_for_measurement_acquisition() -> None:
    result = qualify_reference_panel(_cell_panel())
    assert result.qualified_for_measurement_acquisition is True
    assert result.entry_count == 1


def test_cell_panel_requires_orthogonal_viability_and_membrane_endpoints() -> None:
    payload = _cell_panel()
    payload["entries"][0]["orthogonal_endpoints"] = ["atp_viability"]
    result = qualify_reference_panel(payload)
    assert result.qualified_for_measurement_acquisition is False
    assert result.blockers == ("entry_0:orthogonal_cell_health_endpoints_missing",)


def test_cell_panel_missing_concentration_or_duration_fails_closed() -> None:
    payload = _cell_panel()
    payload["assay_contract"]["required_observation_fields"] = ["replicate", "raw_signal"]
    result = qualify_reference_panel(payload)
    assert result.blockers == ("cell_assay_observation_context_incomplete",)


def test_commensal_panel_requires_exact_strain_and_growth_context() -> None:
    payload = _commensal_panel()
    payload["entries"][0]["medium"] = ""
    result = qualify_reference_panel(payload)
    assert result.qualified_for_measurement_acquisition is False
    assert result.blockers == ("entry_0:missing_medium",)


def test_current_candidate_threshold_fit_must_be_forbidden() -> None:
    payload = deepcopy(_commensal_panel())
    payload["assay_contract"]["candidate_batch_threshold_fit_forbidden"] = False
    result = qualify_reference_panel(payload)
    assert result.blockers == ("current_candidate_threshold_fit_not_forbidden",)


def test_unknown_panel_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="reference panel kind is invalid"):
        qualify_reference_panel({"panel_kind": "unknown", "entries": [{}]})


def test_frozen_enterprise_panels_are_machine_qualified() -> None:
    root = Path(__file__).parents[1]
    for name, expected_count in (
        ("mammalian_cytotoxicity_reference_panel_v39.json", 2),
        ("skin_commensal_counter_screen_panel_v39.json", 4),
    ):
        payload = json.loads(
            (root / "config" / "enterprise" / name).read_text(encoding="utf-8")
        )
        result = qualify_reference_panel(payload)
        assert result.qualified_for_measurement_acquisition is True
        assert result.entry_count == expected_count
