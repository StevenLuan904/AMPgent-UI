import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
RECEIPT = (
    ROOT
    / "reports"
    / "gyrA_pepflow_qd_neighbor_20260904"
    / "continuation_selection_receipt.json"
)


def test_selected_source_batch_has_highest_local_qd_contribution() -> None:
    payload = json.loads(RECEIPT.read_text(encoding="utf-8"))
    comparison = payload["comparison"]
    selected = comparison[payload["selected_batch"]]
    assert selected["qd_new_cell"] == 8
    assert selected["qd_new_cell"] > comparison[
        "reports/gyrA_pepflow_generation5_20260904"
    ]["qd_new_cell"]
    assert selected["qd_new_cell"] > comparison[
        "reports/pbp2a_pepflow_same_domain_20260904"
    ]["qd_new_cell"]
    assert payload["selected_evidence"]["qd"]["cell_count"] == 2160


def test_pg_timeout_is_fail_closed_and_does_not_claim_writes() -> None:
    payload = json.loads(RECEIPT.read_text(encoding="utf-8"))
    postgres = payload["postgresql"]
    assert postgres["status"] == "evidence_verification_unavailable"
    assert postgres["readback_phase"] == "candidate_identity"
    assert postgres["experiment_run_status_verified"] == "succeeded"
    assert postgres["error_category"] == "statement_timeout"
    assert postgres["identity_binding_verified"] is False
    assert postgres["writes_attempted"] == 0
    assert payload["selected_evidence"]["score_all"][
        "hydrophobic_run_or_fraction_hard_gate"
    ] is False
