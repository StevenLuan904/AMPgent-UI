from analysis.report_pool_a_live import summarize


def row(target: str, family: str, dg: float, *, eligible: bool = True) -> dict[str, object]:
    return {
        "target_key": target,
        "candidate_id": f"candidate-{target}-{family}-{dg}",
        "run_id": "run",
        "sequence": "KKLLKK",
        "sequence_sha256": f"sha-{target}-{family}-{dg}",
        "family_key": family,
        "display_eligible": eligible,
        "activity_support": 2,
        "excellent": eligible,
        "formal_metric_count": 12,
        "instability": 50.0,
        "non_toxin": True,
        "macrel_low": True,
        "hemopi2_covered": True,
        "apex_covered": True,
        "peptiverse_covered": True,
        "retained_conflict": False,
        "primary_dg": dg,
        "nstruct": 5,
        "primary_aggregation": "median_dG_separated_of_all_5_decoys",
        "receipt_sha256": "receipt",
    }


def test_summary_requires_all_gates_and_deduplicates_families() -> None:
    payload = summarize(
        [
            row("acea", "family-a", -40.0),
            row("acea", "family-a", -35.0),
            row("acea", "family-b", -29.0),
            row("acea", "family-c", -50.0, eligible=False),
        ]
    )

    summary = payload["targets"]["acea"]
    assert summary["rosetta_completed_candidate_count"] == 4
    assert summary["rosetta_dg_lt_minus_30_candidate_count"] == 3
    assert summary["strict_pool_a_candidate_count"] == 2
    assert summary["strict_pool_a_family_count"] == 1
    assert summary["pool_a_total_candidate_count"] == 2
    assert summary["pool_a_total_family_count"] == 1
    assert summary["pool_a_balance_target"] == 50
    assert summary["pool_a_balance_reached"] is False
    assert summary["pool_a_balance_gap_to_50"] == 49
    assert summary["pool_a_balance_surplus_over_50"] == 0
    assert summary["resource_priority_tier"] == "balance_deficit"
    assert summary["pool_a_family_gap_to_50"] == 49
    assert payload["pool_a_all"][0]["primary_dg"] == -40.0
    assert payload["pool_a_top50"][0]["primary_dg"] == -40.0


def test_summary_keeps_challenger_conflict_as_independent_front() -> None:
    candidate = row("gyra", "family-x", -45.0)
    candidate["retained_conflict"] = True
    payload = summarize([candidate])

    assert payload["targets"]["gyra"]["strict_pool_a_family_count"] == 1
    assert payload["targets"]["gyra"]["retained_conflict_family_count"] == 1


def test_excellent_metadata_is_not_a_second_gate() -> None:
    candidate = row("pbp2a", "family-x", -45.0)
    candidate["excellent"] = False
    payload = summarize([candidate])

    assert payload["targets"]["pbp2a"]["strict_pool_a_family_count"] == 1


def test_pool_a_is_uncapped_while_fifty_remains_balance_target() -> None:
    payload = summarize(
        [row("fgf2", f"family-{index:03d}", -40.0 - index / 1000) for index in range(53)]
    )

    summary = payload["targets"]["fgf2"]
    assert summary["pool_a_total_family_count"] == 53
    assert summary["pool_a_balance_reached"] is True
    assert summary["pool_a_balance_gap_to_50"] == 0
    assert summary["pool_a_balance_surplus_over_50"] == 3
    assert summary["resource_priority_tier"] == "uncapped_growth"
    assert payload["pool_a_admission_policy"] == {
        "capacity_limit": None,
        "admit_all_qualified": True,
        "balance_target_per_target": 50,
        "resource_priority": "targets_below_balance_first_then_best_available",
    }
    assert len(payload["pool_a_all"]) == 53
    assert len(payload["pool_a_top50"]) == 50
    assert payload["pool_a_all"][-1]["pool_a_rank"] == 53
