from analysis.build_pool_a_md_successor_snapshot import build


def row(candidate: str) -> dict[str, str]:
    return {
        "run_id": f"run-{candidate}",
        "candidate_id": candidate,
        "target_key": "acea",
        "sequence_sha256": candidate * 64,
    }


def test_builds_only_exact_new_pool_a_identities() -> None:
    prior = row("a")
    added = row("b")
    result = build(
        {"observed_at": "now", "pool_a_all": [prior, added]},
        {"pool_a_all": [prior]},
    )
    assert result["prior_snapshot_candidate_count"] == 1
    assert result["successor_candidate_count"] == 1
    assert result["pool_a_all"] == [added]
