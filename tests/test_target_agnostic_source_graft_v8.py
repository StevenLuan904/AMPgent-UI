import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "target_agnostic_source_graft_v8",
    Path(__file__).parents[1] / "analysis" / "target_agnostic_source_graft_v8.py",
)
assert _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
eligible_donor = _MODULE.eligible_donor
inventory = _MODULE.inventory
generate_children = _MODULE.generate_children


def row(**updates):
    result = {
        "sequence_sha256": "a" * 64,
        "display_eligible": "true",
        "toxinpred3_label": "Non-Toxin",
        "macrel_hemolysis_label": "low",
        "guruprasad_instability_index": "20",
        "activity_model_support_count_calibrated": "2",
        "donor_source": "PepFlow",
    }
    result.update(updates)
    return result


def test_v8_inventory_is_fail_closed_by_source_and_hard_gates():
    assert eligible_donor(row())
    assert not eligible_donor(row(display_eligible="false"))
    assert not eligible_donor(row(toxinpred3_label="Toxin"))
    assert not eligible_donor(row(macrel_hemolysis_label="high"))
    assert not eligible_donor(row(activity_model_support_count_calibrated="1"))
    result = inventory([row(), row(donor_source="factorized_pepglad_amp_edit")])
    assert result == {
        "input_count": 2,
        "display_safe_support_ge_2_count": 2,
        "pepglad_count": 1,
        "pepflow_count": 1,
    }


def test_raw_pepglad_fragment_can_generate_child_without_claiming_donor_safety():
    parent = row(sequence="ACDEFGHIK", candidate_id="parent")
    raw_donor = row(
        sequence="VVV", candidate_id="raw", display_eligible="false",
    )
    children = generate_children([parent], [raw_donor], "PepGLAD", limit=1)
    assert len(children) == 1
    assert children[0]["donor_display_eligible"] == "false"
    assert children[0]["sequence"] != parent["sequence"]
