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


def test_v2b_pg_new_close_has_formal_qd_and_prepared_only_coarse5() -> None:
    history = json.loads(
        (REPORT / "pg_exact_history_receipt.json").read_text(encoding="utf-8")
    )
    close = json.loads(
        (REPORT / "pg_materialization_close_receipt.json").read_text(encoding="utf-8")
    )
    materialization = json.loads(
        (REPORT / "resume_v2b/materialization_receipt.json").read_text(encoding="utf-8")
    )
    readback = json.loads(
        (REPORT / "resume_v2b/pg_materialization_readback.json").read_text(
            encoding="utf-8"
        )
    )
    qd_evidence = json.loads(
        (REPORT / "resume_v2b/qd_evidence_persistence_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    coarse = json.loads(
        (REPORT / "resume_v2b/coarse5_prepared/coarse5_prepared_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert history["candidate_hit_count"] == 0
    assert history["operational_score_all_hit_count"] == 0
    assert history["rejected_occurrence_count"] == 0
    assert len(history["pg_new_hashes"]) == 2
    assert materialization["materialized_or_reused_in_run_count"] == 2
    assert readback["complete"] is True
    assert readback["candidate_count"] == 2
    assert readback["evaluation_count"] == 34
    assert readback["evaluation_count_per_candidate"] == [17, 17]
    assert readback["drift"] == 0
    assert qd_evidence["status"] == "readback_verified"
    assert qd_evidence["qd_evaluation_count"] == 6
    assert qd_evidence["new_cell_count"] == 2
    assert qd_evidence["replacement_count"] == 0
    assert close["status"] == "closed_formal_pg_new"
    assert close["source_provisional_qd"]["formal_pg_new"] is False
    assert close["final_qd"]["formal_pg_new"] is True
    assert close["final_qd"]["future_priority_only"] is False
    assert close["final_qd"]["quality_eligible_count"] == 2
    assert close["final_qd"]["materialized_contribution_counts"] == {
        "new_cell": 2,
        "replacement": 0,
    }
    assert close["persistence"]["authoritative_candidate_count"] == 2
    assert close["persistence"]["evaluation_count"] == 34
    assert close["persistence"]["qd_evidence_evaluation_count"] == 6
    assert coarse["candidate_count"] == 2
    assert coarse["nstruct"] == 5
    assert coarse["dispatch_allowed"] is False
    assert coarse["pool_a_admitted_count"] == 0
