from datetime import UTC, datetime

import pytest

from pepagent.v37_worker_deployment import (
    V37PhysicalWorkerObservation,
    V37RemoteTarget,
    V37TemporalPollerObservation,
    build_v37_remote_launch_command,
    build_v37_worker_placement_snapshot,
    validate_v37_remote_target,
)


def test_v37_remote_targets_allow_only_frozen_placements() -> None:
    for target in (
        V37RemoteTarget("synth", "boltz2", 5, "/sdd_data/pepagent", "gpu5"),
        V37RemoteTarget("synth", "boltz2", 6, "/sdd_data/pepagent", "gpu6"),
        V37RemoteTarget("synth", "rosetta", "cpu", "/sdd_data/pepagent", "cpu"),
        V37RemoteTarget(
            "192.168.99.19", "boltz2", 5, "/data1/huangyueshan/pepagent", "gpu5"
        ),
    ):
        assert validate_v37_remote_target(target) is target


@pytest.mark.parametrize(
    "target",
    [
        V37RemoteTarget(
            "192.168.99.19", "boltz2", 4, "/data1/huangyueshan/pepagent", "gpu4"
        ),
        V37RemoteTarget("synth", "boltz2", 4, "/sdd_data/pepagent", "gpu4"),
        V37RemoteTarget("synth", "boltz2", 7, "/sdd_data/pepagent", "gpu7"),
        V37RemoteTarget(
            "192.168.99.19", "rosetta", "cpu", "/data1/huangyueshan/pepagent", "cpu"
        ),
    ],
)
def test_v37_remote_targets_reject_noneligible_resources(target: V37RemoteTarget) -> None:
    with pytest.raises(ValueError, match="not eligible|permits only"):
        validate_v37_remote_target(target)


def test_v37_remote_launch_command_binds_release_and_revision() -> None:
    command = build_v37_remote_launch_command(
        V37RemoteTarget("synth", "boltz2", 6, "/sdd_data/pepagent", "gpu6"),
        release_sha256="a" * 64,
        source_revision="b" * 40,
    )
    assert "PEPAGENT_PHYSICAL_HOST=synth" in command
    assert f"platform/releases/{'a' * 64}/deploy/remote/start_v37_worker.sh" in command
    assert "start_v37_worker.sh boltz2 6 gpu6" in command
    assert f"{'a' * 64} {'b' * 40}" in command


def test_v37_worker_snapshot_joins_physical_processes_to_exact_pollers() -> None:
    revision = "b" * 40
    queues = [
        ("v37-control", "pepagent-control-v37", "local", None),
        ("v37-generator", "pepagent-generator-v37", "local", None),
        ("v37-provider", "pepagent-provider-v37", "local", None),
        ("metrics", "pepagent-cpu-metrics", "local", None),
        ("boltz2", "pepagent-gpu-boltz2", "synth", 5),
        ("rosetta", "pepagent-cpu-rosetta", "synth", None),
    ]
    captured_at = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)
    physical = [
        V37PhysicalWorkerObservation(
            physical_host=host,
            gpu_index=gpu,
            pid=index,
            role=role,
            task_queue=queue,
            source_revision=revision,
            release_sha256="c" * 64,
            environment_sha256="d" * 64,
            weights_sha256="e" * 64 if role == "boltz2" else None,
            ampgent_owned=True,
            foreign_process_present=False,
        )
        for index, (role, queue, host, gpu) in enumerate(queues, start=100)
    ]
    pollers = [
        V37TemporalPollerObservation(
            task_queue=worker.task_queue,
            poller_identity=(
                f"pepagent:{worker.role}:{worker.pid}@node:{worker.source_revision}"
            ),
            poller_last_access_at=captured_at.isoformat(),
        )
        for worker in physical
    ]
    payload = build_v37_worker_placement_snapshot(
        physical_observations=physical,
        temporal_pollers=pollers,
        captured_at=captured_at,
        expected_source_revision=revision,
        active_workflow_count=0,
    )
    assert len(payload["placements"]) == 6
    assert payload["placements"][4]["gpu_index"] == 5
