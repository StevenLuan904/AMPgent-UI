from __future__ import annotations

import json
from pathlib import Path


def _receipt() -> dict:
    path = Path(
        "reports/pool_a_md_50ns_expansion_20260903/"
        "md_observability_recovery_20260904.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_recovered_probe_channels_and_exact_remote_handles() -> None:
    payload = _receipt()
    tunnels = payload["tunnel_recovery"]
    assert tunnels["main_019"]["local_endpoint"] == "127.0.0.1:32222"
    assert tunnels["main_019"]["listener_pid"] == 45184
    assert tunnels["synth"]["local_endpoint"] == "127.0.0.1:32224"
    assert tunnels["synth"]["listener_pid"] == 9372
    assert tunnels["secrets_persisted_or_printed"] is False

    main = payload["remote_read_only_observation"]["main_019"]
    synth = payload["remote_read_only_observation"]["synth_successor_11"]
    assert main["alive"] is True
    assert main["supervisor_pids"] == {"md": 3986807, "analysis": 3977733}
    assert synth["alive_pids"] is True
    assert synth["supervisor_pids"] == {
        "md": 1280301,
        "interface_analysis": 3311802,
        "mmgbsa": 3360111,
    }
    assert synth["runner_pids"] == [58820, 1280316, 2060743]


def test_recovery_preserves_486_partition_and_read_only_boundary() -> None:
    payload = _receipt()
    assert payload["decision"] == "verified_wait"
    partition = payload["current_486_authoritative_partition"]
    assert partition["identity_union"] == 486
    assert (
        partition["full_evidence_unique"]
        + partition["analysis_pending_unique"]
        + partition["running_or_incomplete_unique"]
        + partition["not_started_unique"]
        + partition["failed_unique"]
        == 486
    )
    assert partition["launched_unique"] == (
        partition["full_evidence_unique"] + partition["running_or_incomplete_unique"]
    )
    assert partition["new_full_evidence_count_observable"] == 0
    assert partition["relative_to_full_evidence_baseline_31"] == {
        "baseline_full_evidence_unique": 31,
        "current_full_evidence_unique": 31,
        "delta_full_evidence_unique": 0,
    }
    assert payload["postgresql_read_only_probe"]["writes_performed"] is False
    assert payload["postgresql_read_only_probe"]["ingest_performed"] is False
    assert payload["mutations"]["remote_supervisor_or_runner_restart"] is False
    assert payload["mutations"]["remote_task_submission"] is False
    assert payload["mutations"]["remote_large_artifact_download"] is False
    assert payload["mutations"]["remote_large_artifact_delete"] is False
    assert payload["mutations"]["prohibited_32_gpu2_gpu3_touched"] is False
    assert payload["remote_read_only_observation"]["synth_successor_11"]["gpu_observation"][
        "prohibited_32_gpu2_gpu3_touched"
    ] is False
