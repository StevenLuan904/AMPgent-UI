from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.build_source_effect_benchmark import build_benchmark


def test_v3_addition_is_one_run_scoped_gyrA_pepflow_record() -> None:
    root = Path(__file__).resolve().parents[1]
    base = json.loads(
        (root / "reports/source_effect_benchmark_20260904/config_v2.json").read_text(
            encoding="utf-8"
        )
    )
    addition = json.loads(
        (root / "reports/source_effect_benchmark_20260904/config_v3_gyrA.json").read_text(
            encoding="utf-8"
        )
    )
    result = build_benchmark(
        {**base, "specs": [*base["specs"], *addition["specs"]]}, root
    )
    records = [
        item
        for item in result["records"]
        if item["run_id"] == "e405720e-7087-5765-9dde-6148aee0c7aa"
    ]
    assert len(records) == 1
    record = records[0]
    assert record["source"] == "PepFlow"
    assert record["target_key"] == "gyra"
    assert record["counts"]["proposal"] == 12
    assert record["counts"]["materialized"] == 8
    assert record["counts"]["formal12"] == 12
    assert record["counts"]["display"] == 11
    assert record["counts"]["activity_support_ge_2"] == 9
    assert record["materialized_cohort"] == {
        "candidate_count": 8,
        "formal12": 8,
        "display": 8,
        "activity_support_ge_2": 8,
        "excellent": 8,
        "challenger_reviewed": 8,
        "challenger_no_conflict": 8,
        "qd_eligible": 8,
        "qd_new_cell": 8,
        "qd_replacement": 0,
    }
    assert record["counts"]["qd_new_cell"] == 8
    assert record["counts"]["qd_replacement"] == 0
    assert record["counts"]["pool_a_admitted"] == 0
    for rate_name, rate in record["rates"].items():
        if rate["rate"] is not None:
            assert 0 <= rate["rate"] <= 1, rate_name
        if rate["denominator"]:
            assert rate["count"] <= rate["denominator"], rate_name
    assert record["rates"]["materialized_to_formal12"]["denominator"] == 8
    assert record["rates"]["materialized_to_display"]["denominator"] == 8
    assert record["rates"]["materialized_to_activity_support_ge_2"]["denominator"] == 8
    assert record["rates"]["materialized_to_qd_eligible"]["denominator"] == 8
    material = json.loads(
        (root / "reports/gyrA_pepflow_qd_neighbor_20260904/materialization_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert material["materialized_or_reused_in_run_count"] == 8
    assert material["inserted_evaluation_count"] == 136
