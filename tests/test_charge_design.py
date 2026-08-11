from __future__ import annotations

from pathlib import Path

from pepagent.charge_design import (
    build_charge_counterfactual_cohort,
    build_charge_parent_result,
    charge_components_from_preregistration,
)
from pepagent.v33_preregistration import load_v33_preregistration

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_charge_search_sufficiency_v33.yaml"


def _components():
    return charge_components_from_preregistration(load_v33_preregistration(CONFIG))


def test_charge_dose_blocks_are_deterministic_and_position_matched() -> None:
    doses, contract = _components()
    first = build_charge_parent_result(
        parent_id="parent-1",
        parent_sequence="AKKQSTNLGAVVVAA",
        doses=doses,
        contract=contract,
    )
    second = build_charge_parent_result(
        parent_id="parent-1",
        parent_sequence="AKKQSTNLGAVVVAA",
        doses=doses,
        contract=contract,
    )

    assert first == second
    assert first.all_doses_reachable
    for dose_name, expected_delta in (
        ("one_positive_residue", 1),
        ("two_positive_residues", 2),
    ):
        block = first.dose_blocks[dose_name]
        assert block.lysine_arm is not None
        assert block.arginine_arm is not None
        assert block.control_arm is not None
        assert block.lysine_arm.edit_positions_zero_based == (
            block.arginine_arm.edit_positions_zero_based
        )
        assert block.lysine_arm.edit_positions_zero_based == (
            block.control_arm.edit_positions_zero_based
        )
        assert block.lysine_arm.metrics["formal_charge_delta_from_parent"] == (
            expected_delta
        )
        assert block.arginine_arm.metrics["formal_charge_delta_from_parent"] == (
            expected_delta
        )
        assert block.control_arm.metrics["formal_charge_delta_from_parent"] == 0
        assert block.lysine_arm.sequence != block.arginine_arm.sequence


def test_charge_parent_records_unreachable_without_refill() -> None:
    doses, contract = _components()
    result = build_charge_parent_result(
        parent_id="parent-no-edit",
        parent_sequence="AVILMFWYAVIL",
        doses=doses,
        contract=contract,
    )

    assert not result.all_doses_reachable
    assert all(not block.reachable for block in result.dose_blocks.values())
    assert all(block.lysine_arm is None for block in result.dose_blocks.values())


def test_charge_parent_does_not_edit_terminals_or_introduce_histidine() -> None:
    doses, contract = _components()
    result = build_charge_parent_result(
        parent_id="parent-2",
        parent_sequence="QKKQNSAVVVAAATT",
        doses=doses,
        contract=contract,
    )

    assert result.all_doses_reachable
    for block in result.dose_blocks.values():
        assert block.lysine_arm is not None
        assert block.arginine_arm is not None
        assert 0 not in block.edit_positions_zero_based
        assert len(result.parent_sequence) - 1 not in block.edit_positions_zero_based
        assert all(label[-1] == "K" for label in block.lysine_arm.substitutions)
        assert all(label[-1] == "R" for label in block.arginine_arm.substitutions)


def test_transformer_components_are_bound_to_literature_led_preregistration() -> None:
    doses, contract = _components()

    assert doses["one_positive_residue"].edit_count == 1
    assert doses["two_positive_residues"].edit_count == 2
    assert contract.maximum_edit_count == 2
    assert contract.editable_source_residues == ("Q", "N", "S", "T")
    assert contract.control_mapping["Q"] == "N"


def test_counterfactual_cohort_keeps_raw_order_and_records_rejections() -> None:
    doses, contract = _components()
    result = build_charge_counterfactual_cohort(
        parents_in_stream_order=[
            {"id": "invalid", "sequence": "AVILMFWYAVILMFW"},
            {"id": "first", "sequence": "AKKQSTNLGAVVVAA"},
            {"id": "duplicate", "sequence": "AKKQSTNLGAVVVAA"},
            {"id": "second", "sequence": "AKKQNTSLGAVVVAA"},
        ],
        doses=doses,
        contract=contract,
        maximum_parent_count=2,
    )

    assert [parent.parent_id for parent in result.selected_parents] == ["first", "second"]
    assert result.shortfall_count == 0
    assert [rejection.reason for rejection in result.rejections] == [
        "insufficient_neutral_editable_positions",
        "duplicate_sequence",
    ]
