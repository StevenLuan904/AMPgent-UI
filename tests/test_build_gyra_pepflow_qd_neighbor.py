import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "analysis"))

from build_gyra_pepflow_qd_neighbor import _edits, _safe_parents, generate


def test_parent_gate_and_provenance_are_explicit():
    row = {
        "target_key": "GyrA",
        "candidate_id": "14811462-dfe8-46f7-8e1a-1d97634c57bc",
        "sequence": "ERGTKGRGASENLKLRQ",
        "sequence_sha256": "parent-sha",
        "run_id": "169de8d4-8473-5cdf-9100-f3445ba93704",
        "display_eligible": "true",
        "formal_12_complete": "true",
        "activity_model_support_count_calibrated": "3",
        "excellent_sequence_stage_calibrated": "true",
    }
    assert len(_safe_parents([row], "GyrA")) == 1
    assert _safe_parents([{**row, "display_eligible": "false"}], "GyrA") == []


def test_generation_is_empty_cell_and_source_bound():
    parent = {
        "target_key": "GyrA",
        "candidate_id": "14811462-dfe8-46f7-8e1a-1d97634c57bc",
        "run_id": "169de8d4-8473-5cdf-9100-f3445ba93704",
        "sequence": "ERGTKGRGASENLKLRQ",
        "sequence_sha256": "parent-sha",
    }
    archive = {
        "policy": {
            "charge_density_edges": [-1, 1],
            "hydrophobicity_edges": [0, 1],
            "hydrophobic_moment_edges": [0, 2],
            "length_edges": [10, 31],
            "activity_support_minimum": 2,
            "hemolysis_probability_maximum": 0.5,
        },
        "empty_cell_ids": ["q0-h0-m0-l1"],
    }
    donor = {
        "donor_source": "PepFlow",
        "donor_candidate_id": "pepflow-donor-1",
        "donor_sequence": "VVVGGGPPNSAGA",
        "donor_fragment": "P",
        "proposal_id": "proposal-pepflow-1",
    }
    rows = generate(
        [parent],
        [donor],
        archive,
        {"a" * 64},
        _edits([]),
        target="GyrA",
        generation=2,
        seed=20260904,
        limit=12,
    )
    assert rows == []
