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

    assert len(proposals) == 12
    assert {row["historical_pg_gate"] for row in proposals} == {"pending"}
    assert {row["materialization_status"] for row in proposals} == {
        "proposed_not_materialized"
    }
    assert score_receipt["formal_12_complete_count"] == 12
    assert score_receipt["display_eligible_count"] == 11
    assert calibration["support_ge_2_count"] == 9
    assert challenger["challenger_status"] == "runtime_unavailable"
    assert challenger["reviewed_candidate_count"] == 0
    assert qd["status"] == "provisional_only"
    assert qd["provisional_new_cell_count"] == 8
    assert qd["provisional_replacement_count"] == 0
    assert close["persistence"]["pool_a_admitted"] is False
    assert close["persistence"]["postgresql_writes"] == 0
    assert close["provisional_qd"]["formal_pg_new"] is False
