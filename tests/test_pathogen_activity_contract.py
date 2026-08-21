from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from pepagent.pathogen_activity_contract import qualify_pathogen_activity_reference

ROOT = Path(__file__).parents[1]


def _payloads() -> tuple[dict, dict, dict]:
    enterprise = ROOT / "config" / "enterprise"
    return tuple(
        json.loads((enterprise / name).read_text(encoding="utf-8"))
        for name in (
            "pathogen_conditioned_assay_table_v39.json",
            "mic_unit_condition_normalization_contract_v39.json",
            "pathogen_conditioned_split_witness_v39.json",
        )
    )


def test_frozen_pathogen_activity_reference_qualifies_for_dataset_acquisition() -> None:
    table, normalization, split = _payloads()
    result = qualify_pathogen_activity_reference(
        assay_table=table,
        normalization_contract=normalization,
        split_witness=split,
    )
    assert result.qualified_for_dataset_acquisition is True
    assert result.strain_count == 6
    assert result.assay_profile_count == 1
    assert result.blockers == ()


def test_missing_strain_identity_fails_closed() -> None:
    table, normalization, split = _payloads()
    table = deepcopy(table)
    table["entries"][0]["strain_designation"] = ""
    result = qualify_pathogen_activity_reference(
        assay_table=table,
        normalization_contract=normalization,
        split_witness=split,
    )
    assert result.qualified_for_dataset_acquisition is False
    assert "entry_0:strain_designation_missing" in result.blockers


def test_missing_assay_context_fails_closed() -> None:
    table, normalization, split = _payloads()
    table = deepcopy(table)
    table["assay_profiles"]["clsi_m07_cam_hb_aerobic"]["final_inoculum_cfu_ml"] = None
    result = qualify_pathogen_activity_reference(
        assay_table=table,
        normalization_contract=normalization,
        split_witness=split,
    )
    assert any("final_inoculum_cfu_ml" in blocker for blocker in result.blockers)


def test_split_policy_cannot_be_fit_to_current_candidate_batch() -> None:
    table, normalization, split = _payloads()
    split = deepcopy(split)
    split["current_candidate_sequences_used_to_define_split"] = True
    result = qualify_pathogen_activity_reference(
        assay_table=table,
        normalization_contract=normalization,
        split_witness=split,
    )
    assert result.blockers == ("current_candidate_sequences_influenced_split",)


def test_mic_conversion_formula_is_exact_and_raw_value_is_retained() -> None:
    table, normalization, split = _payloads()
    normalization = deepcopy(normalization)
    normalization["mass_to_molar_conversion"]["formula"] = "rounded_formula"
    result = qualify_pathogen_activity_reference(
        assay_table=table,
        normalization_contract=normalization,
        split_witness=split,
    )
    assert result.blockers == ("mass_to_molar_conversion_not_frozen",)
