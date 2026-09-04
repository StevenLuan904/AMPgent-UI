from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.build_source_effect_benchmark import build_benchmark


def test_v4_keeps_new_runs_run_scoped_and_populates_matrix() -> None:
    root = Path(__file__).resolve().parents[1]
    base = json.loads(
        (root / "reports/source_effect_benchmark_20260904/config_v2.json").read_text(
            encoding="utf-8"
        )
    )
    addition = json.loads(
        (
            root / "reports/source_effect_benchmark_20260904/config_v4_additions.json"
        ).read_text(encoding="utf-8")
    )
    result = build_benchmark(
        {**base, "specs": [*base["specs"], *addition["specs"]]}, root
    )
    by_run = {record["run_id"]: record for record in result["records"]}
    vegfa = by_run["d710662b-a74c-50ec-b152-5ea4d72903d3"]
    gyra = by_run["e42e5097-aa1b-55ab-a191-93a923d7f51a"]
    assert (vegfa["counts"]["proposal"], vegfa["counts"]["materialized"]) == (8, 1)
    assert (vegfa["counts"]["qd_new_cell"], vegfa["counts"]["qd_replacement"]) == (1, 0)
    assert vegfa["materialized_cohort"]["challenger_no_conflict"] == 1
    assert (gyra["counts"]["proposal"], gyra["counts"]["materialized"]) == (12, 3)
    assert (gyra["counts"]["qd_new_cell"], gyra["counts"]["qd_replacement"]) == (2, 1)
    assert gyra["materialized_cohort"]["challenger_no_conflict"] == 3
    assert result["coverage_matrix"]["cells"]["PepMLM:vegfa"]["record_count"] == 2
    assert result["coverage_matrix"]["cells"]["PepGLAD:gyra"]["run_ids"] == [
        "e42e5097-aa1b-55ab-a191-93a923d7f51a"
    ]
    assert (
        result["coverage_matrix"]["cells"]["PepFlow:angpt1"]["status"]
        == "benchmark_not_recorded"
    )
    assert "not evidence that" in result["coverage_matrix"]["interpretation"]
