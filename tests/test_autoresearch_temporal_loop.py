from __future__ import annotations

import copy
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from pepagent.autoresearch_closed_loop import (
    ContinuationPolicy,
    ControlledCrossoverAction,
    CrossoverFragment,
    DeNovoAction,
    MultiFrontArchivePolicy,
    PepMLMTargetedAction,
)
from pepagent.db.models import Candidate
from pepagent.provenance.hashing import sha256_text
from pepagent.v38_science_execution import build_default_v38_sequence_contract
from pepagent.workers.autoresearch_activities import (
    _build_replay_bundle,
    _candidate_was_materialized_by_action,
    _duplicate_rejection_reason,
    _effective_planner_seed,
    build_typed_action_projection,
    execute_autoresearch_rule_action_batch,
)
from pepagent.workers.v38_activities import V38_METRIC_OBSERVATIONS
from pepagent.workers.v38_temporal_worker import V38_ROLE_CONFIG
from pepagent.workflows import autoresearch as workflow_module
from pepagent.workflows.autoresearch import (
    REMOTE_PERSISTENCE_MAXIMUM_ITERATIONS_PER_WORKFLOW_EXECUTION,
    AutoResearchClosedLoopWorkflow,
    _validate_request,
    with_remote_persistence_history_compaction,
)

RUN_ID = "00000000-0000-0000-0000-000000000001"
CHILD_ID = "00000000-0000-0000-0000-000000000002"
ACTION_ID = "00000000-0000-0000-0000-000000000003"
DECISION_ID = "00000000-0000-0000-0000-000000000004"


