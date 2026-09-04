from __future__ import annotations

import json
from pathlib import Path

from analysis.build_source_effect_benchmark import build_benchmark


def test_v5_records_nonmaterialized_vegfa_pepflow_without_fabricating_pg() -> None:
    root = Path(__file__).resolve().parents[1]
    base = json.loads(
        (root / "reports/source_effect_benchmark_20260904/config_v2.json").read_text(
            encoding="utf-8"
        )
    )
    addition = json.loads(
        (root / "reports/source_effect_benchmark_20260904/config_v5_additions.json").read_text(
            encoding="utf-8"
        )
    )
    result = build_benchmark(
        {**base, "specs": [*base["specs"], *addition["specs"]]}, root
    )
    record = result["records"][-1]
    assert record["target_key"] == "vegfa"
    assert record["source"] == "PepFlow"
    assert record["counts"] == {
        **record["counts"],
        "proposal": 6,
        "materialized": 0,
        "formal12": 6,
        "display": 3,
        "activity_support_ge_2": 0,
        "qd_eligible": 0,
    }
    assert record["pg_identity"]["materialized_candidate_count"] == 0
    assert record["pg_identity"]["tool_call_id"] is None
    assert result["coverage_matrix"]["cells"]["PepFlow:vegfa"]["status"] == "recorded"
    assert all(
        0 <= rate["rate"] <= 1
        for rate in record["rates"].values()
        if rate["rate"] is not None
    )
