from pathlib import Path

import pytest
import yaml

from pepagent.v33_preregistration import (
    V33Preregistration,
    load_v33_preregistration,
)

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_charge_search_sufficiency_v33.yaml"


def test_v33_preregistration_freezes_charge_pairs_and_search_budget() -> None:
    manifest = load_v33_preregistration(CONFIG)

    assert manifest.formal_run.execution_authorized is False
    assert manifest.formal_run.submitted is False
    assert manifest.generator.valid_stream_checkpoints == [25, 50, 100, 150, 200]
    assert len(manifest.generator.development_seeds) == 3
    assert len(manifest.generator.confirmation_seeds) == 2
    assert {arm.name for arm in manifest.arms} == {
        "baseline_unedited",
        "lysine_one",
        "arginine_one",
        "one_charge_preserving_control",
        "lysine_two",
        "arginine_two",
        "two_charge_preserving_control",
    }
    assert manifest.search_sufficiency.fixed_full_budget_required is True
    assert "global_optimum" in manifest.search_sufficiency.forbidden_verdicts
    assert manifest.database_evidence_contract[
        "database_object_store_only_replay_required"
    ] is True
    assert manifest.parent_evidence["permitted_use"] == (
        "generator_coverage_diagnostic_and_frozen_baseline_only"
    )
    assert manifest.literature_evidence_basis["target_rule"] == (
        "relative_matched_intervention_not_absolute_v32_derived_interval"
    )
    assert len(manifest.literature_evidence_basis["primary_studies"]) >= 5


def test_v33_preregistration_rejects_execution_or_target_drift() -> None:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload["formal_run"]["execution_authorized"] = True
    with pytest.raises(ValueError, match="not authorized"):
        V33Preregistration.model_validate(payload)

    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload["parent_evidence"]["permitted_use"] = "distribution_calibration"
    with pytest.raises(ValueError, match="cannot define"):
        V33Preregistration.model_validate(payload)


def test_v33_preregistration_rejects_missing_database_evidence() -> None:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload["database_evidence_contract"]["persist_checkpoint_archive_snapshots"] = False
    with pytest.raises(ValueError, match="evidence contract"):
        V33Preregistration.model_validate(payload)