def test_explicit_planner_seed_is_generation_scoped() -> None:
    contract = {"seed": 173205}

    assert _effective_planner_seed(contract, 0) == 173205
    assert _effective_planner_seed(contract, 1) == 174214
    assert _effective_planner_seed(contract, 2) == 175223
    assert _effective_planner_seed({}, 2) == 106747


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
        "metric_plugins_by_name": {name: {"name": name} for name in contract["metric_plugins"]},
        "task_queues": {
            "workflow_and_control": "pepagent-autoresearch-control-v1",
            "action_execution": "pepagent-autoresearch-generator-v1",
            "sequence_metrics": "pepagent-autoresearch-metrics-v1",
        },
        "planner_provider": {
            "activity_name": "plan_autoresearch_actions",
            "task_queue": "pepagent-autoresearch-control-v1",
            "planner_contract": {"de_novo_quota": 0.2},
        },
        "action_executor": {
            "operator_environment_sha256": "d" * 64,
            "target_sequence": "MKTIIALSYIFCLVFADYKDDDDK",
            "target_sequence_sha256": sha256_text("MKTIIALSYIFCLVFADYKDDDDK"),
        },
        "initial_action_plan": {
            "agent_decision": {
                "agent_name": "research-director",
                "agent_version": "1",
                "model_name": "deterministic-test",
                "prompt_text": "improve the PBP2a activity-safety front",
                "rationale_by_action_sha256": {action.action_sha256: "open a new sequence family"},
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
    assert projection["lineage_sources"][0]["source_spans"] == [{"child": [1, 5], "source": [1, 5]}]
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
        "rejected_duplicates": [
            {
                "action_id": ACTION_ID,
                "status": "rejected_duplicate",
                "existing_candidate_id": CHILD_ID,
            }
        ],
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
    assert first["rejected_duplicates"][0]["status"] == "rejected_duplicate"


def test_duplicate_disposition_distinguishes_retry_from_existing_generation() -> None:
    action_id = uuid.UUID(ACTION_ID)
    candidate = Candidate(
        id=uuid.UUID(CHILD_ID),
        run_id=uuid.UUID(RUN_ID),
        sequence="KRWLAKIRKL",
        sequence_sha256=sha256_text("KRWLAKIRKL"),
        generation=2,
        status="generated",
        proposal_rank=1,
        metadata_json={"autoresearch_action_id": str(action_id)},
    )

    assert _candidate_was_materialized_by_action(
        candidate,
        action_id=action_id,
        requested_generation=2,
    )
    assert not _candidate_was_materialized_by_action(
        candidate,
        action_id=uuid.uuid4(),
        requested_generation=2,
    )
    assert not _candidate_was_materialized_by_action(
        candidate,
        action_id=action_id,
        requested_generation=3,
    )
    assert (
        _duplicate_rejection_reason(candidate, requested_generation=3)
        == "sequence_already_materialized_in_another_generation"
    )
    assert (
        _duplicate_rejection_reason(candidate, requested_generation=2)
        == "sequence_already_materialized_by_another_action"
    )


@pytest.mark.asyncio
async def test_autoresearch_workflow_end_to_end_replays_identically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    request["initial_action_plan"] = None
    request["seed_score_bundle_import"] = {
        "bundle_cache_root": r"C:\bounded-cache\score-all",
        "bundle_receipt_path": "bundle.receipt.json",
        "bundle_receipt_sha256": "8" * 64,
        "source_map_receipt_path": "score_source_map.receipt.json",
        "source_map_receipt_sha256": "7" * 64,
        "source_map_storage_uri": f"ssh://example.invalid/cas/{'7' * 64}/map.json",
        "target_key": "PBP2a",
    }

    async def execute_once() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        trace: list[dict[str, Any]] = []

        async def fake_execute_activity(name: str, payload: dict[str, Any], **kwargs: Any) -> Any:
            trace.append(
                {
                    "activity": name,
                    "payload": copy.deepcopy(payload),
                    "heartbeat_timeout": kwargs.get("heartbeat_timeout"),
                }
            )
            if name in {
                "mark_run_started",
                "mark_run_succeeded",
                "persist_autoresearch_score_all_bundle",
            }:
                return None
            if name == "plan_autoresearch_actions":
                return {
                    "schema_version": "ampgent.autoresearch-payload-reference.1",
                    "payload_role": "planner_result",
                    "storage_uri": f"s3://cas/{'9' * 64}",
                    "artifact_sha256": "9" * 64,
                    "size_bytes": 4096,
                    "run_id": RUN_ID,
                    "branch_key": "PBP2a",
                    "iteration_no": 0,
                    "action_count": 1,
                }
            if name == "persist_autoresearch_action_plan":
                return {
                    "schema_version": "ampgent.autoresearch-payload-reference.1",
                    "payload_role": "action_plan",
                    "run_id": RUN_ID,
                    "branch_key": "PBP2a",
                    "iteration_no": 0,
                    "agent_decision_id": DECISION_ID,
                    "action_batch_sha256": "1" * 64,
                    "action_count": 1,
                    "action_ids": [ACTION_ID],
                }
            if name == "execute_autoresearch_action_batch":
                return {
                    "schema_version": "ampgent.autoresearch-payload-reference.1",
                    "payload_role": "generated_action_batch",
                    "storage_uri": f"s3://cas/{'8' * 64}",
                    "artifact_sha256": "8" * 64,
                    "size_bytes": 4096,
                    "run_id": RUN_ID,
                    "iteration_no": 0,
                    "action_batch_sha256": "1" * 64,
                    "result_count": 1,
                }
            if name == "persist_autoresearch_children":
                return {
                    "schema_version": "ampgent.autoresearch-payload-reference.1",
                    "payload_role": "children_receipt",
                    "candidate_count": 1,
                    "score_all_candidate_ids": [CHILD_ID],
                }
            if name == "evaluate_v38_sequence_metric":
                assert "plugin" not in payload
                assert "candidates" not in payload
                return {"plugin": {"name": payload["plugin_name"]}}
            if name == "persist_v38_sequence_metric":
                assert "execution_contract" not in payload
                assert "candidates" not in payload
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
    assert names[0:6] == [
        "mark_run_started",
        "persist_autoresearch_score_all_bundle",
        "plan_autoresearch_actions",
        "persist_autoresearch_action_plan",
        "execute_autoresearch_action_batch",
        "persist_autoresearch_children",
    ]
    assert names.count("evaluate_v38_sequence_metric") == 5
    assert names.count("persist_v38_sequence_metric") == 5
    assert names[-2:] == ["finalize_autoresearch_iteration", "mark_run_succeeded"]
    persistence_calls = [
        item
        for item in first_trace
        if item["activity"]
        in {"persist_autoresearch_children", "persist_v38_sequence_metric"}
    ]
    assert persistence_calls
    assert all(item["heartbeat_timeout"] == timedelta(minutes=5) for item in persistence_calls)
    planner_input = next(
        item["payload"] for item in first_trace if item["activity"] == "plan_autoresearch_actions"
    )
    assert planner_input["hydrate_from_run_spec"] is True
    assert "previous_checkpoint" not in planner_input
    assert "execution_contract" not in planner_input
    assert "archive_policy" not in planner_input
    metric_inputs = [
        item["payload"]
        for item in first_trace
        if item["activity"] in {"evaluate_v38_sequence_metric", "persist_v38_sequence_metric"}
    ]
    assert max(len(json.dumps(item, sort_keys=True)) for item in metric_inputs) < 1_000
    generator_input = next(
        item["payload"]
        for item in first_trace
        if item["activity"] == "execute_autoresearch_action_batch"
    )
    assert "executor" not in generator_input
    assert generator_input["action_plan"]["payload_role"] == "action_plan"
    assert generator_input["temporal_payload_mode"] == "reference_v1"
    loop_payloads = [
        item["payload"]
        for item in first_trace
        if item["activity"]
        in {
            "persist_autoresearch_action_plan",
            "execute_autoresearch_action_batch",
            "persist_autoresearch_children",
            "evaluate_v38_sequence_metric",
            "persist_v38_sequence_metric",
            "finalize_autoresearch_iteration",
        }
    ]
    assert max(len(json.dumps(item, sort_keys=True)) for item in loop_payloads) < 4_000


@pytest.mark.asyncio
async def test_autoresearch_all_duplicate_iteration_stops_without_rescoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request()
    trace: list[dict[str, Any]] = []

    async def fake_execute_activity(name: str, payload: dict[str, Any], **_kwargs: Any) -> Any:
        trace.append({"activity": name, "payload": copy.deepcopy(payload)})
        if name in {"mark_run_started", "mark_run_succeeded"}:
            return None
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
                "requires_generator_gpu": False,
                "action_execution_mode": "cpu_rule_only",
            }
        if name == "execute_autoresearch_rule_action_batch":
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
                "schema_version": "ampgent.autoresearch-children-receipt.2",
                "proposed_child_count": 1,
                "candidate_count": 0,
                "candidates": [],
                "rejected_duplicate_count": 1,
                "rejected_duplicates": [
                    {
                        "action_id": ACTION_ID,
                        "status": "rejected_duplicate",
                        "existing_candidate_id": CHILD_ID,
                        "existing_generation": 0,
                        "requested_generation": 1,
                        "reason": "sequence_already_materialized_in_another_generation",
                    }
                ],
                "iteration_noop": True,
                "stop_reason": "no_unique_children_after_duplicate_rejection",
                "score_all_candidate_count": 0,
                "score_all_candidates": [],
            }
        raise AssertionError(f"unexpected activity: {name}")

    monkeypatch.setattr(workflow_module.workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        workflow_module.workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="autoresearch-duplicate-noop"),
    )

    result = await AutoResearchClosedLoopWorkflow().run(request)

    assert result["status"] == "iteration_noop"
    assert result["stop_reason"] == "no_unique_children_after_duplicate_rejection"
    names = [item["activity"] for item in trace]
    assert names == [
        "mark_run_started",
        "persist_autoresearch_action_plan",
        "execute_autoresearch_rule_action_batch",
        "persist_autoresearch_children",
        "mark_run_succeeded",
    ]
    succeeded_payload = trace[-1]["payload"]
    assert succeeded_payload["durable_counts"] == {
        "action_count": 1,
        "candidate_count": 0,
        "rejected_duplicate_count": 1,
        "evaluation_count": 0,
        "metric_delta_count": 0,
        "archive_version_count": 0,
        "checkpoint_count": 0,
        "replay_count": 0,
    }


