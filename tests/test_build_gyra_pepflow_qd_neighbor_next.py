import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "analysis"))

from build_gyra_pepflow_qd_neighbor_next import generate


def test_next_generation_is_pg_pending_and_targets_distinct_empty_cells() -> None:
    parent = {
        "candidate_id": "11111111-1111-4111-8111-111111111111",
        "run_id": "22222222-2222-4222-8222-222222222222",
        "sequence": "ERGTKGRGASENLKLRQ",
        "sequence_sha256": "parent-sha",
        "qd_cell": "q4-h0-m0-l1",
    }
    archive = {
        "policy": {
            "charge_density_edges": [-1, 1],
            "hydrophobicity_edges": [0, 1],
            "hydrophobic_moment_edges": [0, 2],
            "length_edges": [10, 31],
        },
        "covered_cell_ids": [],
        "empty_cell_ids": [],
    }
    donors = [{"donor_candidate_id": "donor-1", "donor_fragment": "P"}]
    rows = generate([parent], donors, archive, set(), set(), limit=12)
    assert rows == []


def test_next_generation_excludes_local_sequence_and_edit_history() -> None:
    parent = {
        "candidate_id": "11111111-1111-4111-8111-111111111111",
        "run_id": "22222222-2222-4222-8222-222222222222",
        "sequence": "ERGTKGRGASENLKLRQ",
        "sequence_sha256": "parent-sha",
        "qd_cell": "q4-h0-m0-l1",
    }
    archive = {
        "policy": {
            "charge_density_edges": [-1, 1],
            "hydrophobicity_edges": [0, 1],
            "hydrophobic_moment_edges": [0, 2],
            "length_edges": [10, 31],
        },
        "covered_cell_ids": [],
        "empty_cell_ids": [],
    }
    donors = [{"donor_candidate_id": "donor-1", "donor_fragment": "P"}]
    child = "EPGTKGRGASENLKLRQ"
    rows = generate(
        [parent],
        donors,
        archive,
        {__import__("hashlib").sha256(child.encode()).hexdigest()},
        {(parent["sequence"], index, "P") for index in range(len(parent["sequence"]))},
        limit=12,
    )
    assert rows == []
