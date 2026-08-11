from __future__ import annotations

from pathlib import Path

import pytest

from pepagent.v37_worker_bridge import (
    V37_ACTIVITY_CONTRACT,
    build_v37_worker_bridge_audit,
    build_v37_worker_role_config,
)

ROOT = Path(__file__).resolve().parents[1]


def _activity() -> None:
    return None


class _Workflow:
    pass


def test_v37_worker_bridge_audit_accepts_aligned_generator_budget_but_stays_blocked() -> None:
    audit = build_v37_worker_bridge_audit(
        repository_root=ROOT, raw_proposals_per_seed=1000
    )
    assert audit["amp_designer_cli_fixed_raw_budget"] == 1000
    assert audit["status"] == "blocked_missing_executable_activity_bridge"
    blocker_codes = {item["code"] for item in audit["blockers"]}
    assert "amp_designer_raw_budget_incompatible" not in blocker_codes
    assert "v37_activity_registry_incomplete" in blocker_codes
    assert audit["remote_host_or_process_probe_performed"] is False
    assert audit["activity_or_workflow_registered"] is False
    assert audit["formal_run_authorized"] is False


def test_v37_activity_contract_separates_gpu_boltz_and_cpu_rosetta() -> None:
    assert V37_ACTIVITY_CONTRACT["predict_v37_boltz_pose"]["queue"] == (
        "pepagent-gpu-boltz2"
    )
    assert V37_ACTIVITY_CONTRACT["score_v37_rosetta_pose"]["queue"] == (
        "pepagent-cpu-rosetta"
    )
    assert V37_ACTIVITY_CONTRACT["persist_v37_boltz_pose"]["queue"] == (
        "pepagent-control"
    )
    assert V37_ACTIVITY_CONTRACT["persist_v37_rosetta_pose"]["queue"] == (
        "pepagent-control"
    )


def test_v37_activity_contract_requires_database_native_evidence_owners() -> None:
    persisted_types = {
        evidence_type
        for contract in V37_ACTIVITY_CONTRACT.values()
        for evidence_type in contract.get("persists", [])
    }
    assert {
        "ToolCall",
        "CandidateOccurrence",
        "Candidate",
        "Evaluation",
        "ToolCallDependency",
        "AgentDecision",
        "AgentDecisionToolCallEdge",
        "Artifact",
        "LifecycleEvent",
    } <= persisted_types
    attempt_owners = [
        name
        for name, contract in V37_ACTIVITY_CONTRACT.items()
        if contract.get("persists_attempt_lifecycle") is True
    ]
    assert attempt_owners == [
        "generate_v37_batch",
        "evaluate_v37_metric",
        "predict_v37_boltz_pose",
        "score_v37_rosetta_pose",
    ]


def test_v37_role_config_rejects_missing_or_extra_activity_bindings() -> None:
    incomplete = {name: _activity for name in list(V37_ACTIVITY_CONTRACT)[:-1]}
    with pytest.raises(ValueError, match="registry drifted"):
        build_v37_worker_role_config(incomplete, workflow=_Workflow)
    extra = {name: _activity for name in V37_ACTIVITY_CONTRACT}
    extra["placeholder_success"] = _activity
    with pytest.raises(ValueError, match="registry drifted"):
        build_v37_worker_role_config(extra, workflow=_Workflow)


def test_v37_role_config_builds_only_from_complete_callable_registry() -> None:
    registry = {name: _activity for name in V37_ACTIVITY_CONTRACT}
    roles = build_v37_worker_role_config(registry, workflow=_Workflow)
    assert set(roles) == {
        "v37-control",
        "v37-portfolio",
        "v37-metrics",
        "v37-boltz2",
        "v37-rosetta",
    }
    assert roles["v37-control"][2] == [_Workflow]
    assert roles["v37-boltz2"][0] == "pepagent-gpu-boltz2"
    assert roles["v37-rosetta"][0] == "pepagent-cpu-rosetta"
