from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from pepagent.api.main import app
from pepagent.api.observer import (
    AUTORESEARCH_DE_NOVO_V2_OPERATOR_ID,
    HISTORICAL_EXACT_REPLAY,
    _autoresearch_operator_id,
    _candidate_pool_generation_narrative,
    _display_eligible,
    _display_population,
    _generation_population,
    _generation_population_for_run,
    _generation_quality_gate_payload,
    _generation_quality_gates_for_runs,
    _historical_exact_replay_exists,
    _run_identity_payload,
    _run_status_payload,
    _temporal_identity_matches,
    _temporal_observability_for_runs,
    _temporal_observability_from_evidence,
    get_observer_node,
    get_observer_run,
    list_observer_runs,
)
from pepagent.db.models import Candidate, ExperimentRun


def _postgresql_sql(clause: object) -> str:
    return str(
        select(Candidate.id)
        .where(clause)
        .compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_historical_replay_predicate_excludes_only_generated_cross_run_replays() -> None:
    sql = _postgresql_sql(_historical_exact_replay_exists(Candidate))

    assert "candidates.generation > 0" in sql
    assert "candidates_1.run_id != candidates.run_id" in sql
    assert "candidates_1.sequence_sha256 = candidates.sequence_sha256" in sql
    assert "candidates_1.created_at < candidates.created_at" in sql
    assert "candidates_1.id < candidates.id" in sql


def test_display_eligible_is_the_exact_negation_of_historical_replay() -> None:
    replay_sql = _postgresql_sql(_historical_exact_replay_exists(Candidate))
    display_sql = _postgresql_sql(_display_eligible(Candidate))

    assert "NOT" in display_sql
    for fragment in (
        "candidates.generation > 0",
        "candidates_1.run_id != candidates.run_id",
        "candidates_1.sequence_sha256 = candidates.sequence_sha256",
    ):
        assert fragment in replay_sql
        assert fragment in display_sql
    assert HISTORICAL_EXACT_REPLAY == "historical_exact_replay"


def test_display_population_uses_the_eligible_count_as_its_primary_denominator() -> None:
    population = _display_population(candidate_record_count=772, excluded_candidate_count=5)

    assert population == {
        "candidate_count": 767,
        "candidate_record_count": 772,
        "excluded_candidate_count": 5,
        "exclusion_reason": "historical_exact_replay",
    }


def test_generation_population_has_stable_baseline_descendant_contract() -> None:
    assert _generation_population(768, 0, 0) == {
        "baseline_candidate_count": 768,
        "descendant_candidate_count": 0,
        "max_generation": 0,
    }
    assert _generation_population(768, 41, 3) == {
        "baseline_candidate_count": 768,
        "descendant_candidate_count": 41,
        "max_generation": 3,
    }
    assert _generation_population(0, 0, None) == {
        "baseline_candidate_count": 0,
        "descendant_candidate_count": 0,
        "max_generation": 0,
    }


def test_candidate_pool_narrative_does_not_call_generation_zero_new_descendants() -> None:
    baseline_only = _candidate_pool_generation_narrative(_generation_population(768, 0, 0))
    mixed = _candidate_pool_generation_narrative(_generation_population(768, 41, 3))

    assert "768 条候选均为基线候选" in baseline_only
    assert "尚无新生子代" in baseline_only
    assert "新生优秀肽" not in baseline_only
    assert "768 条基线候选" in mixed
    assert "41 条新生子代" in mixed
    assert "generation=3" in mixed


def test_generation_contract_uses_display_eligible_population_everywhere() -> None:
    population_source = inspect.getsource(_generation_population_for_run)
    list_source = inspect.getsource(list_observer_runs)
    detail_source = inspect.getsource(get_observer_run)
    node_source = inspect.getsource(get_observer_node)

    assert "_display_eligible(Candidate)" in population_source
    assert "Candidate.generation == 0" in population_source
    assert "Candidate.generation > 0" in population_source
    assert '"generation_population"' in list_source
    assert '"generation_population": generation_population' in detail_source
    assert '"generation": candidate.generation' in detail_source
    assert 'node_id == "candidate_pool"' in node_source
    assert '"generation_population": generation_population' in node_source
    assert "_candidate_pool_generation_narrative(generation_population)" in node_source


def test_observer_display_contract_routes_are_registered() -> None:
    registered = list(app.routes)
    for route in app.routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            registered.extend(original_router.routes)
    routes = {
        (route.path, tuple(sorted(route.methods or ())))
        for route in registered
        if hasattr(route, "path")
    }

    assert ("/v1/observer/runs", ("GET",)) in routes
    assert ("/v1/observer/runs/{run_id}", ("GET",)) in routes
    assert ("/v1/observer/runs/{run_id}/nodes/{node_id}", ("GET",)) in routes


def test_observer_run_identity_is_row_local_and_explicit_for_list_and_detail() -> None:
    target_id = uuid.uuid4()
    shared_spec = {"name": "same title", "target": "same target"}
    first = ExperimentRun(
        id=uuid.uuid4(),
        target_id=target_id,
        spec_json=shared_spec,
        spec_sha256="a" * 64,
        status="running",
        temporal_workflow_id="workflow-first",
        temporal_run_id="temporal-run-first",
    )
    second = ExperimentRun(
        id=uuid.uuid4(),
        target_id=target_id,
        spec_json=shared_spec,
        spec_sha256="b" * 64,
        status="running",
        temporal_workflow_id="workflow-second",
        temporal_run_id=None,
    )

    assert _run_identity_payload(first) == {
        "id": first.id,
        "workflow_id": "workflow-first",
        "temporal_workflow_id": "workflow-first",
        "temporal_run_id": "temporal-run-first",
    }
    assert _run_identity_payload(second) == {
        "id": second.id,
        "workflow_id": "workflow-second",
        "temporal_workflow_id": "workflow-second",
        "temporal_run_id": None,
    }

    # Both public projections must use the same row-local identity contract.
    assert "_run_identity_payload(row)" in inspect.getsource(list_observer_runs)
    assert "_run_identity_payload(run)" in inspect.getsource(get_observer_run)


def test_generation_quality_gate_has_stable_rules_and_distinct_count_semantics() -> None:
    run_id = uuid.uuid4()
    gate = _generation_quality_gate_payload(run_id)

    assert gate["status"] == "not_applied"
    assert gate["operator_name"] == "autoresearch-rule-de-novo"
    assert gate["operator_version"] == "v2"
    assert gate["proposal_count"] == 0
    assert gate["prefilter_pass_count"] == 0
    assert gate["materialized_descendant_count"] == 0
    assert gate["evaluated_descendant_count"] == 0
    assert gate["count_scope"] == {
        "source": "postgresql",
        "run_id": run_id,
        "operator_id": AUTORESEARCH_DE_NOVO_V2_OPERATOR_ID,
    }
    assert gate["semantics"] == {
        "proposal_and_prefilter_pass_are_not_materialized_descendants": True,
        "materialized_descendant_requires_persisted_lineage_edge": True,
        "evaluated_descendant_requires_persisted_evaluation": True,
        "offline_validation_included": False,
    }
    assert gate["rules"] == [
        {
            "metric_key": "guruprasad_instability_index",
            "comparison": "<",
            "threshold": 50.0,
            "unit": "dimensionless",
        },
        {
            "metric_key": "maximum_hydrophobic_run",
            "comparison": "<=",
            "threshold": 2,
            "unit": "residues",
        },
        {
            "metric_key": "hydrophobic_fraction",
            "comparison": "<=",
            "threshold": 0.45,
            "unit": "fraction",
        },
        {
            "metric_key": "net_charge_ph7_4",
            "comparison": ">=",
            "threshold": 3.0,
            "unit": "elementary_charge",
        },
    ]


def test_generation_quality_gate_is_applied_only_for_exact_persisted_v2_operator() -> None:
    v2_spec = {
        "operations": [
            {"payload": {"operator_id": "autoresearch-rule-de-novo-v2"}}
        ]
    }
    v1_spec = {
        "operations": [
            {"payload": {"operator_id": "autoresearch-rule-de-novo-v1"}}
        ]
    }

    assert _autoresearch_operator_id(v2_spec) == AUTORESEARCH_DE_NOVO_V2_OPERATOR_ID
    assert _autoresearch_operator_id(v1_spec) != AUTORESEARCH_DE_NOVO_V2_OPERATOR_ID
    assert _autoresearch_operator_id(None) is None

    gate = _generation_quality_gate_payload(
        uuid.uuid4(),
        proposal_count=9,
        materialized_descendant_count=7,
        evaluated_descendant_count=5,
    )
    assert gate["status"] == "applied"
    assert gate["proposal_count"] == gate["prefilter_pass_count"] == 9
    assert gate["materialized_descendant_count"] == 7
    assert gate["evaluated_descendant_count"] == 5


def test_generation_quality_gate_queries_are_run_scoped_and_exposed_everywhere() -> None:
    aggregate_source = inspect.getsource(_generation_quality_gates_for_runs)
    list_source = inspect.getsource(list_observer_runs)
    detail_source = inspect.getsource(get_observer_run)
    node_source = inspect.getsource(get_observer_node)

    assert "AutoResearchAction.run_id.in_(run_ids)" in aggregate_source
    assert "Candidate.run_id == AutoResearchAction.run_id" in aggregate_source
    assert "CandidateLineageEdge.action_id == AutoResearchAction.id" in aggregate_source
    assert "Evaluation.candidate_id == Candidate.id" in aggregate_source
    assert "_display_eligible(Candidate)" in aggregate_source
    assert '"generation_quality_gate"' in list_source
    assert '"generation_quality_gate": generation_quality_gate' in detail_source
    assert 'node_id == "candidate_pool"' in node_source
    assert '"generation_quality_gate": generation_quality_gate' in node_source


def test_scientific_status_is_independent_from_temporal_observability() -> None:
    run = ExperimentRun(
        id=uuid.uuid4(),
        target_id=uuid.uuid4(),
        spec_json={},
        spec_sha256="c" * 64,
        status="running",
        temporal_workflow_id="workflow-id",
        temporal_run_id="temporal-run-id",
    )

    payload = _run_status_payload(run)
    assert payload["scientific_run_status"] == {
        "status": "running",
        "source": "postgresql",
        "run_id": run.id,
    }
    assert payload["temporal_observability"] == {
        "status": "unknown",
        "source": "postgresql_operational_evidence",
        "observed_at": None,
        "history_read_status": "not_queried",
        "history_read_error_category": None,
        "scheduler_error_category": None,
        "stale_after_seconds": 300,
        "is_stale": None,
        "postgresql_run_id": run.id,
        "temporal_workflow_id": "workflow-id",
        "temporal_run_id": "temporal-run-id",
        "evidence_type": None,
        "affects_scientific_run_status": False,
    }
    assert "_run_status_payload(row, temporal_observability[row.id])" in inspect.getsource(
        list_observer_runs
    )
    assert "_run_status_payload(run, temporal_observability)" in inspect.getsource(
        get_observer_run
    )


def test_temporal_operational_evidence_requires_exact_three_part_identity() -> None:
    run = ExperimentRun(
        id=uuid.uuid4(),
        target_id=uuid.uuid4(),
        spec_json={},
        spec_sha256="d" * 64,
        status="running",
        temporal_workflow_id="workflow-exact",
        temporal_run_id="run-exact",
    )
    exact = {
        "run_id": str(run.id),
        "workflow_id": "workflow-exact",
        "workflow_run_id": "run-exact",
    }

    assert _temporal_identity_matches(exact, run) is True
    assert _temporal_identity_matches({**exact, "workflow_run_id": "other"}, run) is False
    assert _temporal_identity_matches({**exact, "run_id": str(uuid.uuid4())}, run) is False
    assert _temporal_identity_matches({"run_id": str(run.id)}, run) is False


def test_temporal_history_failure_is_categorized_without_raw_error_text() -> None:
    run = ExperimentRun(
        id=uuid.uuid4(),
        target_id=uuid.uuid4(),
        spec_json={},
        spec_sha256="e" * 64,
        status="running",
        temporal_workflow_id="workflow-exact",
        temporal_run_id="run-exact",
    )
    now = datetime(2026, 8, 31, 0, 10, tzinfo=UTC)
    observed_at = now - timedelta(seconds=301)
    raw_error = "context deadline exceeded at secret-internal-host"
    result = _temporal_observability_from_evidence(
        run,
        evidence_type="temporal.history_read.failed",
        payload={
            "run_id": str(run.id),
            "temporal_workflow_id": run.temporal_workflow_id,
            "temporal_run_id": run.temporal_run_id,
            "error_message": raw_error,
        },
        observed_at=observed_at,
        now=now,
    )

    assert result == {
        "status": "degraded",
        "source": "postgresql_operational_evidence",
        "observed_at": observed_at.isoformat(),
        "history_read_status": "failed",
        "history_read_error_category": "timeout",
        "scheduler_error_category": None,
        "stale_after_seconds": 300,
        "is_stale": True,
        "postgresql_run_id": run.id,
        "temporal_workflow_id": "workflow-exact",
        "temporal_run_id": "run-exact",
        "evidence_type": "temporal.history_read.failed",
        "affects_scientific_run_status": False,
    }
    assert raw_error not in str(result)


def test_activity_timeout_is_scheduler_degradation_not_a_history_read_claim() -> None:
    run = ExperimentRun(
        id=uuid.uuid4(),
        target_id=uuid.uuid4(),
        spec_json={},
        spec_sha256="f" * 64,
        status="failed",
        temporal_workflow_id="workflow-exact",
        temporal_run_id="run-exact",
    )
    now = datetime(2026, 8, 31, 0, 10, tzinfo=UTC)
    result = _temporal_observability_from_evidence(
        run,
        evidence_type="activity.failed",
        payload={
            "run_id": str(run.id),
            "workflow_id": run.temporal_workflow_id,
            "workflow_run_id": run.temporal_run_id,
            "error_type": "Activity task timed out",
        },
        observed_at=now,
        now=now,
    )

    assert result is not None
    assert result["status"] == "degraded"
    assert result["history_read_status"] == "not_queried"
    assert result["history_read_error_category"] is None
    assert result["scheduler_error_category"] == "timeout"
    assert result["affects_scientific_run_status"] is False


def test_temporal_observability_aggregation_is_postgresql_only_and_row_scoped() -> None:
    source = inspect.getsource(_temporal_observability_for_runs)
    list_source = inspect.getsource(list_observer_runs)
    detail_source = inspect.getsource(get_observer_run)

    assert "LifecycleEvent.aggregate_id.in_(runs_by_id)" in source
    assert "ToolCall.run_id.in_(runs_by_id)" in source
    assert "TEMPORAL_OPERATIONAL_TOOL_NAMES" in source
    assert "Client.connect" not in source
    assert "fetch_history" not in source
    assert "_temporal_observability_for_runs(session, rows)" in list_source
    assert "_temporal_observability_for_runs(session, [run])" in detail_source
