from __future__ import annotations

import uuid
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from alembic import command
from alembic.config import Config

from pepagent.db.base import Base
from pepagent.db.models import (
    AgentDecision,
    Artifact,
    AutoResearchAction,
    AutoResearchArchiveMembership,
    AutoResearchArchiveVersion,
    AutoResearchCheckpoint,
    Candidate,
    CandidateLineageEdge,
    Evaluation,
    ExperimentRun,
    RunStageCheckpoint,
    ToolCall,
)
from pepagent.db.repository import (
    ExperimentRepository,
    _build_metric_comparison,
    _derive_archive_membership_changes,
    _normalize_autoresearch_lineage_sources,
    _validate_autoresearch_action_contract,
    _validate_score_all_counts,
)
from pepagent.domain.enums import EvaluationStatus

ROOT = Path(__file__).resolve().parents[1]


class _RepositorySession:
    def __init__(self) -> None:
        self.objects: dict[tuple[type[Any], uuid.UUID], Any] = {}
        self.scalar_values: list[Any] = []
        self.scalars_values: list[list[Any]] = []
        self.added: list[Any] = []
        self.flush_count = 0

    async def get(self, model: type[Any], identity: uuid.UUID, **_kwargs: Any) -> Any:
        return self.objects.get((model, identity))

    async def scalar(self, _query: Any) -> Any:
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def scalars(self, _query: Any) -> list[Any]:
        return self.scalars_values.pop(0) if self.scalars_values else []

    def add(self, row: Any) -> None:
        self.added.append(row)

    def add_all(self, rows: list[Any]) -> None:
        self.added.extend(rows)

    async def flush(self) -> None:
        self.flush_count += 1
        for row in self.added:
            if hasattr(row, "id") and row.id is None:
                row.id = uuid.uuid4()


def _candidate(run_id: uuid.UUID, generation: int, sequence: str) -> Candidate:
    return Candidate(
        id=uuid.uuid4(),
        run_id=run_id,
        sequence=sequence,
        sequence_sha256=(sequence[0].lower() * 64),
        generation=generation,
        parent_id=None,
        status="generated",
        proposal_rank=1,
        generator_call_id=None,
        metadata_json={},
    )


def _action(run_id: uuid.UUID, iteration_no: int, kind: str) -> AutoResearchAction:
    return AutoResearchAction(
        id=uuid.uuid4(),
        run_id=run_id,
        iteration_no=iteration_no,
        branch_key="PBP2a",
        action_ordinal=1,
        action_kind=kind,
        random_seed=17,
        agent_decision_id=uuid.uuid4(),
        rationale_text="explore a distinct activity-safety frontier",
        expected_objectives_json=["activity"],
        forbidden_changes_json=["instability>=50"],
        action_spec_json={"operations": [{"op": kind}], "sources": []},
        action_sha256="a" * 64,
    )


def test_autoresearch_schema_has_typed_replay_tables_and_score_all_constraints() -> None:
    expected = {
        "autoresearch_actions",
        "candidate_lineage_edges",
        "autoresearch_metric_deltas",
        "autoresearch_archive_versions",
        "autoresearch_archive_memberships",
        "autoresearch_checkpoints",
    }
    assert expected <= set(Base.metadata.tables)

    checkpoint = Base.metadata.tables["autoresearch_checkpoints"]
    constraint_names = {constraint.name for constraint in checkpoint.constraints}
    assert any(
        name and name.endswith("autoresearch_checkpoint_expected_score_all_count")
        for name in constraint_names
    )
    assert any(
        name and name.endswith("autoresearch_checkpoint_complete_score_all")
        for name in constraint_names
    )
    assert checkpoint.c.replay_artifact_id.foreign_keys


