from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

from generate_angpt1_pepflow_qd_neighbor import build_proposals, sha256_text


def _parent(sequence: str, cell: str, candidate: str) -> dict[str, str]:
    return {
        "candidate_id": candidate,
        "sequence": sequence,
        "sequence_sha256": sha256_text(sequence),
        "display_eligible": "true",
        "activity_support_calibrated": "3",
        "qd_cell": cell,
    }


def test_build_is_deterministic_and_pg_bound() -> None:
    parents = [_parent("ACDEFGHIKL", "q1", "11111111-1111-1111-1111-111111111111")]
    donors = [
        {
            "donor_candidate_id": "pepflow-row-1",
            "donor_source": "PepFlow",
            "donor_sequence": "GGGG",
            "donor_fragment": "G",
        }
    ]
    first = build_proposals(parents, donors, set(), set(), limit=2)
    second = build_proposals(parents, donors, set(), set(), limit=2)
    assert first == second
    assert len(first) == 2
    assert all(row["parent_candidate_id"].startswith("1111") for row in first)
    assert all(row["donor_source"] == "PepFlow" for row in first)
    assert all(row["sequence_sha256"] == sha256_text(row["sequence"]) for row in first)
    assert all(json.loads(row["delta_phi"])["axes"][-1] == "length" for row in first)


def test_history_excludes_sequence_and_prior_edit() -> None:
    parent = _parent("ACDEFGHIKL", "q1", "22222222-2222-2222-2222-222222222222")
    donors = [
        {
            "donor_candidate_id": "pepflow-row-1",
            "donor_source": "PepFlow",
            "donor_sequence": "GGGG",
            "donor_fragment": "G",
        }
    ]
    existing = "G" + parent["sequence"][1:]
    proposals = build_proposals(
        [parent],
        donors,
        {sha256_text(existing)},
        {(parent["sequence_sha256"], 5, "G")},
        limit=12,
    )
    assert all(row["sequence"] != existing for row in proposals)
    assert all(row["acceptor_start_zero_based"] != "5" for row in proposals)
