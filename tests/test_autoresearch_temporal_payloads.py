from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from pepagent.autoresearch_closed_loop import DeNovoAction
from pepagent.db.models import AgentDecision, AutoResearchAction, Candidate, ExperimentRun
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.workers import autoresearch_activities as activity_module
from pepagent.workers import v37_activities as metric_module
from pepagent.workers.autoresearch_activities import (
    _hydrate_planner_request,
    _resolve_action_plan_reference,
    _resolve_generated_reference,
    _resolve_planner_result_reference,
    build_typed_action_projection,
)

RUN_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
DECISION_ID = uuid.UUID("10000000-0000-0000-0000-000000000002")
ACTION_ID = uuid.UUID("10000000-0000-0000-0000-000000000003")


class _ScalarRows:
    def __init__(self, rows: list[Any]):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _Session:
    def __init__(
        self,
        *,
        run: ExperimentRun | None = None,
        decision: AgentDecision | None = None,
        rows: list[Any] | None = None,
    ):
        self.run = run
        self.decision = decision
        self.rows = rows or []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def get(self, model: type[Any], identity: uuid.UUID) -> Any:
        if model is ExperimentRun:
            return self.run if self.run is not None and self.run.id == identity else None
        if model is AgentDecision:
            return (
                self.decision
                if self.decision is not None and self.decision.id == identity
                else None
            )
        raise AssertionError(model)

    async def scalars(self, _statement: Any) -> _ScalarRows:
        return _ScalarRows(self.rows)


def _action() -> DeNovoAction:
    return DeNovoAction(
        branch_key="PBP2a",
        generation=1,
        seed=7,
        operator_id="agent-de-novo-v1",
        operator_release_sha256="a" * 64,
        expected_improvement_metrics=("macrel_amp_probability",),
        protected_metrics=("guruprasad_instability_index",),
        evidence_sha256s=("b" * 64,),
        peptide_length=10,
        proposed_sequence="KRWLAKIRKL",
    )


@pytest.mark.asyncio
async def test_thin_planner_request_hydrates_only_from_authoritative_run_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_request = {
        "run_id": str(RUN_ID),
        "branch_key": "PBP2a",
        "archive_policy": {"archive_capacity_per_front": 8},
        "continuation_policy": {"minimum_high_quality_candidates": 50},
        "execution_contract": {"metric_plugins": ["physicochemical"]},
        "planner_provider": {"planner_contract": {"de_novo_quota": 0.25}},
        "action_executor": {
            "operator_environment_sha256": "a" * 64,
            "target_sequence_sha256": "b" * 64,
        },
        "control_environment_sha256": "c" * 64,
    }

    async def fake_load(run_id: uuid.UUID) -> dict[str, Any]:
        assert run_id == RUN_ID
        return workflow_request

    monkeypatch.setattr(activity_module, "_load_run_workflow_request", fake_load)
    hydrated = await _hydrate_planner_request(
        {
            "schema_version": "ampgent.autoresearch-planner-request.1",
            "run_id": str(RUN_ID),
            "branch_key": "PBP2a",
            "iteration_no": 2,
            "hydrate_from_run_spec": True,
        }
    )

    assert hydrated["archive_policy"] == workflow_request["archive_policy"]
    assert hydrated["planner_contract"] == {"de_novo_quota": 0.25}
    assert hydrated["operator_release_sha256"] == "a" * 64
    assert hydrated["target_sequence_sha256"] == "b" * 64


