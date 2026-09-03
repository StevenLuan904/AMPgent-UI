from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.build_source_effect_benchmark import build_benchmark


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def test_benchmark_keeps_same_sequence_separate_across_runs(tmp_path: Path) -> None:
    rows = (
        "sequence_sha256,formal_12_complete,display_eligible,"
        "activity_support_count_calibrated,excellent_sequence_stage_calibrated\n"
        "same,true,true,2,true\n"
    )
    for name, _run in (("a", "run-a"), ("b", "run-b")):
        _write(tmp_path / f"{name}.csv", rows)
        _write(
            tmp_path / f"{name}.json",
            json.dumps(
                {
                    "materialized_or_reused_in_run_count": 1,
                    "challenger_reviewed_count": 1,
                    "challenger_no_conflict_count": 1,
                }
            ),
        )
        _write(
            tmp_path / f"{name}-qd.json",
            json.dumps(
                {
                    "eligible_batch_candidate_count": 1,
                    "incumbent_replacement_count": 1,
                    "valid_cell_coverage": 1 / 2160,
                }
            ),
        )
    config = {
        "specs": [
            {
                "label": "A",
                "source": "PepFlow",
                "target_key": "acea",
                "run_id": "run-a",
                "proposal_count_override": 1,
                "score_path": "a.csv",
                "materialization_path": "a.json",
                "qd_path": "a-qd.json",
            },
            {
                "label": "B",
                "source": "PepFlow",
                "target_key": "acea",
                "run_id": "run-b",
                "proposal_count_override": 1,
                "score_path": "b.csv",
                "materialization_path": "b.json",
                "qd_path": "b-qd.json",
            },
        ]
    }
    result = build_benchmark(config, tmp_path)
    assert [record["run_id"] for record in result["records"]] == ["run-a", "run-b"]
    assert all(record["within_group_unique_sequence_sha256"] for record in result["records"])


def test_source_split_recomputes_qd_and_conflict_counts(tmp_path: Path) -> None:
    _write(
        tmp_path / "score.csv",
        "sequence_sha256,donor_source,formal_12_complete,display_eligible,"
        "activity_support_count_calibrated,excellent_sequence_stage_calibrated\n"
        "a,PepFlow,true,true,2,true\n"
        "b,PepGLAD,true,false,1,false\n",
    )
    _write(
        tmp_path / "proposal.csv",
        "sequence_sha256,donor_source\na,PepFlow\nb,PepGLAD\n",
    )
    _write(
        tmp_path / "challenger.csv",
        "sequence_sha256,donor_source,challenger_conflict_status\n"
        "a,PepFlow,no_conflict\nb,PepGLAD,conflict\n",
    )
    _write(tmp_path / "material.json", json.dumps({"source_candidate_count": 2}))
    _write(
        tmp_path / "qd.json",
        json.dumps(
            {
                "contributions": [
                    {
                        "candidate_id": "a",
                        "cell_id": "q1",
                        "contribution": "empty_cell",
                        "quality": 0.8,
                    },
                    {
                        "candidate_id": "b",
                        "cell_id": "q2",
                        "contribution": "quality_gate_failed",
                        "quality": 0.2,
                    },
                ]
            }
        ),
    )
    result = build_benchmark(
        {
            "specs": [
                {
                    "label": "mixed",
                    "source": "mixed",
                    "target_key": "target_agnostic_amp",
                    "run_id": "run-1",
                    "source_split": True,
                    "score_path": "score.csv",
                    "proposal_path": "proposal.csv",
                    "challenger_path": "challenger.csv",
                    "materialization_path": "material.json",
                    "qd_path": "qd.json",
                }
            ]
        },
        tmp_path,
    )
    by_source = {record["source"]: record for record in result["records"]}
    assert by_source["PepFlow"]["counts"]["qd_new_cell"] == 1
    assert by_source["PepFlow"]["counts"]["challenger_no_conflict"] == 1
    assert by_source["PepGLAD"]["counts"]["qd_eligible"] == 0
    assert by_source["PepGLAD"]["counts"]["challenger_conflict"] == 1