def test_autoresearch_worker_registration_is_complete() -> None:
    control_queue, control_activities, workflows = V38_ROLE_CONFIG["autoresearch-control"]
    assert control_queue == "pepagent-autoresearch-control-v1"
    registered = {item.__temporal_activity_definition.name for item in control_activities}
    assert {
        "persist_autoresearch_action_plan",
        "persist_autoresearch_children",
        "finalize_autoresearch_iteration",
        "plan_autoresearch_actions",
        "persist_autoresearch_score_all_bundle",
        "execute_autoresearch_rule_action_batch",
    } <= registered
    assert AutoResearchClosedLoopWorkflow in workflows
    generator_queue, generator_activities, _ = V38_ROLE_CONFIG["autoresearch-generator"]
    assert generator_queue == "pepagent-autoresearch-generator-v1"
    generator_registered = {
        item.__temporal_activity_definition.name for item in generator_activities
    }
    assert "execute_autoresearch_action_batch" in generator_registered
    assert "execute_autoresearch_rule_action_batch" not in generator_registered
    metric_queue, metric_activities, _ = V38_ROLE_CONFIG["autoresearch-metrics"]
    assert metric_queue == "pepagent-autoresearch-metrics-v1"
    assert {item.__temporal_activity_definition.name for item in metric_activities} == {
        "evaluate_v38_sequence_metric"
    }

    _, legacy_control_activities, legacy_workflows = V38_ROLE_CONFIG["v38-control"]
    assert AutoResearchClosedLoopWorkflow not in legacy_workflows
    assert not {item.__temporal_activity_definition.name for item in legacy_control_activities} & {
        "persist_autoresearch_action_plan",
        "persist_autoresearch_score_all_bundle",
        "finalize_autoresearch_iteration",
    }