def test_autoresearch_migration_compiles_all_typed_tables_offline() -> None:
    output = StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)

    command.upgrade(
        config,
        "0015_multitarget_structure_evidence:0016_autoresearch_evidence",
        sql=True,
    )

    sql = output.getvalue()
    for table_name in (
        "autoresearch_actions",
        "candidate_lineage_edges",
        "autoresearch_metric_deltas",
        "autoresearch_archive_versions",
        "autoresearch_archive_memberships",
        "autoresearch_checkpoints",
    ):
        assert f"CREATE TABLE {table_name}" in sql
    assert "score_all_candidate_count * score_all_required_metric_count" in sql


def test_action_contract_is_executable_and_fail_closed() -> None:
    parent_id, donor_id = uuid.uuid4(), uuid.uuid4()
    observed = _validate_autoresearch_action_contract(
        iteration_no=2,
        branch_key=" PBP2a ",
        action_ordinal=1,
        action_kind="controlled_mix",
        rationale_text="use a stable backbone and an active donor",
        expected_objectives=["activity", "family novelty"],
        forbidden_changes=["instability>=50"],
        action_spec={
            "operations": [{"op": "replace_span", "start": 4, "end": 7}],
            "sources": [
                {"candidate_id": parent_id, "relation_role": "backbone"},
                {"candidate_id": donor_id, "relation_role": "donor"},
            ],
        },
    )
    assert observed[:2] == ("PBP2a", "controlled_mix")
    assert observed[4]["sources"][0]["candidate_id"] == str(parent_id)

    with pytest.raises(ValueError, match="require source candidates"):
        _validate_autoresearch_action_contract(
            iteration_no=2,
            branch_key="PBP2a",
            action_ordinal=1,
            action_kind="point_edit",
            rationale_text="improve stability",
            expected_objectives=["stability"],
            forbidden_changes=["toxicity"],
            action_spec={"operations": [{"op": "substitute", "site": 3}]},
        )


def test_lineage_contract_captures_all_crossover_sources_and_de_novo_origin() -> None:
    parent_id, donor_id = uuid.uuid4(), uuid.uuid4()
    sources = _normalize_autoresearch_lineage_sources(
        "controlled_mix",
        [
            {
                "parent_candidate_id": parent_id,
                "relation_role": "backbone",
                "source_spans": [{"child": [1, 8], "source": [1, 8]}],
            },
            {
                "parent_candidate_id": donor_id,
                "relation_role": "donor",
                "source_spans": [{"child": [9, 12], "source": [4, 7]}],
            },
        ],
    )
    assert [row["source_ordinal"] for row in sources] == [1, 2]
    assert [row["parent_candidate_id"] for row in sources] == [parent_id, donor_id]
    assert _normalize_autoresearch_lineage_sources("de_novo", [])[0] == {
        "parent_candidate_id": None,
        "relation_role": "de_novo_origin",
        "source_ordinal": 1,
        "source_spans": [],
        "metadata": {},
    }

    with pytest.raises(ValueError, match="must be unique"):
        _normalize_autoresearch_lineage_sources(
            "controlled_mix",
            [
                {"parent_candidate_id": parent_id, "relation_role": "backbone"},
                {"parent_candidate_id": parent_id, "relation_role": "donor"},
            ],
        )


@pytest.mark.asyncio
async def test_archive_version_allows_an_explicitly_empty_frontier() -> None:
    run_id = uuid.uuid4()
    call_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    run = ExperimentRun(
        id=run_id,
        target_id=uuid.uuid4(),
        spec_json={},
        spec_sha256="0" * 64,
        status="running",
    )
    session = _RepositorySession()
    session.objects[(ExperimentRun, run_id)] = run
    session.objects[(ToolCall, call_id)] = SimpleNamespace(
        run_id=run_id,
        status=EvaluationStatus.SUCCEEDED,
    )
    session.objects[(Artifact, snapshot_id)] = SimpleNamespace(sha256="1" * 64)
    session.scalar_values = [None]
    repository = ExperimentRepository(session)  # type: ignore[arg-type]
    repository.append_event = AsyncMock()  # type: ignore[method-assign]

    version = await repository.record_autoresearch_archive_version(
        run_id=run_id,
        iteration_no=0,
        branch_key="PBP2a",
        archive_name="model_disagreement",
        previous_version_id=None,
        policy_sha256="2" * 64,
        tool_call_id=call_id,
        snapshot_artifact_id=snapshot_id,
        memberships=[],
    )

    assert isinstance(version, AutoResearchArchiveVersion)
    assert not any(
        isinstance(row, AutoResearchArchiveMembership) for row in session.added
    )


