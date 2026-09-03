# ruff: noqa: E501
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from analysis.build_reciprocal_micrograft_scheduler_manifest import (
    classify_and_sort,
    dispatch_allowed,
    resolve_authoritative_ids,
)


def test_same_run_unique_resolution_and_cross_run_non_merge() -> None:
    rows = [
        {"run_id": "r1", "sequence": "AAA", "sequence_sha256": "h", "candidate_id": "proposal-x"},
        {"run_id": "r2", "sequence": "AAA", "sequence_sha256": "h", "candidate_id": "proposal-y"},
    ]
    candidates = [{"id": "u1", "run_id": "r1", "sequence_sha256": "h"}]
    resolve_authoritative_ids(rows, candidates)
    assert rows[0]["authoritative_candidate_id"] == "u1"
    assert rows[0]["source_proposal_id"] == "proposal-x"
    assert rows[1]["identity_resolution_status"] == "identity_unresolved"


def test_missing_and_multiple_are_unresolved_and_not_ready() -> None:
    rows = [
        {"run_id": "r1", "sequence": "AAA", "sequence_sha256": "h1", "candidate_id": "p1"},
        {"run_id": "r1", "sequence": "BBB", "sequence_sha256": "h2", "candidate_id": "p2"},
    ]
    resolve_authoritative_ids(rows, [
        {"id": "u1", "run_id": "r1", "sequence_sha256": "h1"},
        {"id": "u2", "run_id": "r1", "sequence_sha256": "h1"},
    ])
    ordered = classify_and_sort(rows, {}, set(), set())
    assert all(row["scheduler_status"] == "identity_unresolved" for row in ordered)


def test_active_and_completed_excluded_and_qd_round_robin() -> None:
    rows = []
    for index, target in enumerate(("a", "b", "a", "b")):
        rows.append({
            "run_id": "r", "sequence": f"S{index}", "sequence_sha256": f"h{index}",
            "candidate_id": f"p{index}", "authoritative_candidate_id": f"u{index}",
            "identity_resolution_status": "resolved", "identity_match_count": "1",
            "identity_key": f"r+u{index}", "qd_contribution": "empty_cell",
            "quality": str(index), "target_key": target,
        })
    rows[2]["identity_key"] = "r+active"
    rows[3]["identity_key"] = "r+done"
    resolved = classify_and_sort(rows, {"r+done": 1}, set(), {"r+active"})
    assert resolved[0]["target_key"] == "a"
    assert resolved[1]["target_key"] == "b"
    assert {row["scheduler_status"] for row in resolved[2:]} == {"already_active", "already_completed"}


def test_capacity_gate_blocks_dispatch_when_limit_is_zero() -> None:
    assert not dispatch_allowed({"active_gpu_count": 8, "new_scheduler_limit": 0, "dispatch_allowed": False})
    assert dispatch_allowed({"active_gpu_count": 0, "new_scheduler_limit": 1, "dispatch_allowed": True})
