from __future__ import annotations

import json
from pathlib import Path


def test_md_handoff_plan_is_observability_safe_and_partitioned() -> None:
    path = Path(
        "reports/pool_a_md_50ns_expansion_20260903/"
        "md_analysis_handoff_plan_20260904.json"
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    snapshot = payload["current_authoritative_snapshot"]
    assert snapshot["identity_union"] == 486
    assert snapshot["launched"] == snapshot["full_evidence"] + snapshot["running_or_incomplete"]
    assert (
        snapshot["full_evidence"]
        + snapshot["running_or_incomplete"]
        + snapshot["not_started"]
        + snapshot["failed"]
        == 486
    )
    assert payload["completion_delta"]["new_full_evidence_count_observable"] is False
    assert payload["read_only_probe"]["main_19"]["remote_pid_state"] == "not_reclassified"
    assert payload["read_only_probe"]["synth_successor_11"]["listener_observed"] is False
    assert payload["replayable_analysis_handoff"]["remote_task_restart_allowed"] is False
