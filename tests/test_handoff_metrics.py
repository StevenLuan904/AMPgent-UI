from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest
import yaml

from pepagent.domain.schemas import ExperimentSpec, TargetSpec
from pepagent.handoff_metrics import normalize_metric_records, physicochemical_descriptors
from pepagent.model_workers.amplify_metric_cli import (
    parse_tsv as parse_amplify_tsv,
)
from pepagent.model_workers.amplify_metric_cli import validate_sequence as amplify_domain
from pepagent.model_workers.llamp_metric_cli import validate_sequence
from pepagent.model_workers.macrel_metric_cli import (
    expected_macrel_sequence,
    parse_prediction_rows,
)
from pepagent.model_workers.sequence_metrics_cli import evaluate
from pepagent.validation import handoff as handoff_validation
from pepagent.validation.handoff import assess_qualitative_checks
from pepagent.workers.temporal_worker import ROLE_CONFIG


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        (
            "KWKLFKKIGAVLKVL",
            (1771.29, 4.987, 11.27880859375, 0.5333333333333333, 0.4139307319247934),
        ),
        (
            "ACDEFGHIK",
            (1019.14, -1.122, 5.173828125, 0.4444444444444444, 0.32641606784293714),
        ),
    ],
)
def test_modlamp_descriptors_reproduce_handoff_reference(
    sequence: str, expected: tuple[float, ...]
) -> None:
    result = physicochemical_descriptors(sequence)
    observed = (
        result["molecular_weight"],
        result["net_charge_ph7_4"],
        result["isoelectric_point"],
        result["hydrophobic_ratio"],
        result["hydrophobic_moment"],
    )
    assert observed == pytest.approx(expected, abs=1e-12)
    assert result["assumptions"]["modlamp_distribution_version"] == "4.3.2"


def test_optional_metrics_are_disabled_by_default_and_use_separate_worker() -> None:
    spec = ExperimentSpec(target=TargetSpec(name="target", sequence="ACDE"))
    assert spec.optional_metrics == []
    assert ROLE_CONFIG["metrics"][0] == "pepagent-cpu-metrics"


def test_handoff_trust_ceiling_prevents_unvalidated_hard_gate() -> None:
    with pytest.raises(ValueError, match="permits trust"):
        ExperimentSpec(
            target=TargetSpec(name="target", sequence="ACDE"),
            optional_metrics=[
                {
                    "name": "serum_half_life",
                    "trust": "soft",
                }
            ],
        )
    with pytest.raises(ValueError, match="cannot be a hard selection gate"):
        ExperimentSpec(
            target=TargetSpec(name="target", sequence="ACDE"),
            optional_metrics=[
                {
                    "name": "hemolysis_risk",
                    "trust": "soft",
                }
            ],
            metric_policy=[
                {
                    "metric_name": "hemopi2_hemolysis_score",
                    "role": "qualification",
                    "maximum": 0.5,
                    "hard": True,
                    "rationale": "test-only unsupported hard gate",
                }
            ],
        )


def test_builtin_metric_normalizes_records_and_chemistry_assumptions(tmp_path: Path) -> None:
    result = evaluate(
        {
            "run_id": "test-run",
            "plugin": {
                "name": "physicochemical_developability",
                "trust": "descriptor",
                "parameters": {"ph": 7.4, "c_terminal_amidated": True},
            },
            "candidates": [{"id": "candidate-1", "sequence": "GIGAVLKVLTTGLPALISWIKRKRQQ"}],
        },
        tmp_path,
        None,
    )
    assert result["status"] == "complete"
    assert result["records"][0]["candidate_id"] == "candidate-1"
    assert {item["metric_name"] for item in result["records"][0]["observations"]} == {
        "molecular_weight_da",
        "net_charge_ph7_4",
        "isoelectric_point",
        "hydrophobic_ratio_modlamp",
        "hydrophobic_moment_eisenberg",
    }
    assert "amidated C-terminus" in result["records"][0]["raw"]["assumptions"]["termini"]


def test_missing_external_adapter_is_unavailable_without_failing_candidates(
    tmp_path: Path,
) -> None:
    result = evaluate(
        {
            "run_id": "test-run",
            "plugin": {"name": "hemolysis_risk", "trust": "soft"},
            "candidates": [{"id": "candidate-1", "sequence": "KLLK"}],
        },
        tmp_path,
        tmp_path / "absent.yaml",
    )
    assert result["status"] == "unavailable"
    assert result["records"] == []


