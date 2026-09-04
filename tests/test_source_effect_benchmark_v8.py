import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _benchmark() -> dict:
    return json.loads(
        (ROOT / "reports/source_effect_benchmark_20260904/benchmark_v8.json").read_text(
            encoding="utf-8"
        )
    )


def test_fgf2_pepglad_authoritative_gap_is_recorded():
    benchmark = _benchmark()
    records = [
        record
        for record in benchmark["records"]
        if record["target_key"] == "fgf2" and record["source"] == "PepGLAD"
    ]
    assert len(records) == 1
    record = records[0]
    assert record["run_id"] == "2b8f5096-5e59-5e27-a53d-692adadebd82"
    assert record["counts"] == {
        **record["counts"],
        "proposal": 12,
        "materialized": 3,
        "formal12": 12,
        "display": 12,
        "activity_support_ge_2": 12,
        "excellent": 12,
        "challenger_reviewed": 12,
        "challenger_no_conflict": 12,
        "qd_eligible": 12,
        "qd_new_cell": 1,
        "qd_replacement": 2,
    }
    assert record["materialized_cohort"] == {
        **record["materialized_cohort"],
        "candidate_count": 3,
        "formal12": 3,
        "display": 3,
        "activity_support_ge_2": 3,
        "excellent": 3,
        "challenger_reviewed": 3,
        "challenger_no_conflict": 3,
        "qd_eligible": 3,
        "qd_new_cell": 1,
        "qd_replacement": 2,
    }


def test_fgf2_pepglad_matrix_cell_is_no_longer_missing():
    cell = _benchmark()["coverage_matrix"]["cells"]["PepGLAD:fgf2"]
    assert cell["status"] == "recorded"
    assert cell["run_ids"] == ["2b8f5096-5e59-5e27-a53d-692adadebd82"]
    assert cell["materialized_count"] == 3
    assert cell["qd_eligible_count"] == 12
