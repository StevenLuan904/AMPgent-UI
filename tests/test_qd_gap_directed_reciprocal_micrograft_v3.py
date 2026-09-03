from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _module():
    root = Path(__file__).parents[1]
    sys.path.insert(0, str(root / "analysis"))
    spec = importlib.util.spec_from_file_location(
        "qd_gap_v3", root / "analysis" / "qd_gap_directed_reciprocal_micrograft_v3.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_exact_empty_cell_hits_are_returned():
    module = _module()
    archive = {
        "policy": {
            "charge_density_edges": [-1, 0, 1],
            "hydrophobicity_edges": [0, 0.5, 1],
            "hydrophobic_moment_edges": [0, 0.5, 1],
            "length_edges": [10, 20, 31],
        },
        "covered_cell_ids": ["q1-h1-m1-l0"],
        "empty_cell_ids": ["q0-h1-m1-l0", "q1-h0-m1-l0"],
    }
    parent = {
        "sequence": "KKLAAAGGKK",
        "candidate_id": "parent",
        "branch_key": "vegfa",
        "display_eligible": "true",
        "activity_model_support_count_calibrated": "2",
        "excellent_sequence_stage_calibrated": "true",
    }
    rows = module.generate([parent], [{"donor_sequence": "VVVAAAAGGG"}], set(), archive, "vegfa", 4)
    assert rows
    assert all(row["target_cell"] == row["actual_cell_preflight"] for row in rows)
    assert all(row["target_cell_hit_preflight"] == "true" for row in rows)
