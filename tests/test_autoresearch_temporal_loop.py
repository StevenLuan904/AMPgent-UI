from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from pepagent.autoresearch_closed_loop import (
    ContinuationPolicy,
    ControlledCrossoverAction,
    CrossoverFragment,
    DeNovoAction,
    MultiFrontArchivePolicy,
)
from pepagent.provenance.hashing import sha256_text
from pepagent.v38_science_execution import build_default_v38_sequence_contract
from pepagent.workers.autoresearch_activities import (
    _build_replay_bundle,
    build_typed_action_projection,
)
from pepagent.workers.v38_activities import V38_METRIC_OBSERVATIONS
from pepagent.workers.v38_temporal_worker import V38_ROLE_CONFIG
from pepagent.workflows import autoresearch as workflow_module
from pepagent.workflows.autoresearch import (
    AutoResearchClosedLoopWorkflow,
    _validate_request,
)

RUN_ID = "00000000-0000-0000-0000-000000000001"
CHILD_ID = "00000000-0000-0000-0000-000000000002"
ACTION_ID = "00000000-0000-0000-0000-000000000003"
DECISION_ID = "00000000-0000-0000-0000-000000000004"


def _de_novo_action() -> DeNovoAction:
    return DeNovoAction(
        branch_key="PBP2a",
        generation=1,
        seed=41,
        operator_id="agent-de-novo-v1",
        operator_release_sha256="a" * 64,
        expected_improvement_metrics=("macrel_amp_probability",),
        protected_metrics=("guruprasad_instability_index",),
        evidence_sha256s=("b" * 64,),
        peptide_length=10,
        proposed_sequence="KRWLAKIRKL",
    )


def _request() -> dict[str, Any]:
    contract = build_default_v38_sequence_contract().model_dump(mode="json")
    action = _de_novo_action()
    return {
        "schema_version": "ampgent.autoresearch-workflow-request.1",
        "run_id": RUN_ID,
        "branch_key": "PBP2a",
        "execution_contract": contract,
        "metric_plugins_by_name": {
            name: {"name": name} for name in contract["metric_plugins"]
        },
        "task_queues": {
            "workflow_and_control": "pepagent-control-v38",
            "action_execution": "pepagent-generator-v38",
            "sequence_metrics": "pepagent-cpu-metrics-v38",
        },
        "planner_provider": {
            "activity_name": "plan_autoresearch_actions",
            "task_queue": "pepagent-autoresearch-director",
            "planner_contract": {"de_novo_quota": 0.2},
        },
        "action_executor": {"operator_environment_sha256": "d" * 64},
        "initial_action_plan": {
            "agent_decision": {
                "agent_name": "research-director",
                "agent_version": "1",
                "model_name": "deterministic-test",
                "prompt_text": "improve the PBP2a activity-safety front",
                "rationale_by_action_sha256": {
                    action.action_sha256: "open a new sequence family"
                },
            },
            "actions": [action.model_dump(mode="json")],
        },
        "archive_policy": MultiFrontArchivePolicy().model_dump(mode="json"),
        "continuation_policy": ContinuationPolicy(
            maximum_generations_per_run=5,
            minimum_high_quality_candidates=50,
            stagnation_patience_generations=1,
        ).model_dump(mode="json"),
        "control_environment_sha256": "c" * 64,
        "maximum_iterations_per_workflow_execution": 10,
    }


