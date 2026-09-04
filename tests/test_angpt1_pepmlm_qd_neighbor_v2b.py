from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from analysis.evaluate_angpt1_pepmlm_qd_neighbor import evaluate

ROOT = Path(__file__).parents[1]
REPORT = ROOT / "reports/angpt1_pepmlm_qd_neighbor_v2b_20260904"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v2b_frozen_inputs_and_qd_are_reproducible(tmp_path: Path) -> None:
    output = tmp_path / "qd"
    receipt = evaluate(
        REPORT / "candidate_scores_calibrated.csv",
        REPORT / "challenger/challenger_review.csv",
        ROOT
        / (
            "reports/autoresearch_lineage_round135_angpt1_qd_fullsupport_prior_v1_close_20260902/"
            "quality_diversity_archive.json"
        ),
        output,
    )
    assert receipt["batch_candidate_count"] == 12
    assert receipt["quality_eligible_count"] == 9
    assert receipt["challenger_no_conflict_count"] == 12
    assert receipt["missing_verified_runtimes"] == ["apex", "peptiverse"]
    rows = list(csv.DictReader((output / "qd_candidates.csv").open(encoding="utf-8")))
    assert len(rows) == 9
    assert len({row["sequence_sha256"] for row in rows}) == 9
    summary = json.loads((output / "qd_summary.json").read_text(encoding="utf-8"))
    assert summary["archive_covered_cell_count"] == 79
    assert summary["archive_empty_cell_count"] == 2081
    assert summary["historical_run_modified"] is False


def test_v2b_integrity_and_pg_block_are_explicit() -> None:
    integrity = json.loads(
        (REPORT / "integrity_preflight.json").read_text(encoding="utf-8")
    )
    assert integrity["proposal_count"] == 12
    assert integrity["proposal_sequence_unique_count"] == 12
    assert integrity["score_all_verified"]["formal_12_complete_count"] == 12
    assert integrity["score_all_verified"]["display_eligible_count"] == 12
    assert integrity["challenger"]["reviewed_count"] == 12
    assert integrity["challenger"]["no_conflict_count"] == 12
    witness = integrity["frozen_parent_witness"]
    assert witness["generation_parent_run_id"] != witness["calibration_reference_run_id"]
    assert witness["roles_are_distinct"] is True
    close = json.loads(
        (REPORT / "close_receipt.json").read_text(encoding="utf-8")
    )
    assert close["status"] == "closed_unmaterialized_pg_blocked"
    assert close["pg_exact"] == {
        "preflight_status": "blocked",
        "materialization_status": "not_attempted_after_preflight",
        "attempted_once": True,
        "run_id": None,
        "candidate_count": 0,
        "evaluation_count": 0,
        "tool_call_id": None,
        "replay_count": 0,
        "identity_drift_count": 0,
    }
    assert close["coarse5"]["prepared_count"] == 0
    assert close["coarse5"]["dispatch_allowed"] is False


def test_v2b_integrity_and_blocked_pg_contract() -> None:
    integrity = json.loads(
        (REPORT / "integrity_preflight.json").read_text(encoding="utf-8")
    )
    assert integrity["proposal_count"] == 12
    assert integrity["proposal_sequence_unique_count"] == 12
    assert (
        _sha256(REPORT / "score_all_verified/candidate_scores.csv")
        == integrity["score_all_verified"]["sha256"]
    )
    assert (
        _sha256(REPORT / "candidate_scores_calibrated.csv")
        == integrity["calibration"]["sha256"]
    )
    assert (
        _sha256(REPORT / "challenger/challenger_review.csv")
        == integrity["challenger"]["sha256"]
    )
    assert integrity["frozen_parent_witness"]["roles_are_distinct"] is True
    blocked = json.loads(
        (REPORT / "materialization_blocked_receipt.json").read_text(encoding="utf-8")
    )
    assert blocked["status"] == "blocked"
    assert blocked["attempted_once"] is True
    assert blocked["pg_write_count"] == 0
    assert blocked["materialization_candidate_count"] == 2
    assert blocked["coarse5_prepared_count"] == 0
    assert blocked["offline_witness_is_not_pg_evidence"] is True
