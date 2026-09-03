import json
from pathlib import Path


def test_witness_freezes_target_agnostic_contract():
    root = Path(__file__).parents[1]
    witness = json.loads(
        (root / "reports/target_agnostic_v10_witness_20260903/witness.json").read_text()
    )
    assert witness["branch"] == "target_agnostic_amp"
    assert witness["model_release_key"] == "ampgent_formal12_frozen"
    assert witness["directions"] == {
        "amp_read_log10_mic_um": "minimize",
        "llamp_log10_mic_um": "minimize",
        "macrel_amp_probability": "maximize",
    }


def test_witness_has_sufficient_same_domain_parents():
    root = Path(__file__).parents[1]
    witness = json.loads(
        (root / "reports/target_agnostic_v10_witness_20260903/witness.json").read_text()
    )
    assert witness["display_support_ge_2_count"] == 31
    assert witness["frozen_parent_count"] == 16
