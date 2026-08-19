from __future__ import annotations

from copy import deepcopy
from typing import Any

from pepagent.provenance.hashing import sha256_json
from pepagent.workflows.v38_sequence_first import _validate_request

V38_ROLE_QUEUES = {
    "v38-control": "pepagent-control-v38",
    "v38-generator": "pepagent-generator-v38",
    "v38-metrics": "pepagent-cpu-metrics-v38",
    "v38-boltz": "pepagent-gpu-boltz2-v38",
    "v38-rosetta": "pepagent-cpu-rosetta-v38",
}


def _require_sha(value: object, *, length: int, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"invalid {label}")
    return value


def _validate_worker_placement(
    placement: dict[str, Any],
    *,
    controller_state: dict[str, Any],
) -> None:
    if placement.get("schema_version") != "v38.worker-placement.1":
        raise ValueError("v38 worker placement schema is invalid")
    workers = placement.get("workers")
    if not isinstance(workers, dict) or set(workers) != set(V38_ROLE_QUEUES):
        raise ValueError("v38 worker placement does not cover exactly five roles")
    serialized = str(placement)
    if "192.168.99.32:2" in serialized or "192.168.99.32:3" in serialized:
        raise ValueError("v38 worker placement references a prohibited GPU")
    sources: set[str] = set()
    releases: set[str] = set()
    for role, queue in V38_ROLE_QUEUES.items():
        worker = workers[role]
        if (
            not isinstance(worker, dict)
            or worker.get("role") != role
            or worker.get("task_queue") != queue
            or worker.get("ampgent_owned") is not True
            or worker.get("foreign") is not False
            or not isinstance(worker.get("pid"), int)
            or worker["pid"] < 1
            or not isinstance(worker.get("poller_identity"), str)
            or not worker["poller_identity"]
        ):
            raise ValueError(f"v38 worker placement is invalid: {role}")
        sources.add(_require_sha(worker.get("source_revision"), length=40, label="source"))
        releases.add(_require_sha(worker.get("release_sha256"), length=64, label="release"))
    if len(sources) != 1 or len(releases) != 1:
        raise ValueError("v38 workers do not share one immutable source and release")
    if workers["v38-boltz"].get("resource") != "192.168.99.32:1":
        raise ValueError("v38 Boltz placement differs from the authorized GPU")
    if workers["v38-rosetta"].get("resource") != "synth:cpu":
        raise ValueError("v38 Rosetta placement differs from the authorized CPU")
    sequence_release = controller_state.get("sequence_worker_release")
    if not isinstance(sequence_release, dict):
        raise ValueError("controller has no accepted sequence worker release")
    if sequence_release.get("source_revision") != next(iter(sources)) or (
        sequence_release.get("release_sha256") != next(iter(releases))
    ):
        raise ValueError("controller and placement worker identities drifted")
    provider = placement.get("refinement_provider")
    if (
        not isinstance(provider, dict)
        or provider.get("task_queue") != "pepagent-refinement-provider-v38"
        or not isinstance(provider.get("poller_identity"), str)
        or not provider["poller_identity"]
    ):
        raise ValueError("v38 refinement provider placement is invalid")
    _require_sha(
        provider.get("runtime_manifest_sha256"),
        length=64,
        label="refinement provider runtime manifest",
    )


def build_v38_submission_preflight(
    *,
    request_template: dict[str, Any],
    controller_state: dict[str, Any],
    worker_placement: dict[str, Any],
    benchmark_sha256: str,
    target_panel_sha256: str,
) -> dict[str, Any]:
    """Bind one not-yet-submitted v38 science request to its executable placement."""

    if request_template.get("run_id") is not None:
        raise ValueError("v38 preflight request template must precede run reservation")
    if request_template.get("submission_preflight") is not None:
        raise ValueError("v38 request template cannot self-assert a passed preflight")
    validated_request = deepcopy(request_template)
    validated_request["submission_preflight"] = {
        "status": "ready_to_submit_unique_run"
    }
    _validate_request(validated_request)
    if controller_state.get("schema_version") != "v38.agent-controller-state.1":
        raise ValueError("v38 controller state schema is invalid")
    if controller_state.get("formal_science_workflow_submitted") is not False:
        raise ValueError("v38 formal workflow is already claimed as submitted")
    if controller_state.get("candidate_generation_started") is not False:
        raise ValueError("v38 candidate generation already started")
    if controller_state.get("blockers") != []:
        raise ValueError("v38 controller still has blockers")
    counts = controller_state.get("durable_counts")
    if not isinstance(counts, dict) or any(int(value) != 0 for value in counts.values()):
        raise ValueError("v38 control run contains unexpected science outputs")
    history_terminal_run_count = controller_state.get("history_terminal_run_count")
    if (
        not isinstance(history_terminal_run_count, int)
        or history_terminal_run_count < 1
    ):
        raise ValueError("v38 historical terminal-run denominator is invalid")
    plan = request_template.get("multitarget_plan_template")
    if (
        not isinstance(plan, dict)
        or plan.get("history_snapshot_sha256")
        != controller_state.get("history_snapshot_sha256")
    ):
        raise ValueError("v38 request does not inherit the controller history snapshot")
    _validate_worker_placement(worker_placement, controller_state=controller_state)
    benchmark_sha256 = _require_sha(
        benchmark_sha256, length=64, label="benchmark SHA-256"
    )
    target_panel_sha256 = _require_sha(
        target_panel_sha256, length=64, label="target panel SHA-256"
    )
    request = deepcopy(request_template)
    preflight_identity = {
        "schema_version": "v38.submission-preflight.1",
        "controller_run_id": controller_state["controller_run_id"],
        "controller_formal_submission_key": controller_state["formal_submission_key"],
        "history_snapshot_sha256": controller_state["history_snapshot_sha256"],
        "history_terminal_run_count": controller_state["history_terminal_run_count"],
        "benchmark_sha256": benchmark_sha256,
        "target_panel_sha256": target_panel_sha256,
        "request_template_sha256": sha256_json(request),
        "worker_placement_sha256": sha256_json(worker_placement),
        "sequence_worker_source_revision": worker_placement["workers"]["v38-control"][
            "source_revision"
        ],
        "sequence_worker_release_sha256": worker_placement["workers"]["v38-control"][
            "release_sha256"
        ],
        "refinement_provider_runtime_manifest_sha256": worker_placement[
            "refinement_provider"
        ]["runtime_manifest_sha256"],
    }
    formal_submission_key = sha256_json(preflight_identity)
    return {
        **preflight_identity,
        "formal_submission_key": formal_submission_key,
        "workflow_id": f"pepagent-sequence-first-v38-{formal_submission_key}",
        "status": "ready_to_submit_unique_run",
        "execution_authorized": True,
        "failed_gates": [],
    }
