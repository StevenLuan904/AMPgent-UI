from pathlib import Path
from tempfile import TemporaryDirectory

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
    assert manifest.formal_run.implementation_revision == (
        "fab5cac50b3d709e9435c732173bc22eba81a505"
    )
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
    assert manifest.literature_evidence_basis["manifest_sha256"] == (
        "309062137acc291ae58346fa9b80b5025a5438c7def097e67e235182bbb98e6a"
    )


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


def test_v33_preregistration_rejects_literature_manifest_drift() -> None:
    config_payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    source = (
        CONFIG.parent
        / config_payload["literature_evidence_basis"]["manifest_path"]
    ).resolve()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        benchmark_dir = root / "benchmarks"
        evidence_dir = root / "evidence"
        benchmark_dir.mkdir()
        evidence_dir.mkdir()
        config_copy = benchmark_dir / CONFIG.name
        literature_copy = evidence_dir / source.name
        config_copy.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        literature_copy.write_bytes(source.read_bytes() + b"\n# drift\n")
        with pytest.raises(ValueError, match="checksum mismatch"):
            load_v33_preregistration(config_copy)


def test_v33_literature_manifest_freezes_external_targets_and_anti_extrapolation() -> None:
    config_payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    literature_path = (
        CONFIG.parent
        / config_payload["literature_evidence_basis"]["manifest_path"]
    ).resolve()
    literature = yaml.safe_load(literature_path.read_text(encoding="utf-8"))
    evidence_ids = {item["evidence_id"] for item in literature["evidence_items"]}
    assert {
        "v13k_charge_series_2008",
        "charge_patterning_2019",
        "ar23_position_distribution_2016",
        "wr_wk_length_series_2016",
        "alpha_defensin_K_R_context_2009",
        "w6k8_w6r8_2026",
    }.issubset(evidence_ids)
    assert literature["biological_target_policy"]["v32_distribution_use"] == (
        "generator_coverage_and_budget_feasibility_only"
    )
    assert "absolute_net_charge_optimum" in literature["biological_target_policy"][
        "target_is_not"
    ]
    assert "call_K_or_R_globally_superior" in literature[
        "cross_study_inference_rules"
    ]["forbidden"]
