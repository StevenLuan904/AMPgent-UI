from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module():
    analysis_dir = Path(__file__).resolve().parents[1] / "analysis"
    sys.path.insert(0, str(analysis_dir))
    spec = importlib.util.spec_from_file_location(
        "_autoresearch_lineage_close",
        analysis_dir / "autoresearch_lineage_close.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_close_accepts_previous_update_as_next_snapshot() -> None:
    module = _load_module()
    snapshot = {"schema_version": "ampgent.autoresearch-archive-snapshot.1"}

    assert module._archive_snapshot_payload(snapshot) is snapshot
    assert module._archive_snapshot_payload({"current": snapshot}) is snapshot


def test_lineage_close_selects_only_full_scored_plan_actions() -> None:
    module = _load_module()
    plan = {
        "actions": [
            {"action_sha256": "a" * 64},
            {"action_sha256": "b" * 64},
            {"action_sha256": "c" * 64},
        ]
    }

    selected, skipped = module._full_scored_action_payloads(plan, {"b" * 64})

    assert selected == [{"action_sha256": "b" * 64}]
    assert skipped == 2


def test_lineage_close_accepts_a_branch_subset_from_mixed_archive() -> None:
    module = _load_module()

    ignored = module._validate_archive_branches(
        {"gyra", "pbp2a", "vegfa"}, {"gyra"}
    )

    assert ignored == ("pbp2a", "vegfa")


def test_lineage_close_rejects_archive_missing_child_branch() -> None:
    module = _load_module()

    with pytest.raises(
        ValueError, match="lineage close archive is missing child branches: gyra"
    ):
        module._validate_archive_branches({"pbp2a"}, {"gyra"})


def test_lineage_close_deduplicates_identical_sequence_evidence() -> None:
    module = _load_module()
    base = {
        "sequence_sha256": "a" * 64,
        "branch_key": "acea",
        "sequence": "KKKK",
        "family_key_80_80": "family-a",
        "activity_model_support_count_calibrated": "2",
        "generation": "1",
        **{metric_name: "1.0" for metric_name in module.FORMAL_METRICS},
    }
    newer = {
        **base,
        "activity_model_support_count_calibrated": "3",
        "generation": "2",
    }

    selected = module._deduplicate_rows_by_sequence(
        [base, newer], label="parent"
    )

    assert selected == [newer]


def test_lineage_close_rejects_drifted_duplicate_sequence_evidence() -> None:
    module = _load_module()
    base = {
        "sequence_sha256": "a" * 64,
        "branch_key": "acea",
        "sequence": "KKKK",
        "family_key_80_80": "family-a",
        **{metric_name: "1.0" for metric_name in module.FORMAL_METRICS},
    }
    drifted = {**base, module.FORMAL_METRICS[0]: "2.0"}

    with pytest.raises(ValueError, match="duplicate sequence evidence drifted"):
        module._deduplicate_rows_by_sequence(
            [base, drifted], label="parent"
        )


def test_challenger_status_counts_keep_conflicts_as_reviewed_front() -> None:
    module = _load_module()
    rows = [
        {
            "sequence_sha256": "a" * 64,
            "challenger_conflict_status": "no_conflict",
        },
        {
            "sequence_sha256": "b" * 64,
            "challenger_conflict_status": "cross_model_disagreement_retained",
        },
    ]

    reviewed, no_conflict, retained_conflict = module._challenger_status_hashes(rows)

    assert reviewed == {"a" * 64, "b" * 64}
    assert no_conflict == {"a" * 64}
    assert retained_conflict == {"b" * 64}


def test_challenger_status_counts_fail_closed_on_unknown_status() -> None:
    module = _load_module()

    with pytest.raises(ValueError, match="challenger status is invalid"):
        module._challenger_status_hashes(
            [{"sequence_sha256": "a" * 64, "challenger_conflict_status": ""}]
        )