@pytest.mark.asyncio
async def test_planner_result_reference_hydrates_exact_cas_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = _action().model_dump(mode="json")
    archive_sha = "c" * 64
    plan = {
        "actions": [action],
        "rationale_by_action_sha256": {action["action_sha256"]: "open a new family"},
        "gold_target": 50,
        "gold_candidate_count": 3,
        "strategies": ["family_novelty"],
    }
    payload = {
        "schema_version": "ampgent.autoresearch-rule-planner-evidence.1",
        "run_id": str(RUN_ID),
        "iteration_no": 0,
        "branch_key": "PBP2a",
        "snapshot": {"archive_sha256": archive_sha},
        "plan": plan,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

    class _Store:
        def get_bytes(self, _uri: str) -> bytes:
            return raw

    monkeypatch.setattr(activity_module, "ContentAddressedObjectStore", _Store)
    reference = {
        "schema_version": "ampgent.autoresearch-payload-reference.1",
        "payload_role": "planner_result",
        "storage_uri": "s3://cas/planner.json",
        "artifact_sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
        "run_id": str(RUN_ID),
        "iteration_no": 0,
        "branch_key": "PBP2a",
        "planner_tool_call_id": str(uuid.uuid4()),
        "artifact_id": str(uuid.uuid4()),
        "archive_sha256": archive_sha,
        "action_count": 1,
    }

    resolved = await _resolve_planner_result_reference(reference)

    assert resolved["actions"] == [action]
    assert resolved["planner_receipt"]["artifact_sha256"] == reference["artifact_sha256"]


@pytest.mark.asyncio
async def test_action_plan_reference_rehydrates_authoritative_database_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = _action()
    projection = build_typed_action_projection(
        action.model_dump(mode="json"),
        iteration_no=0,
        action_ordinal=1,
        rationale_text="open a new family",
    )
    repository_sha = "d" * 64
    row = AutoResearchAction(
        id=ACTION_ID,
        run_id=RUN_ID,
        iteration_no=0,
        branch_key="PBP2a",
        action_ordinal=1,
        action_kind=projection["action_kind"],
        random_seed=projection["random_seed"],
        agent_decision_id=DECISION_ID,
        rationale_text=projection["rationale_text"],
        expected_objectives_json=projection["expected_objectives"],
        forbidden_changes_json=projection["forbidden_changes"],
        action_spec_json=projection["action_spec"],
        action_sha256=repository_sha,
    )
    decision = AgentDecision(
        id=DECISION_ID,
        run_id=RUN_ID,
        generation=0,
        decision_type="autoresearch_action_batch",
        agent_name="planner",
        agent_version="1",
        prompt_text="prompt",
        response_text="response",
        prompt_sha256="e" * 64,
        response_sha256="f" * 64,
        structured_json={"planner_receipt": {"tool_call_id": str(uuid.uuid4())}},
        status="succeeded",
    )
    action_batch_sha = sha256_json(
        {
            "schema_version": "ampgent.autoresearch-action-batch.1",
            "run_id": str(RUN_ID),
            "iteration_no": 0,
            "decision_id": str(DECISION_ID),
            "actions": [
                {
                    "action_id": str(ACTION_ID),
                    "repository_action_sha256": repository_sha,
                    "runtime_action_sha256": action.action_sha256,
                }
            ],
        }
    )
    session = _Session(decision=decision, rows=[row])
    monkeypatch.setattr(activity_module, "SessionFactory", lambda: session)

    resolved = await _resolve_action_plan_reference(
        {
            "schema_version": "ampgent.autoresearch-payload-reference.1",
            "payload_role": "action_plan",
            "run_id": str(RUN_ID),
            "iteration_no": 0,
            "branch_key": "PBP2a",
            "agent_decision_id": str(DECISION_ID),
            "action_batch_sha256": action_batch_sha,
            "action_ids": [str(ACTION_ID)],
        }
    )

    assert resolved["action_batch_sha256"] == action_batch_sha
    assert resolved["actions"][0]["runtime_action"] == action.model_dump(mode="json")


@pytest.mark.asyncio
async def test_generated_action_reference_hydrates_exact_cas_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_sha = "9" * 64
    payload = {
        "schema_version": "ampgent.autoresearch-materialized-action-batch.2",
        "run_id": str(RUN_ID),
        "iteration_no": 0,
        "action_batch_sha256": batch_sha,
        "parent_controls": [],
        "results": [{"action_id": str(ACTION_ID), "sequence": "KRWLAKIRKL"}],
        "provenance": {"attempt": 1},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

    class _Store:
        def get_bytes(self, _uri: str) -> bytes:
            return raw

    monkeypatch.setattr(activity_module, "ContentAddressedObjectStore", _Store)
    reference = {
        "schema_version": "ampgent.autoresearch-payload-reference.1",
        "payload_role": "generated_action_batch",
        "storage_uri": "s3://cas/generated.json",
        "artifact_sha256": sha256_bytes(raw),
        "size_bytes": len(raw),
        "run_id": str(RUN_ID),
        "iteration_no": 0,
        "action_batch_sha256": batch_sha,
        "result_count": 1,
    }

    assert await _resolve_generated_reference(reference) == payload
    drifted = {**reference, "result_count": 2}
    with pytest.raises(ValueError, match="summary drifted"):
        await _resolve_generated_reference(drifted)


@pytest.mark.asyncio
async def test_thin_metric_request_hydrates_plugin_and_preserves_candidate_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    run = ExperimentRun(
        id=RUN_ID,
        target_id=uuid.uuid4(),
        spec_json={
            "workflow_request": {
                "metric_plugins_by_name": {"toxicity_risk": {"name": "toxicity_risk"}}
            }
        },
        spec_sha256="a" * 64,
        status="running",
    )
    first = Candidate(
        id=first_id,
        run_id=RUN_ID,
        sequence="KRWLAKIRKL",
        sequence_sha256="1" * 64,
        generation=1,
        status="generated",
        proposal_rank=1,
        metadata_json={},
    )
    second = Candidate(
        id=second_id,
        run_id=RUN_ID,
        sequence="RWRLKKLAAK",
        sequence_sha256="2" * 64,
        generation=0,
        status="generated",
        proposal_rank=2,
        metadata_json={},
    )
    session = _Session(run=run, rows=[first, second])
    monkeypatch.setattr(metric_module, "SessionFactory", lambda: session)

    hydrated = await metric_module._hydrate_autoresearch_metric_request(
        {
            "run_id": str(RUN_ID),
            "plugin_name": "toxicity_risk",
            "candidate_ids": [str(second_id), str(first_id)],
            "hydrate_from_run_spec": True,
        }
    )

    assert hydrated["plugin"] == {"name": "toxicity_risk"}
    assert [item["id"] for item in hydrated["candidates"]] == [str(second_id), str(first_id)]