def test_ci_is_explicit_for_small_denominators(tmp_path: Path) -> None:
    _write(tmp_path / "score.csv", "sequence_sha256,formal_12_complete\na,true\n")
    _write(tmp_path / "material.json", json.dumps({"source_candidate_count": 1}))
    _write(tmp_path / "qd.json", json.dumps({"eligible_batch_candidate_count": 0}))
    result = build_benchmark(
        {
            "specs": [
                {
                    "label": "small",
                    "source": "PepMLM",
                    "target_key": "acea",
                    "run_id": "run-1",
                    "proposal_count_override": 1,
                    "score_path": "score.csv",
                    "materialization_path": "material.json",
                    "qd_path": "qd.json",
                }
            ]
        },
        tmp_path,
    )
    rate = result["records"][0]["rates"]["materialized_to_formal12"]
    assert rate["ci95"][0] == pytest.approx(0.20654931437728953)
    assert rate["ci95"][1] == 1.0


def test_target_specific_qd_without_rosetta_is_not_pool_a(tmp_path: Path) -> None:
    _write(
        tmp_path / "score.csv",
        "sequence_sha256,formal_12_complete,display_eligible,"
        "activity_model_support_count_calibrated\n"
        "child,true,true,2\n",
    )
    _write(
        tmp_path / "material.json",
        json.dumps(
            {
                "operational_run_id": "run-target",
                "materialized_or_reused_in_run_count": 1,
                "inserted_evaluation_count": 17,
                "challenger_reviewed_count": 1,
                "challenger_no_conflict_count": 1,
            }
        ),
    )
    _write(
        tmp_path / "qd.json",
        json.dumps(
            {
                "actual_cell_id": "q1-h1-m1-l1",
                "eligible_batch_candidate_count": 1,
                "new_cell": True,
                "contribution": "empty_cell",
            }
        ),
    )
    result = build_benchmark(
        {
            "specs": [
                {
                    "label": "targeted",
                    "source": "PepMLM",
                    "target_key": "fgf2",
                    "run_id": "run-target",
                    "proposal_count_override": 1,
                    "score_path": "score.csv",
                    "materialization_path": "material.json",
                    "qd_path": "qd.json",
                    "pool_a_admitted_count": 0,
                }
            ]
        },
        tmp_path,
    )
    record = result["records"][0]
    assert record["counts"]["qd_eligible"] == 1
    assert record["counts"]["rosetta_pending_candidates"] == 1
    assert record["counts"]["pool_a_admitted"] == 0
    assert result["weighted_total_used"] is False


def test_single_candidate_qd_receipt_flags_are_counted(tmp_path: Path) -> None:
    _write(
        tmp_path / "score.csv",
        "sequence_sha256,formal_12_complete,display_eligible,"
        "activity_model_support_count_calibrated\n"
        "child,true,true,2\n",
    )
    _write(
        tmp_path / "material.json",
        json.dumps({"materialized_or_reused_in_run_count": 1}),
    )
    _write(
        tmp_path / "new-cell.json",
        json.dumps(
            {
                "eligible_batch_candidate_count": 1,
                "contribution": "empty_cell",
                "new_cell": True,
                "replacement": False,
            }
        ),
    )
    _write(
        tmp_path / "replacement.json",
        json.dumps(
            {
                "eligible_batch_candidate_count": 1,
                "contribution": "incumbent_replacement",
                "new_cell": False,
                "replacement": True,
            }
        ),
    )

    def build(qd_name: str) -> dict:
        return build_benchmark(
            {
                "specs": [
                    {
                        "label": "single",
                        "source": "PepMLM",
                        "target_key": "target_agnostic_amp",
                        "run_id": qd_name,
                        "score_path": "score.csv",
                        "materialization_path": "material.json",
                        "qd_path": qd_name,
                    }
                ]
            },
            tmp_path,
        )["records"][0]

    new_cell = build("new-cell.json")
    replacement = build("replacement.json")
    assert new_cell["counts"]["qd_new_cell"] == 1
    assert new_cell["counts"]["qd_replacement"] == 0
    assert replacement["counts"]["qd_new_cell"] == 0
    assert replacement["counts"]["qd_replacement"] == 1
