from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
import yaml

from pepagent.provenance.hashing import sha256_json
from pepagent.v38_submit_cli import (
    _start_or_recover_v38_workflow,
    _v38_controller_lock_id,
    _validate_v38_submission_bundle,
)

ROOT = Path(__file__).resolve().parents[1]


def _bundle() -> tuple[dict[str, object], ...]:
    request = json.loads(
        (ROOT / "var/run/v38-workers/v38-request-template.json").read_text(
            encoding="utf-8"
        )
    )
    panel = yaml.safe_load(
        (ROOT / "config/targets/amp_multitarget_panel_v38.yaml").read_text(
            encoding="utf-8"
        )
    )
    controller = {
        "controller_run_id": "b931b9df-c618-4d89-a1d1-ec52acc6e74e",
        "formal_submission_key": "a" * 64,
        "formal_science_workflow_submitted": False,
        "candidate_generation_started": False,
        "blockers": [],
        "durable_counts": {"candidates": 0, "evaluations": 0},
    }
    formal = "b" * 64
    preflight = {
        "schema_version": "v38.submission-preflight.1",
        "status": "ready_to_submit_unique_run",
        "execution_authorized": True,
        "failed_gates": [],
        "request_template_sha256": sha256_json(request),
        "controller_run_id": controller["controller_run_id"],
        "controller_formal_submission_key": controller["formal_submission_key"],
        "formal_submission_key": formal,
        "workflow_id": f"pepagent-sequence-first-v38-{formal}",
    }
    return request, preflight, controller, panel


def test_v38_submission_bundle_accepts_exact_frozen_inputs() -> None:
    request, preflight, controller, panel = _bundle()
    _validate_v38_submission_bundle(
        request_template=request,
        preflight=preflight,
        controller_state=controller,
        panel=panel,
    )


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("controller", "formal_science_workflow_submitted"), True, "already records"),
        (("preflight", "failed_gates"), ["x"], "not executable"),
        (("panel", "branches", 0, "coordinate_sha256"), "0" * 64, "binding drifted"),
    ],
)
def test_v38_submission_bundle_fails_closed_on_drift(
    path: tuple[object, ...], value: object, message: str
) -> None:
    request, preflight, controller, panel = _bundle()
    roots = {"request": request, "preflight": preflight, "controller": controller, "panel": panel}
    target: object = roots[str(path[0])]
    for key in path[1:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(ValueError, match=message):
        _validate_v38_submission_bundle(
            request_template=request,
            preflight=preflight,
            controller_state=controller,
            panel=panel,
        )


def test_v38_controller_lock_is_stable_scoped_signed_64_bit() -> None:
    first = UUID("b931b9df-c618-4d89-a1d1-ec52acc6e74e")
    second = UUID("83797ced-0c10-40bb-971b-39962f3905b6")
    assert _v38_controller_lock_id(first) == _v38_controller_lock_id(first)
    assert _v38_controller_lock_id(first) != _v38_controller_lock_id(second)
    assert -(2**63) <= _v38_controller_lock_id(first) < 2**63


class _Handle:
    def __init__(self, identity: dict[str, object]) -> None:
        self.identity = identity

    async def describe(self) -> SimpleNamespace:
        return SimpleNamespace(
            workflow_type="V38SequenceFirstAgentWorkflow",
            memo={"v38_submission_identity": self.identity},
        )


class _Client:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def start_workflow(
        self,
        workflow_type: str,
        request: dict[str, object],
        **kwargs: object,
    ) -> _Handle:
        identity = kwargs["memo"]["v38_submission_identity"]  # type: ignore[index]
        self.calls.append({"workflow_type": workflow_type, "request": request, **kwargs})
        return _Handle(identity)


@pytest.mark.asyncio
async def test_v38_workflow_start_carries_exact_submission_memo() -> None:
    client = _Client()
    handle = await _start_or_recover_v38_workflow(
        client,  # type: ignore[arg-type]
        workflow_id="pepagent-sequence-first-v38-" + "b" * 64,
        request={"run_id": "r"},
        request_sha256="c" * 64,
        run_id="r",
        controller_run_id="controller",
        formal_submission_key="b" * 64,
    )
    description = await handle.describe()
    assert len(client.calls) == 1
    assert description.memo["v38_submission_identity"]["request_sha256"] == "c" * 64
    assert client.calls[0]["task_queue"] == "pepagent-control-v38"
