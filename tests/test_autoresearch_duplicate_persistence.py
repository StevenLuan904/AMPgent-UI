from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from pepagent.autoresearch_closed_loop import DeNovoAction
from pepagent.db.models import AutoResearchAction, Candidate, ExperimentRun
from pepagent.domain.enums import RunStatus
from pepagent.provenance.hashing import sha256_text
from pepagent.storage.object_store import StoredObject
from pepagent.workers import autoresearch_activities


class _Transaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return False


class _Session:
    def __init__(
        self,
        run: ExperimentRun,
        actions: list[AutoResearchAction],
        candidate_lookups: list[Candidate | None],
    ) -> None:
        self.run = run
        self.actions = {item.id: item for item in actions}
        self.candidate_lookups = list(candidate_lookups)

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return False

    def begin(self) -> _Transaction:
        return _Transaction()

    async def get(self, model: type[Any], identity: uuid.UUID, **kwargs: Any) -> Any:
        if model is ExperimentRun:
            assert kwargs == {"with_for_update": True}
            return self.run
        if model is AutoResearchAction:
            return self.actions.get(identity)
        raise AssertionError(f"unexpected get: {model}")

    async def scalar(self, _query: Any) -> Candidate | None:
        return self.candidate_lookups.pop(0)


class _Repository:
    def __init__(self, run_id: uuid.UUID) -> None:
        self.run_id = run_id
        self.call = SimpleNamespace(id=uuid.uuid4())
        self.occurrences: list[dict[str, Any]] = []
        self.events: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.lineage_action_ids: list[uuid.UUID] = []
        self.added_candidates: list[Candidate] = []

    async def record_completed_tool_call(self, *args: Any, **kwargs: Any) -> Any:
        return self.call

    async def record_agent_tool_edge(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def add_candidate(
        self,
        run_id: uuid.UUID,
        sequence: str,
        generation: int,
        proposal_rank: int,
        generator_call_id: uuid.UUID,
        parent_id: uuid.UUID | None,
        metadata: dict[str, Any],
        actor: str,
    ) -> Candidate:
        assert run_id == self.run_id
        candidate = Candidate(
            id=uuid.uuid4(),
            run_id=run_id,
            sequence=sequence,
            sequence_sha256=sha256_text(sequence),
            generation=generation,
            proposal_rank=proposal_rank,
            generator_call_id=generator_call_id,
            parent_id=parent_id,
            status="generated",
            metadata_json=metadata,
        )
        self.added_candidates.append(candidate)
        return candidate

    async def record_candidate_occurrence(self, **kwargs: Any) -> Any:
        self.occurrences.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    async def record_candidate_lineage(self, **kwargs: Any) -> list[Any]:
        self.lineage_action_ids.append(kwargs["action_id"])
        return [SimpleNamespace(edge_sha256="e" * 64)]

    async def append_event(self, *args: Any, **kwargs: Any) -> Any:
        self.events.append((args, kwargs))
        return SimpleNamespace(id=uuid.uuid4())


def _action(
    run_id: uuid.UUID,
    *,
    ordinal: int,
    sequence: str,
) -> tuple[AutoResearchAction, dict[str, Any]]:
    runtime = DeNovoAction(
        branch_key="VEGFA",
        generation=1,
        seed=40 + ordinal,
        operator_id="agent-de-novo-v1",
        operator_release_sha256="a" * 64,
        expected_improvement_metrics=("macrel_amp_probability",),
        protected_metrics=("guruprasad_instability_index",),
        evidence_sha256s=("b" * 64,),
        peptide_length=len(sequence),
        proposed_sequence=sequence,
    )
    row = AutoResearchAction(
        id=uuid.uuid4(),
        run_id=run_id,
        iteration_no=0,
        branch_key="VEGFA",
        action_ordinal=ordinal,
        action_kind="de_novo",
        random_seed=runtime.seed,
        agent_decision_id=uuid.uuid4(),
        rationale_text="preserve every proposal while scoring only unique children",
        expected_objectives_json=["macrel_amp_probability"],
        forbidden_changes_json=["guruprasad_instability_index"],
        action_spec_json={"operations": [], "sources": []},
        action_sha256=chr(ord("c") + ordinal) * 64,
    )
    plan_item = {
        "action_id": str(row.id),
        "repository_action_sha256": row.action_sha256,
        "runtime_action_sha256": runtime.action_sha256,
        "runtime_action": runtime.model_dump(mode="json"),
        "lineage_sources": [
            {
                "parent_candidate_id": None,
                "relation_role": "de_novo_origin",
                "source_ordinal": 1,
                "source_spans": [],
                "metadata": {},
            }
        ],
    }
    return row, plan_item


def _request(
    run_id: uuid.UUID,
    actions: list[AutoResearchAction],
    plan_items: list[dict[str, Any]],
    sequences: list[str],
) -> dict[str, Any]:
    return {
        "action_plan": {
            "run_id": str(run_id),
            "iteration_no": 0,
            "agent_decision_id": str(uuid.uuid4()),
            "action_batch_sha256": "f" * 64,
            "actions": plan_items,
        },
        "generated": {
            "action_batch_sha256": "f" * 64,
            "parent_controls": [],
            "results": [
                {
                    "action_id": str(action.id),
                    "sequence": sequence,
                    "sequence_sha256": sha256_text(sequence),
                    "executor_action_sha256": plan_item["runtime_action_sha256"],
                }
                for action, plan_item, sequence in zip(
                    actions, plan_items, sequences, strict=True
                )
            ],
            "provenance": {
                "tool_name": "autoresearch-frozen-action-executor",
                "tool_version": "2",
                "environment_sha256": "0" * 64,
                "attempt": 1,
            },
        },
    }


async def _run_persistence(
    monkeypatch: pytest.MonkeyPatch,
    *,
    actions: list[AutoResearchAction],
    plan_items: list[dict[str, Any]],
    sequences: list[str],
    candidate_lookups: list[Candidate | None],
) -> tuple[dict[str, Any], _Repository]:
    run_id = actions[0].run_id
    run = ExperimentRun(
        id=run_id,
        target_id=uuid.uuid4(),
        spec_json={},
        spec_sha256="1" * 64,
        status=RunStatus.RUNNING,
    )
    session = _Session(run, actions, candidate_lookups)
    repository = _Repository(run_id)

    async def fake_store_json(_payload: dict[str, Any]) -> StoredObject:
        return StoredObject(
            sha256="2" * 64,
            size_bytes=1,
            uri="s3://test/object",
            media_type="application/json",
        )

    async def fake_register_artifact(*args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(autoresearch_activities, "SessionFactory", lambda: session)
    monkeypatch.setattr(
        autoresearch_activities,
        "ExperimentRepository",
        lambda _session: repository,
    )
    monkeypatch.setattr(autoresearch_activities, "_store_json", fake_store_json)
    monkeypatch.setattr(
        autoresearch_activities,
        "_register_artifact",
        fake_register_artifact,
    )

    receipt = await autoresearch_activities.persist_autoresearch_children(
        _request(run_id, actions, plan_items, sequences)
    )
    return receipt, repository


@pytest.mark.asyncio
async def test_cross_generation_duplicate_is_audited_and_remaining_child_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid.uuid4()
    duplicate_sequence = "KRWLAKIRKL"
    unique_sequence = "GILKQAKKLG"
    duplicate_action, duplicate_plan = _action(
        run_id,
        ordinal=1,
        sequence=duplicate_sequence,
    )
    unique_action, unique_plan = _action(run_id, ordinal=2, sequence=unique_sequence)
    existing = Candidate(
        id=uuid.uuid4(),
        run_id=run_id,
        sequence=duplicate_sequence,
        sequence_sha256=sha256_text(duplicate_sequence),
        generation=0,
        proposal_rank=1,
        status="generated",
        metadata_json={"source": "seed"},
    )

    receipt, repository = await _run_persistence(
        monkeypatch,
        actions=[duplicate_action, unique_action],
        plan_items=[duplicate_plan, unique_plan],
        sequences=[duplicate_sequence, unique_sequence],
        candidate_lookups=[existing, None],
    )

    assert receipt["candidate_count"] == 1
    assert receipt["rejected_duplicate_count"] == 1
    assert receipt["iteration_noop"] is False
    assert receipt["score_all_candidate_count"] == 1
    assert receipt["score_all_candidates"][0]["sequence"] == unique_sequence
    rejected = receipt["rejected_duplicates"][0]
    assert rejected["status"] == "rejected_duplicate"
    assert rejected["existing_candidate_id"] == str(existing.id)
    assert rejected["existing_generation"] == 0
    assert rejected["requested_generation"] == 1
    assert rejected["reason"] == "sequence_already_materialized_in_another_generation"
    assert repository.lineage_action_ids == [unique_action.id]
    duplicate_occurrence = repository.occurrences[0]
    assert duplicate_occurrence["candidate_id"] == existing.id
    assert duplicate_occurrence["metadata"]["status"] == "rejected_duplicate"
    assert duplicate_occurrence["metadata"]["scientific_output_reused"] is False
    assert duplicate_occurrence["metadata"]["excluded_from_unique_child_cohort"] is True
    assert any(args[2] == "autoresearch.action.rejected_duplicate" for args, _ in repository.events)
    assert not any(args[2] == "autoresearch.iteration.noop" for args, _ in repository.events)


@pytest.mark.asyncio
async def test_cross_run_duplicate_is_audited_without_cross_run_candidate_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid.uuid4()
    historical_run_id = uuid.uuid4()
    sequence = "IRKLKRWLAKIRKLKRWLAK"
    action, plan_item = _action(run_id, ordinal=1, sequence=sequence)
    historical = Candidate(
        id=uuid.uuid4(),
        run_id=historical_run_id,
        sequence=sequence,
        sequence_sha256=sha256_text(sequence),
        generation=2,
        proposal_rank=2_000_001,
        status="generated",
        metadata_json={"source": "historical_autoresearch"},
    )

    receipt, repository = await _run_persistence(
        monkeypatch,
        actions=[action],
        plan_items=[plan_item],
        sequences=[sequence],
        candidate_lookups=[historical],
    )

    assert receipt["candidate_count"] == 0
    assert receipt["rejected_duplicate_count"] == 1
    assert receipt["iteration_noop"] is True
    rejected = receipt["rejected_duplicates"][0]
    assert rejected["reason"] == "sequence_already_materialized_in_historical_run"
    assert rejected["existing_candidate_id"] == str(historical.id)
    assert rejected["existing_run_id"] == str(historical_run_id)
    occurrence = repository.occurrences[0]
    assert occurrence["candidate_id"] is None
    assert occurrence["metadata"]["excluded_from_unique_child_cohort"] is True
    assert not repository.added_candidates


@pytest.mark.asyncio
async def test_all_duplicate_iteration_persists_noop_stop_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid.uuid4()
    sequence = "KRWLAKIRKL"
    action, plan_item = _action(run_id, ordinal=1, sequence=sequence)
    existing = Candidate(
        id=uuid.uuid4(),
        run_id=run_id,
        sequence=sequence,
        sequence_sha256=sha256_text(sequence),
        generation=0,
        proposal_rank=1,
        status="generated",
        metadata_json={"source": "seed"},
    )

    receipt, repository = await _run_persistence(
        monkeypatch,
        actions=[action],
        plan_items=[plan_item],
        sequences=[sequence],
        candidate_lookups=[existing],
    )

    assert receipt["candidate_count"] == 0
    assert receipt["rejected_duplicate_count"] == 1
    assert receipt["iteration_noop"] is True
    assert receipt["stop_reason"] == "no_unique_children_after_duplicate_rejection"
    assert receipt["score_all_candidates"] == []
    assert not repository.added_candidates
    assert any(args[2] == "autoresearch.iteration.noop" for args, _ in repository.events)


@pytest.mark.asyncio
async def test_same_action_same_generation_materialization_is_an_idempotent_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid.uuid4()
    sequence = "KRWLAKIRKL"
    action, plan_item = _action(run_id, ordinal=1, sequence=sequence)
    existing = Candidate(
        id=uuid.uuid4(),
        run_id=run_id,
        sequence=sequence,
        sequence_sha256=sha256_text(sequence),
        generation=1,
        proposal_rank=1_000_001,
        status="generated",
        metadata_json={"autoresearch_action_id": str(action.id)},
    )

    receipt, repository = await _run_persistence(
        monkeypatch,
        actions=[action],
        plan_items=[plan_item],
        sequences=[sequence],
        candidate_lookups=[existing],
    )

    assert receipt["candidate_count"] == 1
    assert receipt["rejected_duplicate_count"] == 0
    assert receipt["candidates"][0]["id"] == str(existing.id)
    assert repository.lineage_action_ids == [action.id]
    assert not any(
        args[2] == "autoresearch.action.rejected_duplicate" for args, _ in repository.events
    )
