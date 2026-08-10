from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pepagent.evidence_replay import replay_v32_portfolio
from pepagent.multiobjective_portfolio import (
    MultiobjectivePortfolioManifest,
    build_portfolio,
    normalized_levenshtein_similarity,
)
from pepagent.multiobjective_portfolio_submit_cli import load_submission_contract
from pepagent.settings import Settings
from pepagent.workers.temporal_worker import ROLE_CONFIG

ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "config" / "benchmarks" / "amp_multiobjective_portfolio_v32.yaml"


def _payload() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def _candidate(index: int, seed: int, sequence: str) -> dict:
    return {
        "id": f"candidate-{index:03d}",
        "seed": seed,
        "sequence": sequence,
        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
        "metrics": {
            "hydrophobic_moment_eisenberg": 0.1 + index / 100,
            "hydrophobic_ratio_modlamp": 0.45 + (index % 3) / 100,
            "maximum_hydrophobic_run": 1 + index % 4,
            "net_charge_ph7_4": float(index % 6),
            "macrel_amp_probability": 0.2 + index / 100,
            "llamp_log10_mic_um": 3.0 - index / 100,
            "amp_read_log10_mic_um": 3.2 - index / 100,
            "toxinpred3_hybrid_score": 0.8 - index / 100,
            "macrel_hemolysis_probability": 0.9 - index / 100,
        },
        "labels": {
            "toxinpred3_label": "Non-Toxin",
            "macrel_hemolysis_label": "low",
        },
    }


def _sequence(index: int) -> str:
    alphabet = "ACDEFGHIKLMNPQRSTVWY"
    digest = hashlib.sha256(f"v32-fixture-{index}".encode()).digest()
    return "".join(alphabet[value % len(alphabet)] for value in digest[:12])


def test_v32_manifest_freezes_evidence_governance_and_defers_charge() -> None:
    manifest = MultiobjectivePortfolioManifest.model_validate(_payload())
    assert manifest.charge_policy == "observe_only_defer_optimization_to_v33"
    assert sum(lane.quota for lane in manifest.lanes) == 24
    assert manifest.scientific_contract["all_agent_evidence_persisted"] is True
    assert manifest.scientific_contract["full_replay_required"] is True


def test_v32_rejects_charge_as_an_objective() -> None:
    payload = _payload()
    payload["lanes"][0]["objectives"][0]["metric_name"] = "net_charge_ph7_4"
    with pytest.raises(ValidationError, match="must not optimize positive charge"):
        MultiobjectivePortfolioManifest.model_validate(payload)


def test_portfolio_is_deterministic_diverse_and_has_no_weighted_score() -> None:
    manifest = MultiobjectivePortfolioManifest.model_validate(_payload())
    candidates = []
    for index in range(36):
        candidates.append(
            _candidate(index, manifest.seeds[index % 3], _sequence(index))
        )
    first = build_portfolio(candidates, manifest)
    second = build_portfolio(deepcopy(candidates), manifest)
    assert first == second
    assert first["selection_complete"] is True
    assert first["selected_count"] == 24
    assert first["weighted_total_score_used"] is False
    assert first["charge_optimized"] is False
    assert len({item["candidate_id"] for item in first["lane_results"]}) == 24
    selected = first["lane_results"]
    for left_index, left in enumerate(selected):
        for right in selected[left_index + 1 :]:
            assert (
                normalized_levenshtein_similarity(left["sequence"], right["sequence"])
                <= manifest.maximum_sequence_similarity
            )


def test_concordant_soft_risk_red_is_excluded_without_safety_claim() -> None:
    manifest = MultiobjectivePortfolioManifest.model_validate(_payload())
    candidates = [
        _candidate(index, manifest.seeds[index % 3], _sequence(index))
        for index in range(30)
    ]
    candidates[0]["labels"] = {
        "toxinpred3_label": "Toxin",
        "macrel_hemolysis_label": "high",
    }
    result = build_portfolio(candidates, manifest)
    assert candidates[0]["id"] in result["excluded_risk_red_candidate_ids"]
    assert all(
        item["claim_scope"] == "computational_multiobjective_hypothesis_only"
        for item in result["lane_results"]
    )


def test_missing_metric_fails_closed() -> None:
    manifest = MultiobjectivePortfolioManifest.model_validate(_payload())
    candidate = _candidate(1, manifest.seeds[0], "ACDEFGHIKLMN")
    del candidate["metrics"]["amp_read_log10_mic_um"]
    with pytest.raises(ValueError, match="missing metrics"):
        build_portfolio([candidate], manifest)


def test_database_only_replay_reconstructs_exact_portfolio() -> None:
    manifest = MultiobjectivePortfolioManifest.model_validate(_payload())
    candidates = [
        _candidate(index, manifest.seeds[index % 3], _sequence(index))
        for index in range(36)
    ]
    graph = {
        "candidates": [
            {
                "id": item["id"],
                "sequence": item["sequence"],
                "sequence_sha256": item["sequence_sha256"],
                "metadata": {"generator_seed": item["seed"]},
            }
            for item in candidates
        ],
        "evaluations": [
            *[
                {
                    "candidate_id": item["id"],
                    "metric_name": name,
                    "numeric_value": value,
                    "text_value": None,
                }
                for item in candidates
                for name, value in item["metrics"].items()
            ],
            *[
                {
                    "candidate_id": item["id"],
                    "metric_name": name,
                    "numeric_value": None,
                    "text_value": value,
                }
                for item in candidates
                for name, value in item["labels"].items()
            ],
        ],
    }
    assert replay_v32_portfolio(graph, manifest) == build_portfolio(candidates, manifest)


def test_formal_submission_contract_is_frozen_to_tested_revision() -> None:
    payload, _ = load_submission_contract(MANIFEST_PATH)
    assert payload["formal_run"]["submitted"] is False
    assert payload["formal_run"]["implementation_revision"] == (
        "fefaa3ce7c3b243e444fbd3037ab8a5829431759"
    )


def test_temporal_roles_register_v32_workflow_and_generation_activity() -> None:
    control = ROLE_CONFIG["control"]
    portfolio = ROLE_CONFIG["portfolio"]
    assert any(item.__name__ == "MultiobjectivePortfolioWorkflow" for item in control[2])
    assert any(item.__name__ == "generate_amp_designer_v32" for item in portfolio[1])


def test_worker_revision_is_exposed_as_a_setting() -> None:
    settings = Settings(worker_source_revision="a" * 40)
    assert settings.worker_source_revision == "a" * 40
