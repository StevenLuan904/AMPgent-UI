from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module():
    analysis_dir = Path(__file__).resolve().parents[1] / "analysis"
    sys.path.insert(0, str(analysis_dir))
    spec = importlib.util.spec_from_file_location(
        "_autoresearch_activity_rescue_variants",
        analysis_dir / "autoresearch_activity_rescue_variants.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_activity_rescue_preserves_new_family_lineage_metadata() -> None:
    module = _load_module()
    parent_sequence = "RTKKKKTTLRREGNRGKWGK"
    parent = {
        "branch_key": "acea",
        "generation": "7",
        "sequence": parent_sequence,
        "sequence_sha256": hashlib.sha256(parent_sequence.encode()).hexdigest(),
        "macrel_amp_probability": "0.2",
        "family_key_80_80": "seqfam80-new",
        "family_representative_sequence": "RTKKKKTTLRREGNRGKWGK",
        "new_family_relative_to_all_references": "true",
        "diversity_qualified": "true",
    }

    generated, _ = module._generate([parent], set(), "0" * 64)

    assert generated
    assert {row["family_key_80_80"] for row in generated} == {"seqfam80-new"}
    assert {row["new_family_relative_to_all_references"] for row in generated} == {
        "true"
    }
    assert {row["diversity_qualified"] for row in generated} == {"true"}


def test_parent_selection_skips_families_that_already_have_full_support() -> None:
    module = _load_module()
    base = {
        "display_eligible": "true",
        "macrel_amp_probability__parent_benefit_percentile": "0.70",
        "macrel_amp_probability": "0.4",
        "amp_read_log10_mic_um": "1.0",
        "llamp_log10_mic_um": "1.0",
        "sequence": "KKKK",
    }
    rows = [
        {
            **base,
            "family_key_80_80": "already-full",
            "activity_model_support_count_calibrated": "3",
        },
        {
            **base,
            "family_key_80_80": "already-full",
            "activity_model_support_count_calibrated": "2",
        },
        {
            **base,
            "family_key_80_80": "needs-rescue",
            "activity_model_support_count_calibrated": "2",
        },
    ]

    selected = module._select_parents(
        rows, 1, exclude_families_with_support3=True
    )

    assert [row["family_key_80_80"] for row in selected] == ["needs-rescue"]


def test_amp_read_parent_selection_uses_its_own_calibrated_endpoint() -> None:
    module = _load_module()
    row = {
        "display_eligible": "true",
        "activity_model_support_count_calibrated": "2",
        "family_key_80_80": "amp-read-gap",
        "amp_read_log10_mic_um__parent_benefit_percentile": "0.4",
        "amp_read_log10_mic_um": "1.2",
        "sequence": "KKKK",
    }

    selected = module._select_parents([row], 1, rescue_endpoint="amp-read")

    assert selected == [row]


def test_mic_endpoint_operator_records_minimize_improvement_target() -> None:
    module = _load_module()
    parent_sequence = "RTKKKKTTLRREGNRGKWGK"
    parent = {
        "branch_key": "fgf2",
        "generation": "2",
        "sequence": parent_sequence,
        "sequence_sha256": hashlib.sha256(parent_sequence.encode()).hexdigest(),
        "amp_read_log10_mic_um": "1.2",
        "family_key_80_80": "seqfam80-amp-read-gap",
    }

    generated, actions = module._generate(
        [parent], set(), "0" * 64, rescue_endpoint="amp-read"
    )

    assert generated[0]["parent_rescue_metric_value"] == 1.2
    assert {
        tuple(action["expected_improvement_metrics"]) for action in actions
    } == {("amp_read_log10_mic_um",)}


def test_charge_pattern_operator_explores_non_hydrophobic_edits() -> None:
    module = _load_module()
    parent_sequence = "WRGGGWKKREKKRGKKKNGGKKGSGK"
    parent = {
        "branch_key": "acea",
        "generation": "8",
        "sequence": parent_sequence,
        "sequence_sha256": hashlib.sha256(parent_sequence.encode()).hexdigest(),
        "macrel_amp_probability": "0.416",
        "family_key_80_80": "seqfam80-last",
        "family_representative_sequence": parent_sequence,
        "new_family_relative_to_all_references": "true",
        "diversity_qualified": "true",
    }

    generated, actions = module._generate(
        [parent], set(), "0" * 64, operator_mode="charge-pattern"
    )

    assert generated
    assert {action["operator_id"] for action in actions} == {
        "autoresearch-macrel-charge-pattern-rescue-v1"
    }
    assert any(row["edit"].endswith("K") for row in generated)


def test_activity_rescue_advances_to_global_generation_floor() -> None:
    module = _load_module()
    parent_sequence = "RTKKKKTTLRREGNRGKWGK"
    parent = {
        "branch_key": "acea",
        "generation": "7",
        "sequence": parent_sequence,
        "sequence_sha256": hashlib.sha256(parent_sequence.encode()).hexdigest(),
        "macrel_amp_probability": "0.2",
        "family_key_80_80": "seqfam80-stale-branch",
        "family_representative_sequence": parent_sequence,
        "new_family_relative_to_all_references": "true",
        "diversity_qualified": "true",
    }

    generated, actions = module._generate(
        [parent], set(), "0" * 64, generation_floor=11
    )

    assert {row["generation"] for row in generated} == {11}
    assert {action["generation"] for action in actions} == {11}


def test_activity_rescue_plan_uses_the_source_branch() -> None:
    module = _load_module()

    assert module._single_branch_key([{"branch_key": "vegfa"}]) == "vegfa"

    with pytest.raises(
        ValueError, match="activity rescue requires exactly one source branch"
    ):
        module._single_branch_key(
            [{"branch_key": "acea"}, {"branch_key": "vegfa"}]
        )
