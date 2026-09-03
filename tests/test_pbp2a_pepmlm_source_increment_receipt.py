from __future__ import annotations

import json
from pathlib import Path


def test_pbp2a_pepmlm_shadow_status_preserves_runtime_contract():
    root = Path(__file__).parents[1]
    receipt = json.loads(
        (
            root
            / "reports"
            / "pbp2a_pepmlm_source_increment_20260904"
            / "close_receipt.json"
        ).read_text(encoding="utf-8")
    )
    quality = receipt["quality"]
    assert quality["apex_status"] == "runtime_unavailable"
    assert quality["peptiverse_status"] == "runtime_unavailable"
    assert quality["apex_conflict_status"] == "not_assessed"
    assert quality["peptiverse_conflict_status"] == "not_assessed"
