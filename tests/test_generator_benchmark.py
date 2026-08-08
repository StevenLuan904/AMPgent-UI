from __future__ import annotations

import pytest

from pepagent.generator_benchmark import (
    GeneratorBenchmarkManifest,
    audit_raw_generator_cohort,
)
from pepagent.generator_benchmark_scorecard_cli import build_scorecard


def _manifest() -> dict:
    generator = {
        "generator_id": "generator_a",
        "display_name": "Generator A",
        "paper_title": "Paper",
        "venue": "Journal",
        "publication_year": 2023,
        "paper_uri": "https://example.org/paper",
        "source_uri": "https://example.org/source",
        "source_revision": "a" * 40,
        "license": "MIT",
        "generation_mode": "de_novo",
        "weights": [
            {"path": "model.bin", "size_bytes": 10, "sha256": "b" * 64}
        ],
        "internal_score_filtering_enabled": False,
        "limitations": ["test fixture"],
    }
    return {
        "benchmark_id": "amp_generator_v23",
        "version": "v23",
        "execution_status": "ready",
        "track": "de_novo",
        "generators": [generator, {**generator, "generator_id": "generator_b"}],
        "seeds": [11, 22, 33],
        "raw_proposal_budget_per_seed": 100,
        "selected_valid_unique_per_seed": 20,
        "minimum_length": 10,
        "maximum_length": 50,
        "canonical_amino_acids": "ACDEFGHIKLMNPQRSTVWY",
        "selection_rule": "raw_order_first_k_valid_unique",
        "missing_policy": "retain_shortfall_no_refill",
        "ranking_method": "pareto_then_lexicographic",
        "bootstrap_unit": "generator_seed",
        "structure_enabled": False,
        "metrics": [
            {
                "name": "amplify_probability",
                "role": "profile",
                "direction": "maximize",
                "evidence_class": "soft",
            },
            {
                "name": "target_specific_delta_nll",
                "role": "diagnostic",
                "direction": "maximize",
                "evidence_class": "low_confidence_proxy",
            },
        ],
        "scientific_contract": {
            "raw_outputs_frozen_before_metrics": True,
            "no_score_based_refill": True,
            "generator_internal_scores_not_used_for_selection": True,
            "pepmlm_not_used_to_select_winner": True,
            "no_binding_or_affinity_claim": True,
        },
    }


def test_generator_benchmark_manifest_accepts_fair_de_novo_contract() -> None:
    manifest = GeneratorBenchmarkManifest.model_validate(_manifest())
    assert manifest.bootstrap_unit == "generator_seed"
    assert manifest.generators[0].internal_score_filtering_enabled is False


def test_generator_benchmark_rejects_pepmlm_as_winner_metric() -> None:
    payload = _manifest()
    payload["metrics"][1]["role"] = "profile"
    with pytest.raises(ValueError, match="diagnostic-only"):
        GeneratorBenchmarkManifest.model_validate(payload)


def test_ready_generator_benchmark_requires_local_weight_sha256() -> None:
    payload = _manifest()
    payload["generators"][0]["weights"][0].pop("sha256")
    payload["generators"][0]["weights"][0]["upstream_digest"] = "md5:abc12345"
    with pytest.raises(ValueError, match="local SHA-256"):
        GeneratorBenchmarkManifest.model_validate(payload)


def test_raw_cohort_is_selected_by_raw_order_without_refill_or_scores() -> None:
    records = [
        {"raw_rank": 1, "sequence": "KLLKLLKLLK", "model_score": -99},
        {"raw_rank": 2, "sequence": "KLLKLLKLLK", "model_score": 99},
        {"raw_rank": 3, "sequence": "ACDXACDXAC", "model_score": 100},
        {"raw_rank": 4, "sequence": "ACDEFGHIKL", "model_score": -100},
    ]
    audit = audit_raw_generator_cohort(
        records,
        raw_budget=4,
        selected_k=3,
        minimum_length=10,
        maximum_length=20,
    )
    assert [item["raw_rank"] for item in audit["selected"]] == [1, 4]
    assert audit["duplicate_count"] == 1
    assert audit["invalid_symbol_count"] == 1
    assert audit["shortfall_count"] == 1
    assert audit["score_fields_ignored"] is True


def test_raw_cohort_requires_exact_contiguous_budget() -> None:
    with pytest.raises(ValueError, match="exactly 3"):
        audit_raw_generator_cohort(
            [{"raw_rank": 1, "sequence": "ACDEFGHIKL"}],
            raw_budget=3,
            selected_k=1,
            minimum_length=10,
            maximum_length=20,
        )


def test_scorecard_disqualifies_short_cohort_without_forced_rank() -> None:
    base = {
        "valid_unique_yield": "0.9",
        "median_macrel_amp_probability": "0.5",
        "median_macrel_hemolysis_probability": "0.3",
        "median_toxinpred3_ml_score": "0.2",
        "median_llamp_predicted_mic_um": "20",
        "median_amp_read_predicted_mic_um": "30",
        "median_net_charge_ph7_4": "3",
        "median_hydrophobic_moment_eisenberg": "0.2",
    }
    rows = [
        {"generator_id": "complete", "selected_count": "100", **base},
        {"generator_id": "short", "selected_count": "1", **base},
    ]

    result = build_scorecard(rows, expected_per_seed=100)

    assert result[0]["qualification_status"] == "qualified"
    assert result[0]["forced_rank"] == "none"
    assert result[1]["qualification_status"] == "disqualified_short_cohort"
    assert result[1]["decision_tier"] == "not_rankable_on_profile_metrics"
