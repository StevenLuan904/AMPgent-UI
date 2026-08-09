from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from pepagent import safety_concordance_v28 as audit

ROOT = Path(__file__).parents[1]


def test_v28_is_preregistered_and_not_authorized() -> None:
    manifest = yaml.safe_load(
        (ROOT / "config/benchmarks/amp_designer_safety_concordance_v28.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["execution_status"] == "preregistered_not_run"
    assert manifest["selection_ranking_and_promotion_forbidden"] is True
    assert manifest["output"]["candidate_level_rows_forbidden"] is True
    with pytest.raises(RuntimeError, match="not authorized"):
        audit.main()


def test_join_is_exact_and_preserves_v25_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "ROW_COUNT", 2)
    first = "ACDE"
    second = "KKLL"
    rows = [
        {
            "candidate_id": "a",
            "sequence": first,
            "sequence_sha256": hashlib.sha256(first.encode()).hexdigest(),
        },
        {
            "candidate_id": "b",
            "sequence": second,
            "sequence_sha256": hashlib.sha256(second.encode()).hexdigest(),
        },
    ]
    joined = audit.join_frozen_rows(rows, list(reversed(rows)))
    assert [left["candidate_id"] for left, _ in joined] == ["a", "b"]
    assert [right["candidate_id"] for _, right in joined] == ["a", "b"]


def test_join_rejects_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "ROW_COUNT", 1)
    left = [
        {
            "candidate_id": "a",
            "sequence": "ACD",
            "sequence_sha256": hashlib.sha256(b"ACD").hexdigest(),
        }
    ]
    right = [
        {
            "candidate_id": "b",
            "sequence": "ACD",
            "sequence_sha256": hashlib.sha256(b"ACD").hexdigest(),
        }
    ]
    with pytest.raises(ValueError, match="key sets differ"):
        audit.join_frozen_rows(left, right)


def test_spearman_uses_average_ties() -> None:
    assert audit.spearman_rho([1.0, 2.0, 2.0, 4.0], [10.0, 20.0, 20.0, 40.0]) == pytest.approx(1.0)
    assert audit.spearman_rho([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)


def test_unknown_labels_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "ROW_COUNT", 1)
    v25 = {
        "macrel_hemolysis_label": "unknown",
        "toxinpred3_label": "Non-Toxin",
        "macrel_hemolysis_probability": "0.1",
        "toxinpred3_hybrid_score": "0.1",
    }
    v27 = {"hemopi2_classification_score": "0.1", "hemopi2_hc50_um": "200"}
    with pytest.raises(ValueError, match="unknown label"):
        audit.audit_rows([(v25, v27)])