def test_typed_crossover_projection_preserves_both_parents_and_spans() -> None:
    parent_id = "00000000-0000-0000-0000-000000000010"
    donor_id = "00000000-0000-0000-0000-000000000011"
    action = ControlledCrossoverAction(
        branch_key="PBP2a",
        generation=1,
        seed=9,
        operator_id="controlled-mix-v1",
        operator_release_sha256="d" * 64,
        expected_improvement_metrics=("macrel_amp_probability",),
        protected_metrics=("guruprasad_instability_index",),
        evidence_sha256s=("e" * 64,),
        parent_candidate_id=parent_id,
        parent_sequence_sha256=sha256_text("ACDEFGHIKL"),
        donor_candidate_id=donor_id,
        donor_sequence_sha256=sha256_text("LMNPQRSTVW"),
        fragments=(
            CrossoverFragment(
                source_role="primary_parent",
                source_start_zero_based=0,
                source_end_exclusive=5,
            ),
            CrossoverFragment(
                source_role="donor_parent",
                source_start_zero_based=5,
                source_end_exclusive=10,
            ),
        ),
    )

    projection = build_typed_action_projection(
        action.model_dump(mode="json"),
        iteration_no=0,
        action_ordinal=1,
        rationale_text="mix complementary activity endpoints",
    )

    assert projection["action_kind"] == "controlled_mix"
    assert [row["parent_candidate_id"] for row in projection["lineage_sources"]] == [
        parent_id,
        donor_id,
    ]
    assert projection["lineage_sources"][0]["source_spans"] == [
        {"child": [1, 5], "source": [1, 5]}
    ]
    assert projection["lineage_sources"][1]["source_spans"] == [
        {"child": [6, 10], "source": [6, 10]}
    ]


def test_replay_bundle_is_canonical_across_unordered_inputs() -> None:
    action = _de_novo_action()
    plan = {
        "agent_decision_id": DECISION_ID,
        "action_batch_sha256": "1" * 64,
        "actions": [
            {
                "action_id": ACTION_ID,
                "repository_action_sha256": "2" * 64,
                "runtime_action_sha256": action.action_sha256,
                "runtime_action": action.model_dump(mode="json"),
            }
        ],
    }
    children = {
        "candidate_count": 1,
        "candidates": [{"id": CHILD_ID, "sequence": action.proposed_sequence}],
    }
    inputs = {
        "run_id": uuid.UUID(RUN_ID),
        "iteration_no": 0,
        "action_plan": plan,
        "children": children,
        "metric_tool_call_ids": ["z", "a"],
        "delta_receipts": [
            {"delta_sha256": "f" * 64},
            {"delta_sha256": "0" * 64},
        ],
        "archive_update": {"update_sha256": "3" * 64},
        "archive_versions": {"z": "9", "a": "1"},
        "continuation_decision_id": uuid.UUID(DECISION_ID),
    }

    first = _build_replay_bundle(**inputs)
    second_inputs = copy.deepcopy(inputs)
    second_inputs["metric_tool_call_ids"].reverse()
    second_inputs["delta_receipts"].reverse()
    second_inputs["archive_versions"] = {"a": "1", "z": "9"}
    second = _build_replay_bundle(**second_inputs)

    assert first == second
    assert first["score_all"]["required_metric_count"] == 12
    assert first["score_all"]["completed_evaluation_count"] == 12


