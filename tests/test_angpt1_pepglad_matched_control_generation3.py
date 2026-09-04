from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/angpt1_pepglad_matched_control_generation3_20260904_v2"
BACKLOG = (
    ROOT
    / "reports/targeted_rosetta_coarse5_backlog_20260904"
    / "vnext_angpt1_matched_generation3_20260904"
)
PARENT_RUN_ID = "a9cde5ec-d241-561a-ae19-e8de0c6c95a3"
PARENT_IDS = {
    "3cf72071-92b5-4ad3-a111-1c9e733a68e2",
    "9adb7487-cdbe-4d75-890c-455959d41328",
    "3ebcb368-d393-47c9-9759-8ebde1ff5e87",
}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_matched_generation_keeps_all_twelve_slots_and_exact_identity() -> None:
    generation = _json(REPORT / "generation_receipt.json")
    proposals = _rows(REPORT / "proposals.csv")

    assert generation["matched_slot_count"] == 12
    assert generation["generated_pepglad_pair_count"] == 12
    assert generation["pg_new_pair_count"] == 12
    assert generation["proposal_count"] == 12
    assert generation["unpaired_slot_count"] == 0
    assert generation["budget_contract"] == "one_residue_only; no slot expansion"
    assert generation["full_reports_scan"] is False
    preflight = generation["pg_history_preflight"]
    assert preflight["status"] == "readback_verified"
    assert preflight["full_sequence_scan"] is False
    assert preflight["queried_hash_count"] == 12
    assert preflight["history_hit_count"] == 0
    assert len(proposals) == 12
    assert len({row["sequence_sha256"] for row in proposals}) == 12
    assert {row["target_key"] for row in proposals} == {"angpt1"}
    assert {row["generation"] for row in proposals} == {"3"}
    assert {
        row["donor_source"] for row in proposals
    } == {"PepGLAD"}
    assert {row["parent_run_id"] for row in proposals} == {PARENT_RUN_ID}
    assert {row["parent_candidate_id"] for row in proposals} <= PARENT_IDS
    assert len({row["matched_pepflow_row_number"] for row in proposals}) == 12
    assert all(len(row["donor_artifact_sha256"]) == 64 for row in proposals)
    assert all(len(row["donor_source_row_sha256"]) == 64 for row in proposals)
    assert all(row["history_gate"].startswith("postgresql_exact") for row in proposals)
    assert all("axes" in json.loads(row["delta_phi"]) for row in proposals)
    parent_positions = [
        (row["parent_candidate_id"], row["acceptor_start_zero_based"])
        for row in proposals
    ]
    assert len(set(parent_positions)) < len(parent_positions)


def test_matched_generation_closes_only_strict_qd_contributors() -> None:
    proposal_hashes = [row["sequence_sha256"] for row in _rows(REPORT / "proposals.csv")]
    score_hashes = [
        row["sequence_sha256"] for row in _rows(REPORT / "score_all/candidate_scores.csv")
    ]
    calibrated_hashes = [
        row["sequence_sha256"]
        for row in _rows(REPORT / "candidate_scores_calibrated.csv")
    ]
    challenger_hashes = [
        row["sequence_sha256"] for row in _rows(REPORT / "challenger/challenger_review.csv")
    ]
    score = _json(REPORT / "score_all/receipt.json")
    calibration = _json(REPORT / "calibration_receipt.json")
    challenger = _json(REPORT / "challenger/receipt.json")
    qd = _json(REPORT / "provisional_qd.json")
    selection = _json(REPORT / "selection_receipt.json")
    material = _json(REPORT / "materialization_receipt.json")
    replay = _json(REPORT / "materialization_replay.json")
    readback = _json(REPORT / "pg_exact_readback_receipt.json")
    close = _json(REPORT / "close_receipt.json")
    coarse = _json(REPORT / "coarse5_prepared/coarse5_prepared_receipt.json")

    assert proposal_hashes == score_hashes == calibrated_hashes == challenger_hashes
    assert (score["proposal_count"], score["formal_12_complete_count"]) == (12, 12)
    assert score["display_eligible_count"] == 11
    assert calibration["support_ge_2_count"] == 11
    assert challenger["reviewed_candidate_count"] == 12
    assert challenger["challenger_no_conflict_count"] == 11
    assert challenger["challenger_conflict_count"] == 1
    assert challenger["challenger_is_not_a_primary_hard_gate"] is True
    assert qd["quality_eligible_count"] == 10
    assert (qd["new_cell_count"], qd["replacement_count"]) == (3, 0)
    assert selection["strict_materialization_selection"]["selected"] == 3
    assert material["materialized_or_reused_in_run_count"] == 3
    assert material["inserted_evaluation_count"] == 51
    assert replay["replay_existing_operation"] is True
    assert replay["inserted_evaluation_count"] == 0
    assert readback["status"] == "readback_verified"
    assert readback["candidate_count"] == 3
    assert readback["evaluation_count"] == 51
    assert set(readback["evaluations_per_candidate"].values()) == {17}
    assert readback["identity_drift"] == 0
    assert close["final_qd"]["formal_pg_new"] is True
    assert close["final_qd"]["future_priority_only"] is False
    assert close["final_qd"]["materialized_contribution_count"] == 3
    assert coarse["candidate_count"] == 3
    assert coarse["nstruct"] == 5
    assert coarse["status"] == "prepared_not_dispatched"
    assert coarse["dispatch_allowed"] is False


def test_matched_comparison_and_global_backlog_are_unweighted_and_unique() -> None:
    comparison = _json(REPORT / "matched_source_comparison.json")
    arms = {arm["arm"]: arm for arm in comparison["arms"]}
    assert set(arms) == {"PepFlow", "PepGLAD"}
    assert comparison["no_weighted_total"] is True
    assert arms["PepFlow"]["counts"] == {
        "proposal": 12,
        "materialized": 3,
        "formal12": 12,
        "display": 10,
        "support_ge_2": 12,
        "excellent": 10,
        "challenger_reviewed": 12,
        "challenger_no_conflict": 12,
        "qd_eligible": 10,
        "qd_new_cell": 3,
        "qd_replacement": 0,
        "coarse5_prepared": 3,
    }
    assert arms["PepGLAD"]["counts"]["display"] == 11
    assert arms["PepGLAD"]["counts"]["support_ge_2"] == 11
    assert arms["PepGLAD"]["counts"]["challenger_no_conflict"] == 11
    assert arms["PepGLAD"]["missing_shadow_runtimes"] == ["apex", "peptiverse"]
    comparison_rows = _rows(REPORT / "matched_source_comparison.csv")
    assert all("wilson_low" in row and "wilson_high" in row for row in comparison_rows)
    backlog = _json(BACKLOG / "targeted_rosetta_coarse5_backlog_vnext.json")
    summary = backlog["summary"]
    assert summary["rows"] == 59
    assert summary["target_counts"]["angpt1"] == 12
    assert summary["unique_identity"] is True
    assert summary["unique_canonical_task_key"] is True
    assert backlog["dispatch_allowed"] is False