def test_metric_delta_preserves_conflict_direction_and_ood_context() -> None:
    parent = Evaluation(
        id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        tool_call_id=uuid.uuid4(),
        metric_name="mic_amp_read",
        numeric_value=8.0,
        text_value=None,
        unit="uM",
        status=EvaluationStatus.SUCCEEDED,
        out_of_domain=False,
        limitations_json=[],
        raw_json={},
    )
    child = Evaluation(
        id=uuid.uuid4(),
        candidate_id=uuid.uuid4(),
        tool_call_id=uuid.uuid4(),
        metric_name="mic_amp_read",
        numeric_value=5.0,
        text_value=None,
        unit="uM",
        status=EvaluationStatus.SUCCEEDED,
        out_of_domain=True,
        limitations_json=["short peptide OOD"],
        raw_json={},
    )
    kind, direction, delta, improved, comparison = _build_metric_comparison(
        parent, child, "minimize"
    )
    assert (kind, direction, delta, improved) == (
        "numeric_delta",
        "minimize",
        -3.0,
        True,
    )
    assert comparison["child"]["out_of_domain"] is True


def test_archive_transition_and_score_all_checkpoint_are_complete() -> None:
    retained, removed, added = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    changes = _derive_archive_membership_changes({retained, removed}, {retained, added})
    assert changes == {removed: "remove", retained: "retain", added: "add"}
    assert _validate_score_all_counts(20, 12, 240) == 240
    with pytest.raises(ValueError, match="cannot close"):
        _validate_score_all_counts(20, 12, 239)


@pytest.mark.asyncio
async def test_repository_records_idempotent_action_and_multi_parent_lineage() -> None:
    run_id = uuid.uuid4()
    decision_id = uuid.uuid4()
    run = ExperimentRun(
        id=run_id,
        target_id=uuid.uuid4(),
        spec_json={},
        spec_sha256="0" * 64,
        status="running",
    )
    decision = AgentDecision(
        id=decision_id,
        run_id=run_id,
        generation=0,
        decision_type="autoresearch_action",
        agent_name="agent",
        agent_version="1",
        model_name="model",
        prompt_text="prompt",
        response_text="response",
        prompt_sha256="1" * 64,
        response_sha256="2" * 64,
        structured_json={},
        status="succeeded",
    )
    session = _RepositorySession()
    session.objects[(ExperimentRun, run_id)] = run
    session.objects[(AgentDecision, decision_id)] = decision
    parent = _candidate(run_id, 0, "KRWAA")
    donor = _candidate(run_id, 0, "GLLKK")
    child = _candidate(run_id, 1, "KRWKK")
    for candidate in (parent, donor, child):
        session.objects[(Candidate, candidate.id)] = candidate
    repository = ExperimentRepository(session)  # type: ignore[arg-type]
    repository.append_event = AsyncMock()  # type: ignore[method-assign]

    frozen_sources = [
        {
            "candidate_id": parent.id,
            "relation_role": "backbone",
            "source_spans": [{"child": [1, 3], "source": [1, 3]}],
        },
        {
            "candidate_id": donor.id,
            "relation_role": "donor",
            "source_spans": [{"child": [4, 5], "source": [4, 5]}],
        },
    ]

    action = await repository.record_autoresearch_action(
        run_id=run_id,
        iteration_no=0,
        branch_key="PBP2a",
        action_ordinal=1,
        action_kind="controlled_mix",
        random_seed=17,
        agent_decision_id=decision_id,
        rationale_text="combine complementary non-dominated ends",
        expected_objectives=["activity", "stability"],
        forbidden_changes=["instability>=50"],
        action_spec={
            "operations": [{"op": "replace_span"}],
            "sources": frozen_sources,
        },
    )
    assert action.action_sha256

    session.objects[(AutoResearchAction, action.id)] = action
    session.scalars_values = [[], [], []]
    edges = await repository.record_candidate_lineage(
        action_id=action.id,
        child_candidate_id=child.id,
        sources=[
            {
                "parent_candidate_id": parent.id,
                "relation_role": "backbone",
                "source_spans": [{"child": [1, 3], "source": [1, 3]}],
            },
            {
                "parent_candidate_id": donor.id,
                "relation_role": "donor",
                "source_spans": [{"child": [4, 5], "source": [4, 5]}],
            },
        ],
    )
    assert [(edge.relation_role, edge.parent_candidate_id) for edge in edges] == [
        ("backbone", parent.id),
        ("donor", donor.id),
    ]
    assert all(isinstance(edge, CandidateLineageEdge) for edge in edges)

    session.scalar_values = [action]
    retried = await repository.record_autoresearch_action(
        run_id=run_id,
        iteration_no=0,
        branch_key="PBP2a",
        action_ordinal=1,
        action_kind="controlled_mix",
        random_seed=17,
        agent_decision_id=decision_id,
        rationale_text="combine complementary non-dominated ends",
        expected_objectives=["activity", "stability"],
        forbidden_changes=["instability>=50"],
        action_spec={
            "operations": [{"op": "replace_span"}],
            "sources": frozen_sources,
        },
    )
    assert retried is action

    session.scalar_values = [action]
    with pytest.raises(ValueError, match="retry payload drifted"):
        await repository.record_autoresearch_action(
            run_id=run_id,
            iteration_no=0,
            branch_key="PBP2a",
            action_ordinal=1,
            action_kind="controlled_mix",
            random_seed=18,
            agent_decision_id=decision_id,
            rationale_text="combine complementary non-dominated ends",
            expected_objectives=["activity", "stability"],
            forbidden_changes=["instability>=50"],
            action_spec={
                "operations": [{"op": "replace_span"}],
                "sources": frozen_sources,
            },
        )


