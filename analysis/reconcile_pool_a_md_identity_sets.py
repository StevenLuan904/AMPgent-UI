"""Exact identity diffs and mutually exclusive Pool-A MD partitions."""

from __future__ import annotations

from collections.abc import Iterable

Identity = tuple[str, str]


def identity(row: dict[str, object]) -> Identity:
    """Return the PostgreSQL subject-run/candidate identity."""

    return (str(row["run_id"]), str(row["candidate_id"]))


def identity_set(rows: Iterable[dict[str, object]]) -> set[Identity]:
    """Build an exact identity set without merging by sequence or target."""

    return {identity(row) for row in rows}


def difference(left: Iterable[Identity], right: Iterable[Identity]) -> list[Identity]:
    """Return a deterministic exact set difference."""

    return sorted(set(left) - set(right))


def partition(
    expected: Iterable[Identity],
    complete: Iterable[Identity],
    running: Iterable[Identity],
    failed: Iterable[Identity] = (),
) -> dict[str, object]:
    """Validate and summarize a complete MD state partition.

    ``complete``, ``running``, and ``failed`` are disjoint terminal/live states;
    ``not_started`` is the exact complement in the authoritative 486 cohort.
    """

    expected_set = set(expected)
    complete_set = set(complete)
    running_set = set(running)
    failed_set = set(failed)
    states = (complete_set, running_set, failed_set)
    if any(left & right for index, left in enumerate(states) for right in states[index + 1 :]):
        raise ValueError("MD state sets overlap")
    if not all(state <= expected_set for state in states):
        raise ValueError("MD state contains an identity outside the authoritative cohort")

    launched = complete_set | running_set | failed_set
    not_started = expected_set - launched
    if launched | not_started != expected_set:
        raise ValueError("MD state partition does not cover the expected cohort")
    return {
        "launched_unique": len(launched),
        "md_complete_unique": len(complete_set),
        "running_unique": len(running_set),
        "failed_unique": len(failed_set),
        "not_started_unique": len(not_started),
        "partition_total_unique": len(launched | not_started),
        "launched_equals_complete_plus_running": not failed_set
        and launched == complete_set | running_set,
        "disjoint_union_equals_expected": len(launched | not_started) == len(expected_set),
        "not_started": sorted(not_started),
    }
