from pathlib import Path

import pytest

from pepagent.v34_evidence import (
    build_v34_evidence_plan,
    validate_v34_provider_change_request_ledger,
    validate_v34_replay_graph,
)
from pepagent.v34_preregistration import load_v34_preregistration

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_knowledge_pepshot_ablation_v34.yaml"


def _plan() -> dict:
    manifest = load_v34_preregistration(CONFIG)
    return build_v34_evidence_plan(
        manifest.parent_cohort["members"],
        order_salt=manifest.factorial_design["arm_order_salt"],
        provider_governance=manifest.provider_governance,
    )


def _complete_graph(plan: dict) -> dict:
    tools = [{"logical_id": item, "status": "succeeded"} for item in plan["required_tool_call_ids"]]
    dependencies = [
        {"parent_logical_id": parent, "child_logical_id": child}
        for parent, child in plan["required_dependencies"]
    ]
    artifacts = []
    for episode in plan["episodes"]:
        for tool in episode["tool_calls"]:
            artifacts.extend(
                {
                    "tool_call_logical_id": tool["logical_id"],
                    "role": role,
                    "sha256": "a" * 64,
                }
                for role in tool["required_artifact_roles"]
            )
    for tool in plan["global_tool_calls"]:
        artifacts.extend(
            {
                "tool_call_logical_id": tool["logical_id"],
                "role": role,
                "sha256": "b" * 64,
            }
            for role in tool["required_artifact_roles"]
        )
    adjudications = [
        {
            "tool_call_logical_id": episode["blinded_adjudication_tool_id"],
            "locked_before_assignment_reveal": True,
        }
        for episode in plan["episodes"]
    ]
    return {
        "tool_calls": tools,
        "dependencies": dependencies,
        "artifacts": artifacts,
        "adjudications": adjudications,
    }


def _empty_provider_ledger(plan: dict) -> dict:
    return {
        "schema_version": "1.0",
        "provider_owner_tasks": plan["provider_governance_contract"][
            "provider_owner_tasks"
        ],
        "formal_run_release_hot_swap_performed": False,
        "database_parentage_verified": True,
        "all_external_requests_have_receipts": True,
        "change_requests": [],
    }


def test_v34_evidence_plan_covers_every_parent_arm_and_global_analysis() -> None:
    plan = _plan()
    assert plan["episode_count"] == 96
    assert len(plan["required_tool_call_ids"]) == 96 * 8 + 3
    assert len({item["opaque_label"] for item in plan["episodes"]}) == 96
    assert plan["parent_manifest_sha256"] == (
        "f1955476cb761d9ca300a8fed00d9bb847e775ee5f4c1ef51d1346376a4f943e"
    )
    assert len(plan["plan_sha256"]) == 64


def test_v34_complete_replay_graph_passes() -> None:
    plan = _plan()
    validate_v34_replay_graph(plan, _complete_graph(plan))


@pytest.mark.parametrize("section", ["tool_calls", "dependencies", "artifacts", "adjudications"])
def test_v34_replay_fails_closed_on_missing_evidence(section: str) -> None:
    plan = _plan()
    graph = _complete_graph(plan)
    graph[section].pop()
    with pytest.raises(ValueError, match="v34 replay"):
        validate_v34_replay_graph(plan, graph)


def test_v34_replay_fails_if_assignment_is_revealed_before_lock() -> None:
    plan = _plan()
    graph = _complete_graph(plan)
    graph["adjudications"][0]["locked_before_assignment_reveal"] = False
    with pytest.raises(ValueError, match="before adjudication lock"):
        validate_v34_replay_graph(plan, graph)


def test_v34_provider_governance_requires_explicit_replayable_no_change_path() -> None:
    plan = _plan()
    validate_v34_provider_change_request_ledger(
        plan["provider_governance_contract"], _empty_provider_ledger(plan)
    )


def test_v34_provider_governance_replays_rejection_through_read_only_reacceptance() -> None:
    plan = _plan()
    ledger = _empty_provider_ledger(plan)
    ledger["change_requests"] = [
        {
            "request_id": "pepshot-review-semantics-001",
            "provider": "pepshot",
            "owner_task_id": "019fb910-f2dd-7be1-a7e6-bfe381512c25",
            "rejecting_run_id": "11111111-1111-4111-8111-111111111111",
            "change_request_run_id": "22222222-2222-4222-8222-222222222222",
            "rejected_release_identity": (
                "pepshot-34487cf9667a64c3-fe1e5382de8cab09"
            ),
            "trigger_category": "scientific_review_inadequacy",
            "reproducible_input_artifact_sha256": "1" * 64,
            "violated_contract_artifact_sha256": "2" * 64,
            "acceptance_criteria_artifact_sha256": "3" * 64,
            "external_request_receipt_artifact_sha256": "4" * 64,
            "lifecycle_state": "read_only_reaccepted",
            "consumer_adaptation_performed": False,
            "replacement_release_identity": "pepshot-replacement-immutable-001",
            "replacement_release_manifest_sha256": "5" * 64,
            "read_only_acceptance_receipt_artifact_sha256": "6" * 64,
        }
    ]
    validate_v34_provider_change_request_ledger(
        plan["provider_governance_contract"], ledger
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("consumer_adaptation_performed", True, "consumer adaptation"),
        ("owner_task_id", "wrong-task", "wrong owner"),
        (
            "change_request_run_id",
            "11111111-1111-4111-8111-111111111111",
            "child run",
        ),
    ],
)
def test_v34_provider_governance_rejects_unreplayable_escalation(
    field: str, value: object, message: str
) -> None:
    plan = _plan()
    ledger = _empty_provider_ledger(plan)
    request = {
        "request_id": "pepshot-request-001",
        "provider": "pepshot",
        "owner_task_id": "019fb910-f2dd-7be1-a7e6-bfe381512c25",
        "rejecting_run_id": "11111111-1111-4111-8111-111111111111",
        "change_request_run_id": "22222222-2222-4222-8222-222222222222",
        "rejected_release_identity": "pepshot-34487cf9667a64c3-fe1e5382de8cab09",
        "trigger_category": "scientific_review_inadequacy",
        "reproducible_input_artifact_sha256": "1" * 64,
        "violated_contract_artifact_sha256": "2" * 64,
        "acceptance_criteria_artifact_sha256": "3" * 64,
        "external_request_receipt_artifact_sha256": "4" * 64,
        "lifecycle_state": "change_request_sent",
        "consumer_adaptation_performed": False,
    }
    request[field] = value
    ledger["change_requests"] = [request]
    with pytest.raises(ValueError, match=message):
        validate_v34_provider_change_request_ledger(
            plan["provider_governance_contract"], ledger
        )
