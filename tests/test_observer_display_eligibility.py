from __future__ import annotations

import inspect
import uuid

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from pepagent.api.main import app
from pepagent.api.observer import (
    HISTORICAL_EXACT_REPLAY,
    _display_eligible,
    _display_population,
    _historical_exact_replay_exists,
    _run_identity_payload,
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
