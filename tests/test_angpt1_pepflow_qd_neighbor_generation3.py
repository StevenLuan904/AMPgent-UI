from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

from generate_angpt1_pepflow_qd_neighbor_vnext import (  # noqa: E402, I001
    PARENT_IDS,
    PARENT_RUN_ID,
    select_pg_new,
)


REPORT = ROOT / "reports/angpt1_pepflow_qd_neighbor_generation3_20260904_v2"
PARENT_READBACK = (
    ROOT / "reports/angpt1_pepflow_qd_neighbor_generation3_20260904"
    / "parent_pg_exact_readback_receipt.json"
)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_generation3_is_bounded_pg_new_and_parent_bound() -> None:
    proposals = _rows(REPORT / "proposals.csv")
    assert len(proposals) == 12
    assert len({row["sequence_sha256"] for row in proposals}) == 12
    assert {row["parent_candidate_id"] for row in proposals} <= set(PARENT_IDS)
    assert {row["parent_run_id"] for row in proposals} == {PARENT_RUN_ID}
    assert {row["proposal_mode"] for row in proposals} == {"pepflow_qd_neighbor_1aa"}
    assert all(row["history_gate"].endswith("candidate_hash_preflight_passed") for row in proposals)

    history = json.loads((REPORT / "pg_bounded_history_receipt.json").read_text())
    assert history["status"] == "readback_verified"
    assert history["full_sequence_scan"] is False
    assert history["queried_hash_count"] == 24
    assert history["history_hit_count"] == 0
    assert history["selected_pg_new_count"] == 12

    parent = json.loads(PARENT_READBACK.read_text())
    assert parent["status"] == "readback_verified"
    assert parent["run_id"] == PARENT_RUN_ID
    assert parent["candidate_ids"] == list(PARENT_IDS)
    assert parent["candidate_count"] == 3
    assert parent["evaluation_count"] == 51
    assert parent["identity_drift"] == 0


def test_generation3_stages_and_close_contract() -> None:
    score = json.loads((REPORT / "score_all/receipt.json").read_text())
    calibration = json.loads((REPORT / "calibration_receipt.json").read_text())
    challenger = json.loads((REPORT / "challenger/receipt.json").read_text())
    qd = json.loads((REPORT / "provisional_qd.json").read_text())
    close = json.loads((REPORT / "close_receipt.json").read_text())
    material = json.loads((REPORT / "materialization_receipt.json").read_text())
    replay = json.loads((REPORT / "materialization_replay.json").read_text())
    coarse = json.loads(
        (REPORT / "coarse5_prepared/coarse5_prepared_receipt.json").read_text()
    )
    backlog = json.loads(
        (
            ROOT
            / "reports/targeted_rosetta_coarse5_backlog_20260904"
            / "vnext_angpt1_generation3_20260904/targeted_rosetta_coarse5_backlog_vnext.json"
        ).read_text()
    )

    assert (score["proposal_count"], score["formal_12_complete_count"]) == (12, 12)
    assert score["display_eligible_count"] == 10
    assert calibration["candidate_count"] == 12
    assert calibration["support_ge_2_count"] == 12
    assert challenger["challenger_no_conflict_count"] == 12
    assert challenger["challenger_conflict_count"] == 0
    assert qd["quality_eligible_count"] == 10
    assert (qd["new_cell_count"], qd["replacement_count"]) == (3, 0)
    assert material["materialized_or_reused_in_run_count"] == 3
    assert material["inserted_evaluation_count"] == 51
    assert replay["replay_existing_operation"] is True
    assert replay["inserted_evaluation_count"] == 0
    assert close["final_qd"] == {
        "status": "formal_pg_new_materialized",
        "quality_eligible_count": 3,
        "new_cell_count": 3,
        "replacement_count": 0,
        "materialized_contribution_count": 3,
        "formal_pg_new": True,
        "future_priority_only": False,
    }
    assert close["persistence"]["evaluation_count"] == 51
    assert close["persistence"]["drift"] == 0
    assert coarse["candidate_count"] == 3
    assert coarse["nstruct"] == 5
    assert coarse["dispatch_allowed"] is False
    assert backlog["summary"]["rows"] == 56
    assert backlog["summary"]["target_counts"]["angpt1"] == 9
    assert backlog["summary"]["unique_identity"] is True
    assert backlog["summary"]["unique_canonical_task_key"] is True


def test_pg_new_selection_fails_closed_and_filters_exact_hashes() -> None:
    rows = [
        {"sequence_sha256": "a" * 64},
        {"sequence_sha256": "b" * 64},
    ]
    receipt = {"status": "readback_verified", "history_hits": [{"sequence_sha256": "a" * 64}]}
    assert select_pg_new(rows, receipt, limit=1) == [rows[1]]
    try:
        select_pg_new(rows, {"status": "unavailable"}, limit=1)
    except ValueError as exc:
        assert "exact history" in str(exc)
    else:
        raise AssertionError("unavailable PG history must not select proposals")