def test_external_adapter_retry_cannot_reuse_stale_predictions(tmp_path: Path) -> None:
    work_dir = tmp_path / "adapter-work"
    work_dir.mkdir()
    (work_dir / "predictions.csv").write_text(
        "candidate_id,sequence,amplify_probability,amplify_label\ncandidate-1,KLLK,0.99,AMP\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.yaml"
    registry_path.write_text(
        yaml.safe_dump(
            {
                "adapters": {
                    "amp_likeness": {
                        "enabled": True,
                        "version": "no-output-test",
                        "command": [sys.executable, "-c", "pass"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = evaluate(
        {
            "run_id": "test-run",
            "plugin": {"name": "amp_likeness", "trust": "soft"},
            "candidates": [{"id": "candidate-1", "sequence": "KLLK"}],
        },
        work_dir,
        registry_path,
    )

    assert result["status"] == "unavailable"
    assert result["reason"] == "external adapter exited with code 0"
    assert not (work_dir / "predictions.csv").exists()


def test_macrel_adapter_parses_comments_and_declares_n_terminal_met_normalization(
    tmp_path: Path,
) -> None:
    prediction_path = tmp_path / "macrel.out.prediction.gz"
    with gzip.open(prediction_path, "wt", encoding="utf-8", newline="") as stream:
        stream.write("# macrel 1.6.1\n")
        stream.write("Access\tSequence\tAMP_probability\n")
        stream.write("candidate-1\tKLLK\t0.812\n")

    assert parse_prediction_rows(prediction_path) == [
        {"Access": "candidate-1", "Sequence": "KLLK", "AMP_probability": "0.812"}
    ]
    assert expected_macrel_sequence("MKLLK") == (
        "KLLK",
        "Macrel removed the N-terminal M",
    )
    assert expected_macrel_sequence("KLLK") == ("KLLK", "")


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        ("GIGAVLKVLTTGLPALISWIKRKRQQ", None),
        ("KLLK", "outside released LLAMP 5-50 residue domain"),
        ("KLLKX", "non-standard or empty peptide sequence"),
    ],
)
def test_llamp_adapter_enforces_released_sequence_domain(
    sequence: str, expected: str | None
) -> None:
    assert validate_sequence(sequence) == expected


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        ("GIGAVLKVLTTGLPALISWIKRKRQQ", None),
        ("A", "outside released AMPlify 2-200 residue domain"),
        ("KLLKX", "non-standard or empty peptide sequence"),
    ],
)
def test_amplify_adapter_enforces_released_sequence_domain(
    sequence: str, expected: str | None
) -> None:
    assert amplify_domain(sequence) == expected


def test_amplify_adapter_parses_exact_sequence_and_released_threshold(
    tmp_path: Path,
) -> None:
    output = tmp_path / "amplify.tsv"
    output.write_text(
        "Sequence_ID\tSequence\tProbability_score\tPrediction\t"
        "Sub_model_1_probability_score\tSub_model_2_probability_score\t"
        "Sub_model_3_probability_score\tSub_model_4_probability_score\t"
        "Sub_model_5_probability_score\n"
        "candidate-1\tKLLK\t0.75\tAMP\t0.7\t0.8\t0.75\t0.76\t0.74\n",
        encoding="utf-8",
    )

    parsed = parse_amplify_tsv(output, {"candidate-1": "KLLK"})

    assert parsed["candidate-1"]["amplify_probability"] == 0.75
    assert parsed["candidate-1"]["amplify_label"] == "AMP"


def test_amplify_adapter_rejects_label_threshold_mismatch(tmp_path: Path) -> None:
    output = tmp_path / "amplify.tsv"
    output.write_text(
        "Sequence_ID\tSequence\tProbability_score\tPrediction\t"
        "Sub_model_1_probability_score\tSub_model_2_probability_score\t"
        "Sub_model_3_probability_score\tSub_model_4_probability_score\t"
        "Sub_model_5_probability_score\n"
        "candidate-1\tKLLK\t0.75\tnon-AMP\t0.7\t0.8\t0.75\t0.76\t0.74\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="label/threshold mismatch"):
        parse_amplify_tsv(output, {"candidate-1": "KLLK"})


@pytest.mark.parametrize(
    ("plugin", "row", "expected"),
    [
        (
            "hemolysis_risk",
            {
                "candidate_id": "c1",
                "sequence": "KLLK",
                "status": "success",
                "hemopi2_score": "0.61",
                "hemopi2_label": "Hemolytic",
                "decision": "review",
            },
            {"hemopi2_hemolysis_score", "hemopi2_hemolysis_label", "hemolysis_consensus_decision"},
        ),
        (
            "sequence_novelty",
            {
                "candidate_id": "c1",
                "sequence": "KLLK",
                "status": "ok",
                "nearest_fident": "0.75",
                "nearest_query_coverage": "1.0",
                "esm2_cosine_similarity": "0.8",
            },
            {
                "mmseqs_nearest_identity",
                "mmseqs_nearest_query_coverage",
                "esm2_nearest_cosine_similarity",
            },
        ),
        (
            "aggregation_apr",
            {
                "candidate_id": "c1",
                "sequence": "KLLK",
                "status": "success",
                "score_mean": "0.2",
                "score_max": "0.7",
            },
            {"aggrescanai_apr_mean", "aggrescanai_apr_max"},
        ),
    ],
)
def test_handoff_csv_fields_map_to_stable_metric_names(
    plugin: str,
    row: dict[str, str],
    expected: set[str],
) -> None:
    normalized = normalize_metric_records(plugin, [row])
    assert {item["metric_name"] for item in normalized[0]["observations"]} == expected


def test_public_complex_validation_uses_full_sequence_for_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    full_sequence = "GIGAVLKVLTTGLPALISWIKRKRQQ"
    modeled_sequence = "IGAVLKVLTTGLPALISWIKRKR"
    observed_payloads: list[dict[str, object]] = []

    monkeypatch.setattr(handoff_validation, "sha256_file", lambda _: "locked-sha")
    monkeypatch.setattr(
        handoff_validation,
        "atom_chain_sequence",
        lambda _path, _chains: modeled_sequence,
    )

    def fake_evaluate(
        payload: dict[str, object], _work_root: Path, _registry_path: Path | None
    ) -> dict[str, object]:
        observed_payloads.append(payload)
        return {
            "status": "complete",
            "records": [
                {
                    "observations": [
                        {
                            "metric_name": "molecular_weight_da",
                            "numeric_value": 2846.48,
                        }
                    ]
                }
            ],
        }

    monkeypatch.setattr(handoff_validation, "evaluate", fake_evaluate)
    suite = {
        "suite_id": "public-control",
        "case": {
            "pdb_id": "8AHS",
            "source_sha256": "locked-sha",
            "peptide_chain": "C",
            "peptide_sequence": full_sequence,
            "modeled_peptide_sequence": modeled_sequence,
            "property_primary_source": {"citation": "public assay"},
        },
        "metrics": [
            {
                "name": "physicochemical_developability",
                "trust": "descriptor",
                "stages": ["final"],
            }
        ],
        "descriptor_reference": {"molecular_weight_da": 2846.48},
    }

    result = handoff_validation.validate_handoff_metric_control(
        suite,
        tmp_path / "8ahs.pdb",
        tmp_path / "work",
        None,
    )

    assert result["modeled_peptide_sequence"] == modeled_sequence
    assert observed_payloads[0]["candidates"] == [
        {"id": "pdb-8ahs-auth-chain-c", "sequence": full_sequence}
    ]
    assert result["descriptor_reproduced"] is True


def test_public_control_separates_runtime_success_from_scientific_conflict() -> None:
    checks = [
        {
            "name": "toxicity_direction",
            "plugin": "toxicity_risk",
            "metric_name": "toxinpred3_label",
            "operator": "eq",
            "value": "Toxin",
            "expected": "toxic-risk direction",
        },
        {
            "name": "mic_available",
            "plugin": "mic_potency",
            "metric_name": "llamp_predicted_mic_um",
            "operator": "finite",
            "expected": "finite prediction",
        },
    ]
    results = {
        "toxicity_risk": {
            "status": "complete",
            "records": [
                {
                    "observations": [
                        {
                            "metric_name": "toxinpred3_label",
                            "numeric_value": None,
                            "text_value": "Non-Toxin",
                        }
                    ]
                }
            ],
        },
        "mic_potency": {"status": "unavailable", "records": []},
    }

    assessments = assess_qualitative_checks(checks, results)

    assert assessments["toxicity_direction"]["status"] == "conflicting"
    assert assessments["mic_available"]["status"] == "unavailable"