@pytest.mark.asyncio
async def test_autoresearch_workflow_end_to_end_replays_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    request["initial_action_plan"] = None

    async def execute_once() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        trace: list[dict[str, Any]] = []

        async def fake_execute_activity(
            name: str, payload: dict[str, Any], **_kwargs: Any
        ) -> Any:
            trace.append({"activity": name, "payload": copy.deepcopy(payload)})
            if name in {"mark_run_started", "mark_run_succeeded"}:
                return None
            if name == "plan_autoresearch_actions":
                action = _de_novo_action().model_dump(mode="json")
                return {
                    "agent_decision": {
                        "agent_name": "autoresearch-multi-front-rule-planner",
                        "agent_version": "1",
                        "model_name": None,
                        "prompt_text": "retain conflict fronts",
                        "rationale_by_action_sha256": {
                            action["action_sha256"]: "open a new sequence family"
                        },
                    },
                    "actions": [action],
                    "planner_receipt": {
                        "tool_call_id": "planner-tool-1",
                        "artifact_sha256": "9" * 64,
                    },
                }
            if name == "persist_autoresearch_action_plan":
                action = payload["actions"][0]
                return {
                    "run_id": RUN_ID,
                    "branch_key": "PBP2a",
                    "iteration_no": 0,
                    "agent_decision_id": DECISION_ID,
                    "action_batch_sha256": "1" * 64,
                    "actions": [
                        {
                            "action_id": ACTION_ID,
                            "repository_action_sha256": "2" * 64,
                            "runtime_action_sha256": action["action_sha256"],
                            "runtime_action": action,
                            "lineage_sources": [],
                        }
                    ],
                }
            if name == "execute_autoresearch_action_batch":
                return {
                    "action_batch_sha256": "1" * 64,
                    "results": [
                        {
                            "action_id": ACTION_ID,
                            "sequence": "KRWLAKIRKL",
                            "sequence_sha256": sha256_text("KRWLAKIRKL"),
                        }
                    ],
                    "provenance": {},
                }
            if name == "persist_autoresearch_children":
                return {
                    "candidate_count": 1,
                    "candidates": [
                        {
                            "id": CHILD_ID,
                            "sequence": "KRWLAKIRKL",
                            "sequence_sha256": sha256_text("KRWLAKIRKL"),
                            "generation": 1,
                        }
                    ],
                }
            if name == "evaluate_v38_sequence_metric":
                return {"plugin": payload["plugin"]}
            if name == "persist_v38_sequence_metric":
                plugin_name = str(payload["metric_result"]["plugin"]["name"])
                return {
                    "plugin": plugin_name,
                    "evaluation_count": len(V38_METRIC_OBSERVATIONS[plugin_name]),
                    "tool_call_id": f"tool-{plugin_name}",
                }
            if name == "finalize_autoresearch_iteration":
                return {
                    "checkpoint_id": "checkpoint-1",
                    "checkpoint_receipt_sha256": "3" * 64,
                    "replay_sha256": "4" * 64,
                    "durable_counts": {
                        "action_count": 1,
                        "candidate_count": 1,
                        "evaluation_count": 12,
                        "metric_delta_count": 0,
                        "archive_version_count": 8,
                        "checkpoint_count": 1,
                        "replay_count": 1,
                    },
                    "continuation": {
                        "next_action": "quality_goal_met",
                        "continue_required": False,
                        "high_quality_candidate_count": 1,
                        "archive_gain": False,
                        "consecutive_stagnant_generations": 1,
                        "reasons": ["quality_goal_met"],
                    },
                }
            raise AssertionError(f"unexpected activity: {name}")

        monkeypatch.setattr(workflow_module.workflow, "execute_activity", fake_execute_activity)
        monkeypatch.setattr(
            workflow_module.workflow,
            "info",
            lambda: SimpleNamespace(workflow_id="autoresearch-test-workflow"),
        )
        result = await AutoResearchClosedLoopWorkflow().run(copy.deepcopy(request))
        return result, trace

    first_result, first_trace = await execute_once()
    second_result, second_trace = await execute_once()

    assert first_result == second_result
    assert first_trace == second_trace
    assert first_result["status"] == "quality_goal_met"
    names = [item["activity"] for item in first_trace]
    assert names[0:5] == [
        "mark_run_started",
        "plan_autoresearch_actions",
        "persist_autoresearch_action_plan",
        "execute_autoresearch_action_batch",
        "persist_autoresearch_children",
    ]
    assert names.count("evaluate_v38_sequence_metric") == 5
    assert names.count("persist_v38_sequence_metric") == 5
    assert names[-2:] == ["finalize_autoresearch_iteration", "mark_run_succeeded"]


def test_autoresearch_worker_registration_is_complete() -> None:
    _, control_activities, workflows = V38_ROLE_CONFIG["v38-control"]
    registered = {
        item.__temporal_activity_definition.name for item in control_activities
    }
    assert {
        "persist_autoresearch_action_plan",
        "persist_autoresearch_children",
        "finalize_autoresearch_iteration",
        "plan_autoresearch_actions",
    } <= registered
    assert AutoResearchClosedLoopWorkflow in workflows
    _, generator_activities, _ = V38_ROLE_CONFIG["v38-generator"]
    generator_registered = {
        item.__temporal_activity_definition.name for item in generator_activities
    }
    assert "execute_autoresearch_action_batch" in generator_registered


def test_autoresearch_request_rejects_partial_score_all_registry() -> None:
    request = _request()
    _validate_request(request)
    request["metric_plugins_by_name"].pop(next(iter(request["metric_plugins_by_name"])))
    with pytest.raises(ValueError, match="plugin registry"):
        _validate_request(request)
