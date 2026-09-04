import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
REPORT = ROOT / "reports/angpt1_pepmlm_archive_increment_20260904"


def test_shadow_limitation_is_separate_from_lineage_gate() -> None:
    receipt = json.loads((REPORT / "close_receipt.json").read_text(encoding="utf-8"))
    gates = receipt["gates"]
    assert gates["primary_display_gate_passed"] is True
    assert gates["challenger_runtime_complete"] is False
    assert gates["lineage_close_allowed"] is True
    assert gates["apex"] == "runtime_unavailable/not_assessed"
    assert gates["peptiverse"] == "runtime_unavailable/not_assessed"


def test_future_named_source_is_historical_promotion() -> None:
    receipt = json.loads((REPORT / "source_receipt.json").read_text(encoding="utf-8"))
    assert receipt["source_artifact_classification"] == (
        "historical_artifact_promoted_not_generated_this_round"
    )
    assert receipt["source_date_valid_for_current_round"] is False
