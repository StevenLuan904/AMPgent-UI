from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "analysis"
        / "persist_autoresearch_rescue_evidence.py"
    )
    spec = importlib.util.spec_from_file_location("_persist_rescue_evidence", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(*, excellent: bool) -> dict[str, str]:
    return {
        "sequence": "KKLLKKLLKK",
        "sequence_sha256": "a" * 64,
        "formal_12_complete": "true",
        "display_eligible": "true",
        "activity_model_support_count_calibrated": "2" if excellent else "1",
        "family_key_80_80": "family-a",
        "diversity_qualified": "true",
        "excellent_sequence_stage_calibrated": str(excellent).lower(),
    }


def test_candidate_payload_preserves_non_excellent_score_all_history() -> None:
    module = _load_module()

    payload = module._candidate_payload(
        _row(excellent=False),
        excellent_challenger_status="reviewed_no_conflict",
    )

    assert payload["sequence_sha256"] == "a" * 64
    assert payload["excellent_sequence_stage"] == "false"
    assert payload["challenger_status"] == "not_reviewed_not_excellent_sequence_stage"


def test_candidate_payload_keeps_challenger_status_for_excellent_rows() -> None:
    module = _load_module()

    payload = module._candidate_payload(
        _row(excellent=True),
        excellent_challenger_status="reviewed_no_conflict",
    )

    assert payload["excellent_sequence_stage"] == "true"
    assert payload["challenger_status"] == "reviewed_no_conflict"