@pytest.mark.asyncio
async def test_repository_persists_comparable_delta_archive_and_checkpoint_receipt() -> None:
    run_id = uuid.uuid4()
    run = ExperimentRun(
        id=run_id,
        target_id=uuid.uuid4(),
        spec_json={},
        spec_sha256="0" * 64,
        status="running",
    )
    action = _action(run_id, 0, "point_edit")
    parent = _candidate(run_id, 0, "KRWAA")
    child = _candidate(run_id, 1, "KRWKA")
    lineage = CandidateLineageEdge(
        id=uuid.uuid4(),
        action_id=action.id,
        child_candidate_id=child.id,
        parent_candidate_id=parent.id,
        relation_role="primary_parent",
        source_ordinal=1,
        source_spans_json=[{"child": [4, 4], "source": [4, 4]}],
        edge_sha256="e" * 64,
        metadata_json={},
    )
    parent_call_id, child_call_id = uuid.uuid4(), uuid.uuid4()
    parent_evaluation = Evaluation(
        id=uuid.uuid4(),
        candidate_id=parent.id,
        tool_call_id=parent_call_id,
        metric_name="mic_amp_read",
        numeric_value=8.0,
        text_value=None,
        unit="uM",
        status=EvaluationStatus.SUCCEEDED,
        out_of_domain=False,
        limitations_json=[],
        raw_json={},
    )
    child_evaluation = Evaluation(
        id=uuid.uuid4(),
        candidate_id=child.id,
        tool_call_id=child_call_id,
        metric_name="mic_amp_read",
        numeric_value=5.0,
        text_value=None,
        unit="uM",
        status=EvaluationStatus.SUCCEEDED,
        out_of_domain=False,
        limitations_json=[],
        raw_json={},
    )
    frozen_call = {
        "tool_name": "AMP-READ",
        "tool_version": "1",
        "model_uri": "s3://models/amp-read",
        "weights_sha256": "4" * 64,
        "environment_sha256": "5" * 64,
    }
    session = _RepositorySession()
    for model, row in (
        (ExperimentRun, run),
        (AutoResearchAction, action),
        (Candidate, parent),
        (Candidate, child),
        (Evaluation, parent_evaluation),
        (Evaluation, child_evaluation),
    ):
        session.objects[(model, row.id)] = row
    session.objects[(ToolCall, parent_call_id)] = SimpleNamespace(**frozen_call)
    session.objects[(ToolCall, child_call_id)] = SimpleNamespace(**frozen_call)
    session.scalar_values = [lineage, None]
    repository = ExperimentRepository(session)  # type: ignore[arg-type]
    repository.append_event = AsyncMock()  # type: ignore[method-assign]

    delta = await repository.record_autoresearch_metric_delta(
        action_id=action.id,
        child_candidate_id=child.id,
        comparator_candidate_id=parent.id,
        metric_name="mic_amp_read",
        parent_evaluation_id=parent_evaluation.id,
        child_evaluation_id=child_evaluation.id,
        direction="minimize",
    )
    assert delta.numeric_delta == -3.0
    assert delta.improved is True

    archive_call_id, snapshot_id = uuid.uuid4(), uuid.uuid4()
    session.objects[(ToolCall, archive_call_id)] = SimpleNamespace(
        run_id=run_id, status=EvaluationStatus.SUCCEEDED
    )
    session.objects[(Artifact, snapshot_id)] = SimpleNamespace(sha256="6" * 64)
    session.scalar_values = [None]
    version = await repository.record_autoresearch_archive_version(
        run_id=run_id,
        iteration_no=0,
        branch_key="PBP2a",
        archive_name="multi_frontier",
        previous_version_id=None,
        policy_sha256="7" * 64,
        tool_call_id=archive_call_id,
        snapshot_artifact_id=snapshot_id,
        memberships=[
            {
                "candidate_id": child.id,
                "change_kind": "add",
                "is_active": True,
                "member_ordinal": 1,
                "source_action_id": action.id,
                "reason": "new activity-stability frontier member",
                "witness_candidate_ids": [parent.id],
            }
        ],
    )
    assert isinstance(version, AutoResearchArchiveVersion)
    persisted_memberships = [
        row for row in session.added if isinstance(row, AutoResearchArchiveMembership)
    ]
    assert len(persisted_memberships) == 1
    assert persisted_memberships[0].change_kind == "add"

    decision_id, stage_id, replay_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    decision = AgentDecision(
        id=decision_id,
        run_id=run_id,
        generation=0,
        decision_type="autoresearch_continue",
        agent_name="agent",
        agent_version="1",
        model_name="model",
        prompt_text="prompt",
        response_text="continue",
        prompt_sha256="8" * 64,
        response_sha256="9" * 64,
        structured_json={},
        status="succeeded",
    )
    session.objects[(AgentDecision, decision_id)] = decision
    session.objects[(RunStageCheckpoint, stage_id)] = SimpleNamespace(
        run_id=run_id,
        stage_name="autoresearch.iteration",
        stage_status="completed",
        receipt_sha256="a" * 64,
    )
    session.objects[(Artifact, replay_id)] = SimpleNamespace(sha256="b" * 64)
    session.scalar_values = [None]
    checkpoint = await repository.record_autoresearch_checkpoint(
        run_id=run_id,
        iteration_no=0,
        run_stage_checkpoint_id=stage_id,
        agent_decision_id=decision_id,
        action_batch_sha256="c" * 64,
        archive_before_sha256="d" * 64,
        archive_after_sha256="6" * 64,
        score_all_candidate_count=2,
        score_all_required_metric_count=12,
        score_all_completed_evaluation_count=24,
        next_controller_action="continue",
        replay_artifact_id=replay_id,
    )
    assert isinstance(checkpoint, AutoResearchCheckpoint)
    assert checkpoint.score_all_expected_evaluation_count == 24
    assert checkpoint.replay_verified is True
    assert len(checkpoint.receipt_sha256) == 64
