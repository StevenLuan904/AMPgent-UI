from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _module():
    root = Path(__file__).parents[1]
    sys.path.insert(0, str(root / "analysis"))
    spec = importlib.util.spec_from_file_location(
        "qd_gap_v2", root / "analysis" / "qd_gap_directed_reciprocal_micrograft_v2.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _archive():
    return {
        "policy": {
            "charge_density_edges": [-1, 0, 1],
            "hydrophobicity_edges": [0, 0.5, 1],
            "hydrophobic_moment_edges": [0, 0.5, 1],
            "length_edges": [10, 20, 31],
        },
        "covered_cell_ids": ["q1-h1-m1-l0"],
        "empty_cell_ids": ["q0-h1-m1-l0", "q1-h0-m1-l0"],
    }


def test_nearest_empty_cell_is_deterministic():
    assert _module().nearest_empty_cell(_archive()) == "q0-h1-m1-l0"


def test_generation_is_equal_length_bounded_and_records_gap_fields():
    module = _module()
    parent = {
        "sequence": "KKLAAAGGKK",
        "candidate_id": "parent",
        "branch_key": "vegfa",
        "display_eligible": "true",
        "activity_model_support_count_calibrated": "2",
        "excellent_sequence_stage_calibrated": "true",
    }
    donor = {"donor_sequence": "VVVAAAAGGG", "donor_candidate_id": "donor"}
    result = module.generate([parent], [donor], set(), _archive(), "vegfa", limit=4)
    assert len(result) == 4
    assert all(len(row["sequence"]) == len(parent["sequence"]) for row in result)
    assert all(row["target_cell"] for row in result)
    assert all("delta_phi_skill" in row for row in result)