@pytest.mark.asyncio
async def test_cpu_rule_executor_rejects_a_pepmlm_action() -> None:
    action = PepMLMTargetedAction(
        branch_key="PBP2a",
        generation=1,
        seed=23,
        operator_id="pepmlm-targeted-action-v1",
        operator_release_sha256="a" * 64,
        target_sequence_sha256="c" * 64,
        expected_improvement_metrics=("macrel_amp_probability",),
        protected_metrics=("guruprasad_instability_index",),
        evidence_sha256s=("b" * 64,),
        proposal_mode="de_novo",
        peptide_length=20,
    )
    request = {
        "action_plan": {
            "schema_version": "ampgent.autoresearch-action-plan-receipt.1",
            "run_id": RUN_ID,
            "iteration_no": 0,
            "action_batch_sha256": "1" * 64,
            "action_execution_mode": "cpu_rule_only",
            "actions": [{"runtime_action": action.model_dump(mode="json")}],
        }
    }

    with pytest.raises(ValueError, match="cannot contain PepMLM"):
        await execute_autoresearch_rule_action_batch(request)


def test_autoresearch_request_rejects_partial_score_all_registry() -> None:
    request = _request()
    _validate_request(request)
    request["metric_plugins_by_name"].pop(next(iter(request["metric_plugins_by_name"])))
    with pytest.raises(ValueError, match="plugin registry"):
        _validate_request(request)


def test_autoresearch_request_requires_literal_boolean_cpu_only_contract() -> None:
    request = _request()
    request["planner_provider"]["planner_contract"]["pepmlm_targeted_enabled"] = "false"

    with pytest.raises(ValueError, match="frozen boolean"):
        _validate_request(request)


def test_remote_persistence_history_compaction_is_explicit_and_non_mutating() -> None:
    request = _request()
    request.pop("maximum_iterations_per_workflow_execution")

    compacted = with_remote_persistence_history_compaction(request)

    assert "maximum_iterations_per_workflow_execution" not in request
    assert compacted["maximum_iterations_per_workflow_execution"] == 2
    assert (
        compacted["maximum_iterations_per_workflow_execution"]
        == REMOTE_PERSISTENCE_MAXIMUM_ITERATIONS_PER_WORKFLOW_EXECUTION
    )
    _validate_request(compacted)


def test_remote_persistence_history_compaction_rejects_invalid_interval() -> None:
    with pytest.raises(ValueError, match="continue-as-new interval"):
        with_remote_persistence_history_compaction(_request(), maximum_iterations=0)


def test_autoresearch_request_rejects_cross_branch_seed_bundle() -> None:
    request = _request()
    request["seed_score_bundle_import"] = {
        "bundle_cache_root": r"C:\bounded-cache\score-all",
        "bundle_receipt_path": "bundle.receipt.json",
        "bundle_receipt_sha256": "8" * 64,
        "source_map_receipt_path": "score_source_map.receipt.json",
        "source_map_receipt_sha256": "7" * 64,
        "source_map_storage_uri": f"ssh://example.invalid/cas/{'7' * 64}/map.json",
        "target_key": "FGF2",
    }

    with pytest.raises(ValueError, match="target differs"):
        _validate_request(request)


def test_autoresearch_request_rejects_absolute_seed_member_paths() -> None:
    request = _request()
    request["seed_score_bundle_import"] = {
        "bundle_cache_root": r"C:\bounded-cache\score-all",
        "bundle_receipt_path": r"C:\bounded-cache\score-all\bundle.receipt.json",
        "bundle_receipt_sha256": "8" * 64,
        "source_map_receipt_path": "score_source_map.receipt.json",
        "source_map_receipt_sha256": "7" * 64,
        "source_map_storage_uri": f"ssh://example.invalid/cas/{'7' * 64}/map.json",
        "target_key": "PBP2a",
    }

    with pytest.raises(ValueError, match="unsafe score bundle path"):
        _validate_request(request)
