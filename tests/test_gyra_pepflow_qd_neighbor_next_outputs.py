import csv
import json
from pathlib import Path

REPORT = (
    Path(__file__).parents[1]
    / "reports"
    / "gyrA_pepflow_qd_neighbor_next_20260904"
)


def _json(name: str) -> dict:
    return json.loads((REPORT / name).read_text(encoding="utf-8"))


def test_next_batch_receipts_keep_provisional_and_formal_boundaries() -> None:
    with (REPORT / "proposals.csv").open(encoding="utf-8-sig", newline="") as stream:
        proposals = list(csv.DictReader(stream))
    score_receipt = _json("score_all/receipt.json")
    calibration = _json("calibration_receipt.json")
    challenger = _json("challenger/receipt.json")
    qd = _json("provisional_qd_receipt.json")
    close = _json("close_receipt.json")
    pg_history = _json("pg_exact_history_receipt.json")
    pg_close = _json("pg_materialization_close_receipt.json")
    qd_evidence = _json("qd_evidence_persistence_receipt.json")
    with (REPORT / "challenger/hemopi2_results.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        hemopi2_results = list(csv.DictReader(stream))

    assert len(proposals) == 12
    assert {row["historical_pg_gate"] for row in proposals} == {"pending"}
    assert {row["materialization_status"] for row in proposals} == {
        "proposed_not_materialized"
    }
    assert score_receipt["formal_12_complete_count"] == 12
    assert score_receipt["display_eligible_count"] == 11
    assert calibration["support_ge_2_count"] == 9
    assert challenger["reviewed_candidate_count"] == 12
    assert challenger["candidate_identity_coverage_count"] == 12
    assert challenger["candidate_identity_coverage_complete"] is True
    assert challenger["challenger_no_conflict_count"] == 12
    assert challenger["challenger_conflict_count"] == 0
    assert challenger["missing_verified_runtimes"] == ["apex", "peptiverse"]
    assert len(hemopi2_results) == 12
    assert {row["hemopi2_classification_label"] for row in hemopi2_results} == {"0"}
    assert {row["challenger_conflict_status"] for row in hemopi2_results} == {
        "no_conflict"
    }
    assert qd["status"] == "provisional_only"
    assert qd["provisional_new_cell_count"] == 8
    assert qd["provisional_replacement_count"] == 0
    assert qd["challenger_no_conflict_count"] == 12
    assert qd["provisional_eligible_after_challenger_count"] == 8
    assert close["persistence"]["pool_a_admitted"] is False
    assert close["persistence"]["historical_pg_gate"] == "evidence_verification_unavailable"
    assert close["persistence"]["postgresql_writes"] == 1
    assert close["persistence"]["materialization_writes"] == 0
    assert close["provisional_qd"]["formal_pg_new"] is False
    assert close["challenger"]["hemopi2_reviewed_count"] == 12
    assert close["challenger"]["hemopi2_no_conflict_count"] == 12
    assert pg_history["exact_history_status"] == "verified_exact_scan"
    assert pg_history["materialization_allowed"] is True
    assert pg_history["candidate_hit_count"] == 8
    assert pg_history["already_materialized_count"] == 8
    assert pg_history["pg_new_count"] == 8
    assert pg_history["rejected_occurrence_count"] == 0
    assert pg_history["materialization_status"] == "already_materialized"
    assert pg_close["source_provisional_qd"]["formal_pg_new"] is False
    assert pg_close["source_provisional_qd"]["future_priority_only"] is True
    assert pg_close["final_qd"]["status"] == "formal_pg_new_materialized"
    assert pg_close["final_qd"]["formal_pg_new"] is True
    assert pg_close["final_qd"]["future_priority_only"] is False
    assert pg_close["final_qd"]["quality_eligible_count"] == 8
    assert pg_close["final_qd"]["new_cell_count"] == 8
    assert pg_close["final_qd"]["replacement_count"] == 0
    assert pg_close["final_qd"]["materialized_contribution_counts"] == {
        "new_cell": 8,
        "replacement": 0,
    }
    assert qd_evidence["status"] == "readback_verified"
    assert qd_evidence["qd_evaluation_count"] == 24
    assert qd_evidence["candidate_mutations"] == 0
    assert qd_evidence["historical_runs_modified"] is False
    assert pg_close["persistence"]["authoritative_candidate_count"] > 0
    assert not (
        pg_close["final_qd"]["formal_pg_new"] is False
        or pg_close["final_qd"]["future_priority_only"] is True
    )
