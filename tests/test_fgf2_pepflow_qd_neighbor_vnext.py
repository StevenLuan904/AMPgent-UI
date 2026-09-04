import csv
import json
import sys
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_text

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

from close_fgf2_pepflow_qd_neighbor_vnext import _identity  # noqa: E402, I001


REPORT = ROOT / "reports" / "fgf2_pepflow_qd_neighbor_vnext_20260904_generation5_v4"


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_vnext_outputs_preserve_single_twelve_row_identity() -> None:
    proposals = _csv(REPORT / "proposals.csv")
    score = _csv(REPORT / "score_all" / "candidate_scores.csv")
    calibrated = _csv(REPORT / "candidate_scores_calibrated.csv")
    challenger = _csv(REPORT / "challenger" / "challenger_review.csv")
    assert len(proposals) == 12
    _identity(score, proposals, "score-all")
    _identity(calibrated, proposals, "calibration")
    _identity(challenger, proposals, "challenger")


def test_close_requires_authoritative_final_qd_and_prepared_only_queue() -> None:
    close = json.loads((REPORT / "close_receipt.json").read_text(encoding="utf-8"))
    queue = json.loads(
        (REPORT / "coarse5_prepared" / "coarse5_prepared_receipt.json").read_text(encoding="utf-8")
    )
    assert close["final_qd"] == {
        "status": "formal_pg_new_materialized",
        "quality_eligible_count": 3,
        "new_cell_count": 3,
        "replacement_count": 0,
        "materialized_contribution_count": 3,
        "formal_pg_new": True,
        "future_priority_only": False,
    }
    assert close["persistence"]["evaluation_count"] == 51
    assert close["persistence"]["readback"]["drift"] == 0
    assert close["persistence"]["replay_validation"] == {
        "status": "readback_noop_verified",
        "receipt_sha256": close["persistence"]["replay_validation"]["receipt_sha256"],
        "inserted_evaluation_count": 0,
        "drift": 0,
    }
    assert queue["candidate_count"] == queue["task_key_count"] == 3
    assert queue["dispatch_allowed"] is False
    assert queue["remote_dispatch_submitted"] is False


def test_identity_contract_rejects_sequence_order_drift() -> None:
    expected = [
        {"sequence": "AAAA", "sequence_sha256": sha256_text("AAAA")},
        {"sequence": "CCCC", "sequence_sha256": sha256_text("CCCC")},
    ]
    actual = list(reversed(expected))
    with pytest.raises(ValueError, match="sequence identity/order drifted"):
        _identity(actual, expected, "fixture")
