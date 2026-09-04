from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.build_targeted_rosetta_coarse5_backlog import (
    build_local,
    quality_gate,
    sort_rows,
    task_key,
)


def test_task_key_uses_authoritative_uuid_not_proposal_id() -> None:
    value = task_key("acea", "run-1", "11111111-1111-1111-1111-111111111111")
    assert value == "rosetta5:acea:run-1:11111111-1111-1111-1111-111111111111"
    assert "proposal-" not in value


def test_sort_primary_key_is_balance_then_qd_quality() -> None:
    rows = [
        {
            "balance_gap_to_50": 0,
            "qd_new_cell": 1,
            "qd_replacement": 0,
            "qd_quality": 0.99,
            "activity_support": 3,
            "target_key": "acea",
            "run_id": "a",
            "candidate_id": "1",
        },
        {
            "balance_gap_to_50": 4,
            "qd_new_cell": 0,
            "qd_replacement": 1,
            "qd_quality": 0.1,
            "activity_support": 2,
            "target_key": "fgf2",
            "run_id": "b",
            "candidate_id": "2",
        },
    ]
    assert sort_rows(rows)[0]["target_key"] == "fgf2"


def test_quality_gate_rejects_display_or_safety_failure() -> None:
    row = {
        "formal_12_complete": "true",
        "formal_metric_count": "12",
        "display_eligible": "false",
        "excellent_sequence_stage_calibrated": "true",
        "activity_model_support_count_calibrated": "2",
        "guruprasad_instability_index": "10",
        "toxinpred3_label": "Non-Toxin",
        "macrel_hemolysis_label": "low",
    }
    assert not quality_gate(row, {"quality_eligible": True})


def test_build_excludes_existing_hit_and_keeps_exact_run_identity(tmp_path: Path) -> None:
    benchmark = {
        "records": [
            {
                "target_key": "acea",
                "run_id": "run-1",
                "source": "PepMLM",
                "source_scope": "run",
                "evidence": {"qd_path": "qd.json", "score_path": "score.csv"},
            }
        ]
    }
    (tmp_path / "qd.json").write_text(
        json.dumps(
            {
                "candidate_id": "a" * 64,
                "contribution": "empty_cell",
                "quality_eligible": True,
                "actual_cell_id": "q1",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "score.csv").write_text(
        "sequence_sha256,formal_12_complete,formal_metric_count,display_eligible,excellent_sequence_stage_calibrated,activity_model_support_count_calibrated,guruprasad_instability_index,toxinpred3_label,macrel_hemolysis_label\n"
        + "a" * 64
        + ",true,12,true,true,2,10,Non-Toxin,low\n",
        encoding="utf-8",
    )
    pg = {
        ("run-1", "a" * 64): {
            "candidate_id": "11111111-1111-1111-1111-111111111111",
            "run_id": "run-1",
            "sequence": "ACDE",
            "sequence_sha256": "a" * 64,
            "target": "P0",
            "metadata_json": {"challenger_conflict_status": "no_conflict"},
            "succeeded_evaluations": 15,
            "structure_evidence_count": 0,
        }
    }
    (tmp_path / "prepared_structure_receipt.json").write_text(
        "11111111-1111-1111-1111-111111111111", encoding="utf-8"
    )
    rows, summary = build_local(benchmark, tmp_path, pg, {"acea": 50}, None)
    assert rows == []
    assert summary["excluded_counts"]["existing_structure_or_task"] == 1
