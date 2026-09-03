from analysis.analyze_candidate_source_yield import analyze


def test_source_yields_keep_comparable_denominators_separate():
    result = analyze(
        {
            "sources": {
                "pepglad": {
                    "strict_unique_candidate_count": 100,
                    "activity_support_at_least_two_count": 70,
                    "family_count_80_80": 10,
                    "targets": {"acea": 50, "gyra": 50},
                },
                "target_conditioned_pepmlm": {
                    "generated_unique_candidate_count": 20,
                    "formal_12_complete_count": 20,
                    "display_eligible_count": 8,
                    "per_target": {
                        "acea": {
                            "generated": 10,
                            "formal_12": 10,
                            "display_eligible": 6,
                        },
                        "gyra": {
                            "generated": 10,
                            "formal_12": 10,
                            "display_eligible": 2,
                        },
                    },
                },
                "pepflow": {
                    "generated_valid_count": 8,
                    "formal_12_complete_count": 8,
                    "display_eligible_count": 5,
                    "qd_quality_gate_pass_count": 0,
                    "pool_a_addition_count": 0,
                },
            }
        }
    )
    assert result["sources"]["pepglad"]["activity_support_at_least_two_yield"] == 0.7
    assert result["sources"]["target_conditioned_pepmlm"]["display_yield"] == 0.4
    assert result["sources"]["target_conditioned_pepmlm"]["lowest_display_yield_target"] == "gyra"
    assert result["sources"]["pepflow"]["bottleneck"] == "activity_qd_gate"
    assert result["cross_source_weighted_total_used"] is False
