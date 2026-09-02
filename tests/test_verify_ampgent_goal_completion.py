import json
from pathlib import Path

from analysis.report_pool_a_live import TARGETS, summarize
from analysis.verify_ampgent_goal_completion import verify
from tests.test_verify_pool_a_completion import candidate


def test_verifies_closed_loop_and_remote_authority(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    for target in TARGETS:
        plan = reports / f"autoresearch_lineage_round1_{target}_test"
        plan.mkdir()
        (plan / "plans.json").write_text(
            json.dumps(
                {"actions": [{"action_type": action} for action in (
                    "masked_substitution",
                    "controlled_crossover",
                    "de_novo",
                )]},
                indent=2,
            ),
            encoding="utf-8",
        )
        close = reports / f"autoresearch_lineage_round1_{target}_test_close"
        close.mkdir()
        (close / "archive_updates.json").write_text(
            json.dumps({"branches": {target: {"current": {"a": [], "b": []}}}}),
            encoding="utf-8",
        )
        for name in ("parent_child_delta_receipts.json", "replay_bundle.json"):
            (close / name).write_text("{}", encoding="utf-8")

    pool = summarize(
        [candidate(target, index) for target in TARGETS for index in range(50)]
    )
    access = {
        "authoritative_database": {
            "host": "192.168.99.19",
            "database": "pepagent",
            "in_recovery": False,
        },
        "migration_evidence": {
            "cutover_user_table_differences": 0,
            "old_primary_fenced": True,
        },
        "access_contract": {
            "workstation_endpoint": "127.0.0.1:55432",
            "consumer_thread_id": "01a01cf7-c832-7930-b1a4-b39edcf1dca4",
            "local_database_query_status": "passed",
        },
        "proactive_thread_message_sent": False,
    }

    result = verify(pool, reports, access)

    assert result["verified"] is True
    assert result["errors"] == []
    assert all(
        branch["latest_archive_front_count"] == 2
        for branch in result["branches"].values()
    )
