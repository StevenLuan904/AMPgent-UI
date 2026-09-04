import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

from build_targeted_rosetta_coarse5_backlog_vnext import challenger_passes  # noqa: E402, I001
from close_fgf2_pepflow_qd_neighbor_vnext import _identity  # noqa: E402, I001


REPORT = ROOT / "reports" / "acea_pepflow_qd_neighbor_vnext_20260904_generation4_v2"
BACKLOG = (
    ROOT
    / "reports"
    / "targeted_rosetta_coarse5_backlog_20260904"
    / "vnext_fgf2_acea_20260904"
)


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_acea_outputs_preserve_target_conditioned_twelve_row_identity() -> None:
    proposals = _csv(REPORT / "proposals.csv")
    score = _csv(REPORT / "score_all" / "candidate_scores.csv")
    calibrated = _csv(REPORT / "candidate_scores_calibrated.csv")
    challenger = _csv(REPORT / "challenger" / "challenger_review.csv")
    generation = _json(REPORT / "generation_receipt.json")
    score_receipt = _json(REPORT / "score_all" / "receipt.json")

    assert len(proposals) == 12
    assert {row["target_key"] for row in proposals} == {"acea"}
    assert generation["identity_contract"] == {
        "sequence_sha256_unique": True,
        "sequence_order_preserved": True,
        "target_key": "acea",
        "output_dir_is_independent": True,
    }
    _identity(score, proposals, "AceA score-all")
    _identity(calibrated, proposals, "AceA calibration")
    _identity(challenger, proposals, "AceA challenger")
    assert score_receipt["identity_contract"]["sequence_identity_verified"] is True
    assert score_receipt["identity_contract"]["sequence_order_preserved"] is True


def test_acea_close_records_formal_pg_new_qd_and_prepared_only_queue() -> None:
    close = _json(REPORT / "close_receipt.json")
    queue = _json(REPORT / "coarse5_prepared" / "coarse5_prepared_receipt.json")
    backlog = _json(BACKLOG / "targeted_rosetta_coarse5_backlog_vnext.json")

    assert close["stage_counts"] == {
        "proposal": 12,
        "formal12": 12,
        "display": 12,
        "support_ge_2": 7,
        "challenger_reviewed": 12,
        "challenger_no_conflict": 12,
        "challenger_conflict": 0,
    }
    assert close["final_qd"] == {
        "status": "formal_pg_new_materialized",
        "quality_eligible_count": 2,
        "new_cell_count": 2,
        "replacement_count": 0,
        "materialized_contribution_count": 2,
        "formal_pg_new": True,
        "future_priority_only": False,
    }
    assert close["persistence"]["exact_binding"] is True
    assert close["persistence"]["evaluation_count"] == 34
    assert close["persistence"]["readback"]["drift"] == 0
    assert close["persistence"]["replay_validation"]["status"] == "readback_noop_verified"
    assert queue["candidate_count"] == queue["task_key_count"] == 2
    assert queue["nstruct"] == 5
    assert queue["dispatch_allowed"] is False
    assert queue["remote_dispatch_submitted"] is False
    assert backlog["summary"]["rows"] == 50
    assert backlog["summary"]["target_counts"]["acea"] == 4
    assert backlog["summary"]["target_counts"]["fgf2"] == 6
    backlog_rows = _csv(BACKLOG / "targeted_rosetta_coarse5_backlog_vnext.csv")
    assert not any(
        row["target_key"] == "acea" and row["source_generation"] == "4"
        for row in backlog_rows
    )
    assert sum(
        row["target_key"] == "fgf2" and row["source_generation"] == "5"
        for row in backlog_rows
    ) == 3
    assert backlog["dispatch_allowed"] is False
    assert backlog["requires_remote_exact_preflight"] is True


def test_acea_identity_contract_rejects_sequence_order_drift() -> None:
    expected = [
        {"sequence": "AAAA", "sequence_sha256": "a"},
        {"sequence": "CCCC", "sequence_sha256": "b"},
    ]
    with pytest.raises(ValueError, match="sequence identity/order drifted"):
        _identity(list(reversed(expected)), expected, "AceA fixture")


def test_retained_challenger_disagreement_remains_complete_evidence() -> None:
    row = {
        "hemopi2_classification_label": "0",
        "validator_version": "HemoPI2-test",
        "hemopi2_classification_score": "0.3",
        "hemopi2_hc50_um": "160",
        "challenger_conflict_status": "cross_model_disagreement_retained",
    }
    assert challenger_passes(row) is False
    assert challenger_passes(row, allow_retained_conflict=True) is True


def test_acea_current_pg_unavailable_is_proposal_only_and_has_no_uuid() -> None:
    eligibility = _json(REPORT / "targeted_backlog_eligibility.json")
    assert eligibility["effective_status"] == "proposal_only_pending"
    assert eligibility["authoritative_candidate_ids"] == []
    assert eligibility["postgresql_status"] == "unavailable"
