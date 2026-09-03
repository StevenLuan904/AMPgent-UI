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
