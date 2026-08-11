"""Integration contract for the formal v37 database/object-store closure.

These tests deliberately exercise the shapes emitted by the production database
projection and the production persistence activities.  They are stronger than
the small synthetic verifier unit tests: a formal run must not be authorized
until every test in this module passes without xfail or monkey-patching core
code.
"""

from __future__ import annotations

import importlib.util
import inspect
import uuid
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_persistence import validate_v37_database_object_replay
from pepagent.workers import v37_activities

ROOT = Path(__file__).resolve().parents[1]


def _load_existing_replay_fixture_module() -> ModuleType:
    """Reuse the frozen synthetic graph builder without making tests a package."""
    path = Path(__file__).with_name("test_v37_persistence.py")
    spec = importlib.util.spec_from_file_location("_v37_replay_fixture", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import machinery guard
        raise RuntimeError("cannot load v37 replay fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIXTURE_MODULE = _load_existing_replay_fixture_module()


def _fixture() -> tuple[dict, dict, dict, dict[str, dict]]:
    return deepcopy(FIXTURE_MODULE._fixture())


def _validate(bundle: tuple[dict, dict, dict, dict[str, dict]]) -> dict:
    manifest, plan, graph, payloads = bundle
    return validate_v37_database_object_replay(
        manifest=manifest,
        plan=plan,
        graph=graph,
        artifact_payloads_by_sha256=payloads,
    )


def _artifact_payload(
    graph: dict,
    payloads: dict[str, dict],
    *,
    logical_id: str,
    role: str,
) -> dict:
    call_id = next(
        str(call["id"])
        for call in graph["tool_calls"]
        if call["input_json"]["v37_logical_id"] == logical_id
    )
    link = next(
        item
        for item in graph["evidence_artifacts"]
        if str(item["tool_call_id"]) == call_id and item["role"] == role
    )
    artifact = next(
        item for item in graph["artifacts"] if item["id"] == link["artifact_id"]
    )
    return payloads[artifact["sha256"]]


def _rehash_artifact(
    graph: dict,
    payloads: dict[str, dict],
    *,
    logical_id: str,
    role: str,
    mutation,
) -> None:
    call_id = next(
        str(call["id"])
        for call in graph["tool_calls"]
        if call["input_json"]["v37_logical_id"] == logical_id
    )
    link = next(
        item
        for item in graph["evidence_artifacts"]
        if str(item["tool_call_id"]) == call_id and item["role"] == role
    )
    artifact = next(
        item for item in graph["artifacts"] if item["id"] == link["artifact_id"]
    )
    old_sha = artifact["sha256"]
    payload = payloads.pop(old_sha)
    mutation(payload)
    new_sha = sha256_json(payload)
    artifact["sha256"] = new_sha
    payloads[new_sha] = payload


def test_formal_closure_invokes_verifier_and_never_hardcodes_success() -> None:
    source = inspect.getsource(v37_activities.persist_v37_final_portfolio_and_replay)
    assert "validate_v37_database_object_replay(" in source
    assert '"exact_database_replay": True' not in source


def test_formal_closure_validates_a_graph_containing_the_replay_call() -> None:
    source = inspect.getsource(v37_activities.persist_v37_final_portfolio_and_replay)
    replay_call = source.index('replay_logical = "v37:replay"')
    graph_projection = source.index("build_database_evidence_graph")
    assert replay_call < graph_projection


def test_replay_accepts_the_real_database_candidate_projection_shape() -> None:
    manifest, plan, graph, payloads = _fixture()
    for candidate in graph["candidates"]:
        metadata = candidate.get("metadata")
        if metadata is None:
            metadata = {
                "generator_id": candidate.pop("generator_id"),
                "generator_seed": candidate.pop("seed"),
                "raw_rank": candidate.pop("raw_rank"),
            }
        raw_rank = metadata["raw_rank"]
        candidate.update(
            {
                "generation": 0,
                "parent_id": None,
                "proposal_rank": raw_rank,
                "status": "generated",
                "generator_call_id": "fixture-generator-call",
                "metadata": metadata,
            }
        )
    assert _validate((manifest, plan, graph, payloads))["exact_replay"] is True


def test_replay_recomputes_risk_exclusion_before_accepting_final_portfolio() -> None:
    manifest, plan, graph, payloads = _fixture()
    candidate_id = graph["candidates"][0]["id"]
    updates = {
        "toxinpred3_label": "Toxin",
        "macrel_hemolysis_label": "high",
    }
    for metric_name, label in updates.items():
        logical_id = next(
            metric["logical_id"]
            for metric in plan["metric_calls"]
            if metric_name in metric["metric_names"]
        )
        for evaluation in graph["evaluations"]:
            if (
                evaluation["candidate_id"] == candidate_id
                and evaluation["metric_name"] == metric_name
            ):
                evaluation["text_value"] = label
        _rehash_artifact(
            graph,
            payloads,
            logical_id=logical_id,
            role="evaluation_vector",
            mutation=lambda payload, name=metric_name, value=label: [
                row.update({"text_value": value})
                for row in payload["evaluations"]
                if row["candidate_id"] == candidate_id and row["metric_name"] == name
            ],
        )
    with pytest.raises(ValueError, match="risk|excluded|portfolio"):
        _validate((manifest, plan, graph, payloads))


def test_replay_recomputes_pareto_maximin_lane_selection() -> None:
    manifest, plan, graph, payloads = _fixture()
    replacement = _artifact_payload(
        graph,
        payloads,
        logical_id="v37:stage1-shortlist",
        role="shortlist_manifest",
    )["candidate_ids"][1]
    _rehash_artifact(
        graph,
        payloads,
        logical_id="v37:final-portfolio",
        role="final_portfolio",
        mutation=lambda payload: payload.update({"candidate_ids": [replacement]}),
    )
    with pytest.raises(ValueError, match="Pareto|maximin|lane|selection"):
        _validate((manifest, plan, graph, payloads))


@pytest.mark.parametrize(
    "role",
    ("pareto_layers", "diversity_witness", "shortfall_witness"),
)
def test_replay_rejects_tampered_selection_witnesses(role: str) -> None:
    manifest, plan, graph, payloads = _fixture()
    _rehash_artifact(
        graph,
        payloads,
        logical_id="v37:final-portfolio",
        role=role,
        mutation=lambda payload: payload.update({"tampered": True}),
    )
    with pytest.raises(ValueError, match="witness|Pareto|selection"):
        _validate((manifest, plan, graph, payloads))


@pytest.mark.parametrize(
    ("mutation", "match"),
    (
        (
            lambda graph: graph["evidence_artifacts"].__setitem__(
                slice(None),
                [
                    link
                    for link in graph["evidence_artifacts"]
                    if link["role"] != "attempt_ledger"
                ],
            ),
            "artifact roles|attempt",
        ),
        (
            lambda graph: graph["evidence_artifacts"].__setitem__(
                slice(None),
                [
                    link
                    for link in graph["evidence_artifacts"]
                    if link["role"] != "failure_ledger"
                ],
            ),
            "artifact roles|failure",
        ),
        (lambda graph: graph["agent_decisions"].pop(), "AgentDecision"),
        (
            lambda graph: graph["lifecycle_events"].__setitem__(
                slice(None),
                [
                    event
                    for event in graph["lifecycle_events"]
                    if event["event_type"] != "v37.stage_stopped"
                ],
            ),
            "stop/failure",
        ),
    ),
)
def test_replay_requires_attempt_failure_decision_and_stop_evidence(
    mutation, match: str
) -> None:
    manifest, plan, graph, payloads = _fixture()
    mutation(graph)
    with pytest.raises(ValueError, match=match):
        _validate((manifest, plan, graph, payloads))


def test_replay_rejects_extra_metric_rows_not_declared_by_the_plugin() -> None:
    manifest, plan, graph, payloads = _fixture()
    metric_call = next(
        call
        for call in graph["tool_calls"]
        if call["input_json"]["v37_logical_id"].startswith("v37:metric:")
    )
    graph["evaluations"].append(
        {
            "id": "extra-evaluation",
            "candidate_id": graph["candidates"][0]["id"],
            "tool_call_id": metric_call["id"],
            "metric_name": "undeclared_extra_metric",
            "numeric_value": 1.0,
            "text_value": None,
            "unit": None,
            "status": "succeeded",
            "out_of_domain": False,
            "limitations": [],
            "raw": {"source": "negative-test"},
        }
    )
    with pytest.raises(ValueError, match="extra|plugin|metric coverage"):
        _validate((manifest, plan, graph, payloads))


class _StageOneSession:
    def __init__(self, rows: list[SimpleNamespace], calls: list[SimpleNamespace]) -> None:
        self._results = iter((rows, calls))

    async def scalars(self, _query):
        return next(self._results)


@pytest.mark.asyncio
async def test_formal_stage1_rejects_observations_persisted_by_the_wrong_plugin() -> None:
    manifest, _plan, _graph, _payloads = _fixture()
    candidate_id = uuid.uuid4()
    rows = []
    for metric_name in manifest["stage_1_sequence_evaluation"]["required_metric_names"]:
        label = {
            "toxinpred3_label": "Non-Toxin",
            "macrel_hemolysis_label": "low",
        }.get(metric_name)
        rows.append(
            SimpleNamespace(
                candidate_id=candidate_id,
                metric_name=metric_name,
                numeric_value=None if label else 1.0,
                text_value=label,
                status="succeeded",
                out_of_domain=False,
                tool_call_id="all-observations-wrongly-owned-by-one-plugin",
            )
        )
    calls = [
        SimpleNamespace(input_json={"v37_logical_id": f"v37:metric:{plugin['name']}"})
        for plugin in manifest["stage_1_sequence_evaluation"]["metric_plugins"]
    ]
    with pytest.raises(ValueError, match="plugin|ToolCall|ownership"):
        await v37_activities._validate_stage1_observations(
            _StageOneSession(rows, calls),
            run_id=uuid.uuid4(),
            candidate_ids=[candidate_id],
            manifest=manifest,
        )


def test_formal_structure_summary_enforces_exact_three_by_sixteen_coverage() -> None:
    source = inspect.getsource(v37_activities.persist_v37_structure_stage_summaries)
    assert "poses_per_candidate" in source
    assert "rosetta_decoys_per_pose" in source
    assert "pose coverage" in source.lower()
    assert "decoy coverage" in source.lower()


def test_replay_rejects_a_single_missing_decoy_from_three_by_sixteen() -> None:
    manifest, plan, graph, payloads = _fixture()
    _rehash_artifact(
        graph,
        payloads,
        logical_id="v37:rosetta",
        role="decoy_manifest",
        mutation=lambda payload: payload["decoys"].pop(),
    )
    with pytest.raises(ValueError, match="decoy coverage"):
        _validate((manifest, plan, graph, payloads))


def test_final_idempotent_recovery_still_runs_database_object_replay() -> None:
    source = inspect.getsource(v37_activities.persist_v37_final_portfolio_and_replay)
    assert source.count("_persist_v37_node(") == 1
    assert "_get_or_create_pending_v37_replay_call(" in source
    assert "_complete_v37_replay_call(" in source
    assert "allow_incomplete_replay=True" in source
    assert "validate_v37_database_object_replay(" in source
    assert source.index('replay_logical = "v37:replay"') < source.index(
        "build_database_evidence_graph"
    )


def test_formal_provider_and_external_metric_launches_use_the_typed_guard() -> None:
    module_source = inspect.getsource(v37_activities)
    metric_source = inspect.getsource(v37_activities.evaluate_v37_sequence_metric)
    knowledge_source = inspect.getsource(v37_activities.run_and_persist_v37_knowledge)
    pepshot_source = inspect.getsource(v37_activities.run_and_persist_v37_pepshot)
    assert "async def _run_process" not in module_source
    assert "build_external_metric_plan(" in metric_source
    assert "materialize_external_metric_input" in metric_source
    assert "consume_external_metric_result(" in metric_source
    assert "_run_guarded_generic_runtime(" in metric_source
    assert "_run_guarded_generic_runtime(" in knowledge_source
    assert pepshot_source.count("_run_guarded_generic_runtime(") == 2
    assert "v37_runtime_receipts" in knowledge_source
    assert "v37_runtime_receipts" in pepshot_source
