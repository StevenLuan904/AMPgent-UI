import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
RECEIPT = ROOT / (
    "reports/pool_a_md_50ns_expansion_20260903/"
    "md_remote_compact_transition_20260904.json"
)


def test_transition_records_exact_five_and_replay_only() -> None:
    payload = json.loads(RECEIPT.read_text(encoding="utf-8"))
    rows = payload["candidates"]
    assert len(rows) == 5
    assert len({(row["run_id"], row["candidate_id"]) for row in rows}) == 5
    assert all(row["pg_evaluation_count"] == 15 for row in rows)
    assert payload["pg_reconciliation"]["actual_inserted_evaluations"] == 0
    assert payload["pg_reconciliation"]["preexisting_replayed_evaluations"] == 75


def test_pending_mmgbsa_only_is_in_disjoint_486_partition() -> None:
    payload = json.loads(RECEIPT.read_text(encoding="utf-8"))
    after = payload["snapshot_transition"]["after"]
    partition_names = (
        "full_evidence",
        "analysis_pending",
        "running_or_incomplete",
        "not_started",
        "failed",
    )
    assert sum(after[name] for name in partition_names) == 486
    assert after["launched"] == (
        after["full_evidence"]
        + after["analysis_pending"]
        + after["running_or_incomplete"]
    )
    assert payload["snapshot_transition"]["launched_equals_full_plus_running"] is False
