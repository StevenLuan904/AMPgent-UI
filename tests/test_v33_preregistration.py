from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from pepagent.provenance.hashing import sha256_bytes
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
        "0bb8fb65c7bc42f427e9c06e55c2fab4cb8a7e26"
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
    assert manifest.search_sufficiency.archive_method_version == (
        "v33-search-sufficiency-v2"
    )
    assert manifest.search_sufficiency.methods_evidence_manifest_sha256 == (
        "b5c3629cf19d90a6962d048cbe6bf8ff1d6ee7bef7ae449ffe03c649aa5470e6"
    )
    assert (
        manifest.search_sufficiency.cross_seed_attainment_gate
        .symmetric_recurrence_required_for_saturation
        is True
    )
    assert (
        manifest.search_sufficiency.saturation_gate
        .maximum_epsilon_cell_turnover_fraction
        == 0.10
    )
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
        "94096787d62233e9dca77f277bc24ec18ce512e9cb49db740255541f02b897e4"
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


def test_v33_preregistration_rejects_search_methods_manifest_drift() -> None:
    config_payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    literature_source = (
        CONFIG.parent
        / config_payload["literature_evidence_basis"]["manifest_path"]
    ).resolve()
    methods_source = (
        CONFIG.parent
        / config_payload["search_sufficiency"]["methods_evidence_manifest_path"]
    ).resolve()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        benchmark_dir = root / "benchmarks"
        evidence_dir = root / "evidence"
        benchmark_dir.mkdir()
        evidence_dir.mkdir()
        config_copy = benchmark_dir / CONFIG.name
        literature_copy = evidence_dir / literature_source.name
        methods_copy = evidence_dir / methods_source.name
        config_copy.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        literature_copy.write_bytes(literature_source.read_bytes())
        methods_copy.write_bytes(methods_source.read_bytes() + b"\n# drift\n")
        with pytest.raises(
            ValueError,
            match="search sufficiency methods manifest checksum mismatch",
        ):
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
        "kr12_bilayer_mechanism_2017",
        "r9_charge_insufficiency_2019",
    }.issubset(evidence_ids)
    assert len(literature["evidence_items"]) == 9
    for item in literature["evidence_items"]:
        assert len(item["source_record"]["sha256"]) == 64
        assert item["source_record"]["passage_locator"]
        assert item["evidence_grade"]
        assert item["applicability_distance"]
    assert literature["biological_target_policy"]["v32_distribution_use"] == (
        "generator_coverage_and_budget_feasibility_only"
    )
    assert "absolute_net_charge_optimum" in literature["biological_target_policy"][
        "target_is_not"
    ]
    assert "call_K_or_R_globally_superior" in literature[
        "cross_study_inference_rules"
    ]["forbidden"]
    assert {
        witness["conflict_id"]
        for witness in literature["cross_study_conflict_witnesses"]
    } == {
        "K_R_identity_direction_is_scaffold_dependent",
        "charge_amount_is_not_monotonic_activity_or_safety",
        "positive_charge_is_not_sufficient_for_activity",
    }


def test_v33_literature_manifest_rejects_benchmark_source_identity_drift() -> None:
    config_payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    literature_source = (
        CONFIG.parent
        / config_payload["literature_evidence_basis"]["manifest_path"]
    ).resolve()
    methods_source = (
        CONFIG.parent
        / config_payload["search_sufficiency"]["methods_evidence_manifest_path"]
    ).resolve()
    literature_payload = yaml.safe_load(literature_source.read_text(encoding="utf-8"))
    r9_item = next(
        item
        for item in literature_payload["evidence_items"]
        if item["evidence_id"] == "r9_charge_insufficiency_2019"
    )
    r9_item["citation"]["pmid"] = "99999999"
    r9_item["citation"]["source_uri"] = "https://pubmed.ncbi.nlm.nih.gov/99999999/"
    r9_item["source_record"]["retrieval_uri"] = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
        "db=pubmed&id=99999999&retmode=xml"
    )
    with TemporaryDirectory() as directory:
        root = Path(directory)
        benchmark_dir = root / "benchmarks"
        evidence_dir = root / "evidence"
        benchmark_dir.mkdir()
        evidence_dir.mkdir()
        literature_copy = evidence_dir / literature_source.name
        methods_copy = evidence_dir / methods_source.name
        config_copy = benchmark_dir / CONFIG.name
        literature_copy.write_text(
            yaml.safe_dump(literature_payload, sort_keys=False), encoding="utf-8"
        )
        methods_copy.write_bytes(methods_source.read_bytes())
        config_payload["literature_evidence_basis"]["manifest_sha256"] = sha256_bytes(
            literature_copy.read_bytes()
        )
        config_copy.write_text(
            yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8"
        )
        with pytest.raises(ValueError, match="PMID sets drifted"):
            load_v33_preregistration(config_copy)
