import json
from pathlib import Path


def test_pg_new_preflight_never_promotes_invalid_source_rows() -> None:
    path = (
        Path(__file__).parents[1]
        / "reports"
        / "pepflow_pepglad_pg_new_preflight_20260904"
        / "receipt.json"
    )
    receipt = json.loads(path.read_text(encoding="utf-8"))

    assert receipt["decision"] == "no_materialization"
    assert receipt["materialization"] == {
        "candidate_count": 0,
        "evaluation_count": 0,
        "coarse5_prepared_count": 0,
        "reason": "no PG-new candidate satisfies all existing score/challenger/QD gates",
    }
    assert all(row["formal12"] == row["score_rows"] for row in receipt["exact_audit"])
    assert receipt["exact_audit"][2]["pg_new_qd_valid"] == 0
    assert receipt["exact_audit"][3]["support_ge_2"] == 0
    assert receipt["exact_audit"][4]["support_ge_2"] == 0
    assert receipt["gate_semantics"]["apex"] == "runtime_unavailable/not_assessed"
    assert receipt["gate_semantics"]["peptiverse"] == "runtime_unavailable/not_assessed"
