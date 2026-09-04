from __future__ import annotations

import json

from analysis.build_targeted_rosetta_coarse5_backlog_vnext import (
    challenger_passes,
    close_is_authoritative,
    merge_rows,
    score_passes,
    task_key,
)


def _score() -> dict[str, str]:
    return {
        "formal_12_complete": "true",
        "formal_metric_count": "12",
        "display_eligible": "true",
        "excellent_sequence_stage_calibrated": "true",
        "activity_model_support_count_calibrated": "2",
        "guruprasad_instability_index": "10",
        "toxinpred3_label": "Non-Toxin",
        "macrel_hemolysis_label": "low",
    }


def test_local_gate_requires_formal_display_activity_and_safety() -> None:
    assert score_passes(_score())
    failed = {**_score(), "display_eligible": "false"}
    assert not score_passes(failed)


def test_challenger_requires_hemo_evidence_and_no_conflict() -> None:
    row = {
        "hemopi2_classification_label": "0",
        "validator_version": "hemopi2-v27",
        "hemopi2_classification_score": "0.2",
        "hemopi2_hc50_um": "150",
        "challenger_conflict_status": "no_conflict",
    }
    assert challenger_passes(row)
    assert not challenger_passes({**row, "challenger_conflict_status": "conflict"})


def test_close_receipt_accepts_legacy_postgresql_readback_shape() -> None:
    run_id = "11111111-1111-1111-1111-111111111111"
    close = {
        "target_key": "angpt1",
        "postgresql": {
            "run_id": run_id,
            "candidate_count": 2,
            "evaluation_count": 34,
            "subject_run_drift_count": 0,
            "replay_count": 0,
        },
    }
    assert close_is_authoritative(close, "angpt1", run_id, 2)


def test_merge_normalizes_task_key_and_preserves_alias() -> None:
    run_id = "11111111-1111-1111-1111-111111111111"
    candidate_id = "22222222-2222-2222-2222-222222222222"
    row = {
        "target_key": "angpt1",
        "target": "ANGPT1",
        "run_id": run_id,
        "candidate_id": candidate_id,
        "sequence_sha256": "a" * 64,
        "sequence": "ACDE",
        "qd_contribution": "empty_cell",
        "qd_cell": "q1",
        "qd_new_cell": 1,
        "qd_replacement": 0,
        "qd_quality": 0.9,
        "archive_qd_score": 1,
        "activity_support": 2,
        "formal12": 1,
        "display": 1,
        "challenger_status": "no_conflict",
        "quality_eligible": 1,
        "status": "prepared_not_dispatched",
        "existing_decoy_count": 0,
        "source_task_aliases": {"rosetta-coarse5:angpt1:" + run_id + ":" + candidate_id},
    }
    rows, exclusions = merge_rows(
        [],
        [row],
        {
            (run_id, candidate_id): {
                "structure_evidence_count": 0,
                "rosetta_decoy_count": 0,
                "succeeded_evaluations": 17,
                "lifecycle_events": [],
            }
        },
        {"angpt1": 2},
    )
    assert not exclusions
    assert rows[0]["rosetta_task_key"] == task_key("angpt1", run_id, candidate_id)
    assert json.loads(rows[0]["task_aliases"]) == [
        "rosetta-coarse5:angpt1:" + run_id + ":" + candidate_id
    ]
    assert rows[0]["status"] == "prepared"
    assert rows[0]["requires_remote_exact_preflight"] == "true"
