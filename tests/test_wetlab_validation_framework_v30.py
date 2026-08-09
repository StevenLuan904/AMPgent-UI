from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
FRAMEWORK = ROOT / "config/benchmarks/amp_wetlab_validation_framework_v30.yaml"


def load_framework() -> dict[str, object]:
    return yaml.safe_load(FRAMEWORK.read_text(encoding="utf-8"))


def test_v30_is_a_non_authorizing_draft() -> None:
    framework = load_framework()
    assert framework["execution_status"] == "draft_requires_user_inputs"
    assert framework["preregistered"] is False
    assert framework["experiment_authorized"] is False
    assert framework["candidate_selection_authorized"] is False
    assert framework["candidate_sequences_included"] is False
    assert framework["existing_soft_scores_may_select_candidates"] is False


def test_every_user_input_block_is_unresolved() -> None:
    framework = load_framework()
    inputs = framework["required_user_inputs"]
    assert isinstance(inputs, dict)
    assert inputs
    assert all(block["unresolved"] is True for block in inputs.values())


def test_candidate_firewall_remains_closed() -> None:
    framework = load_framework()
    firewall = framework["candidate_set_firewall"]
    assert firewall["current_v25_cohort_read_for_this_draft"] is False
    assert firewall["candidate_ids_or_sequences_permitted_in_draft"] is False
    assert firewall["ranking_or_selection_permitted_in_draft"] is False
    assert framework["next_action"]["status"] == "blocked_on_user_experimental_context"
