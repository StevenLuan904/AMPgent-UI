from pathlib import Path

import pytest
import yaml

from pepagent.v34_ablation import deterministic_arm_order, paired_factorial_contrasts
from pepagent.v34_preregistration import V34Preregistration, load_v34_preregistration

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_knowledge_pepshot_ablation_v34.yaml"


def test_v34_freezes_exact_factorial_and_database_contract() -> None:
    manifest = load_v34_preregistration(CONFIG)

    assert manifest.formal_run.execution_authorized is False
    assert manifest.formal_run.submitted is False
    assert manifest.formal_run.implementation_revision == (
        "12cd18e9790fe67503709406c007d49cd5f677eb"
    )
    assert manifest.parent_cohort["expected_parent_count"] == 24
    assert len(manifest.parent_cohort["members"]) == 24
    assert manifest.parent_cohort["member_manifest_sha256"] == (
        "f1955476cb761d9ca300a8fed00d9bb847e775ee5f4c1ef51d1346376a4f943e"
    )
    assert {arm["name"] for arm in manifest.factorial_design["arms"]} == {
        "baseline",
        "cards_only",
        "pepshot_only",
        "cards_and_pepshot",
    }
    assert manifest.budget_contract["raw_proposals_per_parent_arm"] == 8
    assert manifest.independent_evaluation["weighted_total_score_forbidden"] is True
    assert manifest.analysis_contract["promotion_rule"]["current_margin_status"] == (
        "frozen_before_execution"
    )
    assert manifest.database_evidence_contract[
        "database_object_store_only_replay_required"
    ] is True


def test_v34_rejects_missing_arm_blinding_or_evidence() -> None:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload["factorial_design"]["arms"].pop()
    with pytest.raises(ValueError, match="exact knowledge by PepShot 2x2"):
        V34Preregistration.model_validate(payload)

    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload["factorial_design"]["assignment_reveal_after_locked_adjudication"] = False
    with pytest.raises(ValueError, match="blinded"):
        V34Preregistration.model_validate(payload)

    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload["database_evidence_contract"][
        "persist_context_query_pack_trace_cards_passages_and_policy"
    ] = False
    with pytest.raises(ValueError, match="database evidence"):
        V34Preregistration.model_validate(payload)

    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload["parent_cohort"]["members"][0]["candidate_id"] = payload["parent_cohort"][
        "members"
    ][1]["candidate_id"]
    with pytest.raises(ValueError, match="identities and sequences"):
        V34Preregistration.model_validate(payload)

    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload["formal_run"]["implementation_revision"] = "not-a-revision"
    with pytest.raises(ValueError, match="exact git SHA"):
        V34Preregistration.model_validate(payload)


def test_v34_deterministic_order_is_complete_stable_and_parent_specific() -> None:
    first = deterministic_arm_order("parent-a", "frozen-salt")
    assert first == deterministic_arm_order("parent-a", "frozen-salt")
    assert set(first) == {
        "baseline",
        "cards_only",
        "pepshot_only",
        "cards_and_pepshot",
    }
    assert first != deterministic_arm_order("parent-b", "frozen-salt")


def test_v34_factorial_contrasts_are_paired_and_direction_oriented() -> None:
    values = {
        "p1": {
            "baseline": 1.0,
            "cards_only": 3.0,
            "pepshot_only": 4.0,
            "cards_and_pepshot": 8.0,
        },
        "p2": {
            "baseline": 2.0,
            "cards_only": 4.0,
            "pepshot_only": 5.0,
            "cards_and_pepshot": 9.0,
        },
    }
    result = paired_factorial_contrasts(values, direction="maximize")
    assert result.knowledge_main_effect == pytest.approx(3.0)
    assert result.pepshot_main_effect == pytest.approx(4.0)
    assert result.knowledge_by_pepshot_interaction == pytest.approx(2.0)
    assert result.cards_only_vs_baseline == pytest.approx(2.0)
    assert result.pepshot_only_vs_baseline == pytest.approx(3.0)
    assert result.cards_and_pepshot_vs_baseline == pytest.approx(7.0)
    assert result.parent_count == 2

    minimized = paired_factorial_contrasts(values, direction="minimize")
    assert minimized.cards_only_vs_baseline == pytest.approx(-2.0)


def test_v34_factorial_analysis_fails_on_incomplete_parent_block() -> None:
    with pytest.raises(ValueError, match="exactly four arms"):
        paired_factorial_contrasts(
            {"p1": {"baseline": 1.0, "cards_only": 2.0}}, direction="maximize"
        )
