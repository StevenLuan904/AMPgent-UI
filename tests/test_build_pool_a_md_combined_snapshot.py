import pytest

from analysis.build_pool_a_md_combined_snapshot import combine


def row(candidate_id: str, target: str = "acea") -> dict:
    return {
        "run_id": f"run-{candidate_id}",
        "candidate_id": candidate_id,
        "target_key": target,
        "sequence_sha256": candidate_id * 64,
    }


def test_combines_identity_disjoint_cohorts() -> None:
    result = combine([{"pool_a_all": [row("a")]}, {"pool_a_all": [row("b", "gyra")]}])
    assert result["cohort_candidate_counts"] == [1, 1]
    assert result["combined_candidate_count"] == 2
    assert [item["candidate_id"] for item in result["pool_a_all"]] == ["a", "b"]


def test_rejects_candidate_reuse_across_cohorts() -> None:
    with pytest.raises(ValueError, match="candidate_id reused"):
        combine([{"pool_a_all": [row("a")]}, {"pool_a_all": [row("a", "gyra")]}])
