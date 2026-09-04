import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_MODULE_PATH = (
    Path(__file__).parents[1] / "analysis" / "reconcile_pool_a_md_identity_sets.py"
)
_SPEC = spec_from_file_location("reconcile_pool_a_md_identity_sets", _MODULE_PATH)
assert _SPEC and _SPEC.loader
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
difference = _MODULE.difference
partition = _MODULE.partition


def test_difference_reports_one_exact_identity_without_sequence_merging() -> None:
    previous = {("run-a", "candidate-a"), ("run-b", "candidate-b")}
    current = {("run-a", "candidate-a")}
    assert difference(previous, current) == [("run-b", "candidate-b")]
    assert difference(current, previous) == []


def test_partition_is_mutually_exclusive_and_covers_expected() -> None:
    expected = {("run-a", "a"), ("run-a", "b"), ("run-b", "c")}
    result = partition(expected, {("run-a", "a")}, {("run-a", "b")})
    assert result["launched_unique"] == 2
    assert result["not_started_unique"] == 1
    assert result["partition_total_unique"] == 3
    assert result["launched_equals_complete_plus_running"] is True
    assert result["disjoint_union_equals_expected"] is True


def test_partition_rejects_overlap_and_out_of_cohort_identity() -> None:
    with pytest.raises(ValueError, match="overlap"):
        partition({("run-a", "a")}, {("run-a", "a")}, {("run-a", "a")})
    with pytest.raises(ValueError, match="outside"):
        partition({("run-a", "a")}, {("run-b", "b")}, set())


def test_current_snapshot_has_exact_five_new_identities_and_partition_invariants() -> None:
    snapshot = (
        Path(__file__).parents[1]
        / "reports/pool_a_md_50ns_expansion_20260903/md_current_identity_snapshot_20260904.json"
    )
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    counts = payload["current_partition"]
    assert counts["launched_unique"] == 50
    assert counts["full_evidence_unique"] == 36
    assert counts["analysis_pending_unique"] == 2
    assert counts["running_or_incomplete_unique"] == 12
    assert counts["not_started_unique"] == 436
    assert counts["failed_unique"] == 0
    assert counts["partition_total_unique"] == 486
    assert counts["launched_equals_full_plus_running"] is False
    assert counts["launched_unique"] == (
        counts["full_evidence_unique"]
        + counts["analysis_pending_unique"]
        + counts["running_or_incomplete_unique"]
    )
    assert counts["disjoint_union_equals_486"] is True
    new = payload["new_identities_since_previous_45"]
    assert payload["new_identity_count"] == 5
    assert len({(row["run_id"], row["candidate_id"]) for row in new}) == 5


def test_fgf2_transition_receipts_are_pg_complete() -> None:
    report_dir = (
        Path(__file__).parents[1]
        / "reports/pool_a_md_50ns_expansion_20260903"
    )
    complete = json.loads(
        (report_dir / "md_transition_verification_a32aadd5_20260904.json").read_text(
            encoding="utf-8"
        )
    )
    incomplete = json.loads(
        (report_dir / "md_transition_verification_bf53367f_20260904.json").read_text(
            encoding="utf-8"
        )
    )
    assert complete["pg"]["md_evaluation_count"] == 15
    assert complete["pg"]["interface_evaluation_count"] == 11
    assert complete["pg"]["mmgbsa_evaluation_count"] == 4
    assert complete["pg"]["full_md_coverage"] is True
    assert len(complete["pg"]["successful_md_tool_call_ids"]) == 2
    assert incomplete["pg"]["md_evaluation_count"] == 15
    assert incomplete["pg"]["interface_evaluation_count"] == 11
    assert incomplete["pg"]["mmgbsa_evaluation_count"] == 4
    assert incomplete["pg"]["full_md_coverage"] is True
    assert len(incomplete["pg"]["successful_md_tool_call_ids"]) == 2
    assert incomplete["decision"]["ingest_status"] == "inserted_exact_once"
    snapshot = json.loads(
        (report_dir / "md_current_identity_snapshot_20260904.json").read_text(
            encoding="utf-8"
        )
    )
    audit = snapshot["postgresql_evidence_audit"]
    assert audit["pg_complete_full_evidence_unique"] == 36
    assert audit["transition_pg_complete"] + audit["transition_pg_incomplete"] == 7
    assert audit["transition_pg_incomplete"] == 0
