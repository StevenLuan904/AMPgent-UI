from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pepagent.v37_capacity import validate_v37_worker_placement_snapshot

V37_SOURCE_REVISION_PATTERN = re.compile(r"[0-9a-f]{40}")
V37_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class V37RemoteTarget:
    physical_host: Literal["synth", "192.168.99.19"]
    role: Literal["boltz2", "rosetta"]
    resource: int | Literal["cpu"]
    root: str
    instance: str


@dataclass(frozen=True)
class V37PhysicalWorkerObservation:
    physical_host: str
    gpu_index: int | None
    pid: int
    role: str
    task_queue: str
    source_revision: str
    release_sha256: str
    environment_sha256: str
    weights_sha256: str | None
    ampgent_owned: bool
    foreign_process_present: bool


@dataclass(frozen=True)
class V37TemporalPollerObservation:
    task_queue: str
    poller_identity: str
    poller_last_access_at: str


def validate_v37_remote_target(target: V37RemoteTarget) -> V37RemoteTarget:
    """Reject every placement outside the frozen v37 resource contract."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", target.instance):
        raise ValueError("v37 worker instance is invalid")
    if target.physical_host == "synth":
        if target.root != "/sdd_data/pepagent":
            raise ValueError("v37 synth root drifted")
        if target.role == "boltz2" and target.resource not in {5, 6}:
            raise ValueError("v37 synth Boltz placement is not eligible")
        if target.role == "rosetta" and target.resource != "cpu":
            raise ValueError("v37 synth Rosetta placement must be CPU")
    elif target.physical_host == "192.168.99.19":
        if target.root != "/data1/huangyueshan/pepagent":
            raise ValueError("v37 .19 root drifted")
        if target.role != "boltz2" or target.resource != 5:
            raise ValueError("v37 .19 permits only the frozen GPU5 Boltz placement")
    else:  # pragma: no cover - Literal protects typed callers
        raise ValueError("v37 physical host is prohibited")
    return target


def build_v37_remote_launch_command(
    target: V37RemoteTarget,
    *,
    release_sha256: str,
    source_revision: str,
) -> str:
    """Build, but never execute, the exact immutable remote worker launch command."""
    validate_v37_remote_target(target)
    if not V37_SHA256_PATTERN.fullmatch(release_sha256):
        raise ValueError("v37 release SHA-256 is invalid")
    if not V37_SOURCE_REVISION_PATTERN.fullmatch(source_revision):
        raise ValueError("v37 source revision is invalid")
    launcher = (
        f"{target.root}/platform/releases/{release_sha256}"
        "/deploy/remote/start_v37_worker.sh"
    )
    values = [
        "env",
        f"PEPAGENT_ROOT={target.root}",
        f"PEPAGENT_PHYSICAL_HOST={target.physical_host}",
        launcher,
        target.role,
        str(target.resource),
        target.instance,
        release_sha256,
        source_revision,
    ]
    return shlex.join(values)


def build_v37_worker_placement_snapshot(
    *,
    physical_observations: list[V37PhysicalWorkerObservation],
    temporal_pollers: list[V37TemporalPollerObservation],
    captured_at: datetime,
    expected_source_revision: str,
    active_workflow_count: int,
) -> dict[str, Any]:
    """Join independent physical and Temporal observations into the frozen evidence schema."""
    if captured_at.tzinfo is None:
        raise ValueError("v37 placement capture timestamp lacks timezone")
    pollers_by_queue: dict[str, list[V37TemporalPollerObservation]] = {}
    for poller in temporal_pollers:
        pollers_by_queue.setdefault(poller.task_queue, []).append(poller)
    placements: list[dict[str, Any]] = []
    for worker in physical_observations:
        matching = [
            poller
            for poller in pollers_by_queue.get(worker.task_queue, [])
            if _poller_identity_matches_worker(poller.poller_identity, worker)
        ]
        if len(matching) != 1:
            raise ValueError("v37 physical worker does not map to exactly one Temporal poller")
        poller = matching[0]
        placements.append(
            {
                "physical_host": worker.physical_host,
                "gpu_index": worker.gpu_index,
                "pid": worker.pid,
                "role": worker.role,
                "task_queue": worker.task_queue,
                "poller_identity": poller.poller_identity,
                "poller_last_access_at": poller.poller_last_access_at,
                "source_revision": worker.source_revision,
                "release_sha256": worker.release_sha256,
                "environment_sha256": worker.environment_sha256,
                "weights_sha256": worker.weights_sha256,
                "ampgent_owned": worker.ampgent_owned,
                "foreign_process_present": worker.foreign_process_present,
            }
        )
    payload: dict[str, Any] = {
        "schema_version": "v37.worker-placement-snapshot.1",
        "captured_at": captured_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "active_workflow_count": active_workflow_count,
        "topology_frozen_for_run": True,
        "placements": placements,
    }
    validate_v37_worker_placement_snapshot(
        payload,
        expected_source_revision=expected_source_revision,
        reference_time=captured_at,
    )
    return payload


def _poller_identity_matches_worker(
    identity: str, worker: V37PhysicalWorkerObservation
) -> bool:
    prefix = f"pepagent:{worker.role}:{worker.pid}@"
    suffix = f":{worker.source_revision}"
    return identity.startswith(prefix) and identity.endswith(suffix)
