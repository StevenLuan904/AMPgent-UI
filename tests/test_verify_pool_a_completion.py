from analysis.report_pool_a_live import TARGETS, summarize
from analysis.verify_pool_a_completion import verify


def candidate(target: str, index: int) -> dict[str, object]:
    token = f"{TARGETS.index(target):02d}{index:062d}"
    return {
        "target_key": target,
        "candidate_id": f"candidate-{target}-{index}",
        "run_id": f"run-{target}",
        "sequence": "KKLLKK",
        "sequence_sha256": token,
        "family_key": f"family-{target}-{index}",
        "display_eligible": True,
        "activity_support": 2,
        "formal_metric_count": 12,
        "instability": 50.0,
        "non_toxin": True,
        "macrel_low": True,
        "hemopi2_covered": True,
        "apex_covered": True,
        "peptiverse_covered": True,
        "retained_conflict": False,
        "primary_dg": -40.0 - index / 1000,
        "nstruct": 5,
        "primary_aggregation": "median_dG_separated_of_all_5_decoys",
        "receipt_sha256": f"{index:064d}",
    }


def test_verifies_complete_uncapped_pool() -> None:
    payload = summarize(
        [candidate(target, index) for target in TARGETS for index in range(51)]
    )

    result = verify(payload)

    assert result["verified"] is True
    assert result["pool_a_family_count"] == 306
    assert set(result["target_family_counts"].values()) == {51}


def test_rejects_a_target_below_balance_goal() -> None:
    payload = summarize(
        [
            candidate(target, index)
            for target in TARGETS
            for index in range(49 if target == "fgf2" else 50)
        ]
    )

    result = verify(payload)

    assert result["verified"] is False
    assert "fgf2:balance_target" in result["errors"]
