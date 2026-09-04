import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_v9_inventory_closes_matrix_without_fabricating_a_run() -> None:
    payload = json.loads(
        (ROOT / "reports/source_effect_benchmark_20260904/benchmark_v9.json").read_text(
            encoding="utf-8"
        )
    )
    records = {
        (row["source"], row["target_key"]): row for row in payload["records"]
    }
    assert len(payload["records"]) == 25
    assert records["PepGLAD", "acea"]["run_id"]
    assert records["PepFlow", "angpt1"]["run_id"]
    vegfa = records["PepGLAD", "vegfa"]
    assert vegfa["run_id"] is None
    assert vegfa["source_scope"] == "artifact"
    assert vegfa["evidence_strength"] == "artifact_scoped_nonmaterialized"
    assert payload["next_operator"]["status"] == "inventory_closed_no_unresolved_matrix_gap"
    assert payload["selection_exclusions"]
