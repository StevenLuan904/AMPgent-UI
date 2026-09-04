from __future__ import annotations

import json
from pathlib import Path

from analysis.build_source_effect_benchmark import build_benchmark


def test_v6_includes_exact_angpt1_pepmlm_materialized_run() -> None:
    root = Path(__file__).resolve().parents[1]
    base = json.loads(
        (root / "reports/source_effect_benchmark_20260904/config_v2.json").read_text(
            encoding="utf-8"
        )
    )
    additions = json.loads(
        (root / "reports/source_effect_benchmark_20260904/config_v6_additions.json").read_text(
            encoding="utf-8"
        )
    )
    result = build_benchmark(
        {**base, "specs": [*base["specs"], *additions["specs"]]}, root
    )
    record = next(
        item
        for item in result["records"]
        if item["target_key"] == "angpt1" and item["source"] == "PepMLM"
    )
    assert record["run_id"] == "f54f47a8-f356-53c2-8488-57714683f275"
    assert record["source_scope"] == "run"
    assert record["counts"]["proposal"] == 1
    assert record["counts"]["materialized"] == 1
    assert record["counts"]["formal12"] == 1
    assert record["counts"]["display"] == 1
    assert record["counts"]["activity_support_ge_2"] == 1
    assert record["counts"]["challenger_reviewed"] == 1
    assert record["counts"]["qd_eligible"] == 1
    assert record["counts"]["qd_new_cell"] == 1
    assert record["pg_identity"]["run_id"] == (
        "f54f47a8-f356-53c2-8488-57714683f275"
    )
    assert record["pg_identity"]["tool_call_id"] == (
        "4213295d-9cdb-4e0b-8c29-4b372313b7fb"
    )
    assert record["pg_identity"]["materialized_candidate_count"] == 1
    assert record["pg_identity"]["inserted_evaluation_count"] == 17
    close = json.loads(
        (
            root
            / "reports/angpt1_pepmlm_archive_increment_20260904/close_receipt.json"
        ).read_text(encoding="utf-8")
    )
    assert close["run_id"] == record["run_id"]
    assert close["candidate_id"] == (
        "63d347e3-c352-4f3f-8649-0367aeae17fa"
    )
    assert close["counts"]["evaluations"] == 17
    assert result["coverage_matrix"]["cells"]["PepMLM:angpt1"]["materialized_count"] == 1
