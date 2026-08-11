import ast
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from pepagent.v37_preflight import (
    authorize_v37_submission_preflight,
    build_v37_static_preflight,
)
from pepagent.v37_submit_cli import (
    build_v37_formal_submission_key,
    build_v37_workflow_id,
    ensure_no_existing_v37_run,
    load_v37_submission_bundle,
)
from pepagent.workers import v37_activities
from pepagent.workers.temporal_worker import ROLE_CONFIG
from pepagent.workers.v37_activities import (
    evaluate_v37_sequence_metric,
    predict_v37_boltz2_complex,
    score_v37_rosetta_complex,
)
from pepagent.workers.v37_temporal_worker import V37_ROLE_CONFIG

ROOT = Path(__file__).resolve().parents[1]


def _workflow_calls() -> list[str]:
    source = (ROOT / "src/pepagent/workflows/v37_champion.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not (
            isinstance(function, ast.Attribute)
            and function.attr == "execute_activity"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            continue
        calls.append(str(node.args[0].value))
    return calls


def test_v37_workflow_activity_history_and_queues_are_explicit() -> None:
    calls = _workflow_calls()
    assert len(calls) == 19
    assert len(calls) == len(set(calls))
    assert "generate_v37_batch" in calls
    assert "evaluate_v37_sequence_metric" in calls
    assert "predict_v37_boltz2_complex" in calls
    assert "score_v37_rosetta_complex" in calls
    assert "run_and_persist_v37_pepshot" in calls
    assert "persist_v37_knowledge_projection" in calls
    assert "finalize_v37_run" in calls
    source = (ROOT / "src/pepagent/workflows/v37_champion.py").read_text(encoding="utf-8")
    for role in (
        "workflow_and_control",
        "generator",
        "provider",
        "sequence_metrics",
        "boltz",
        "rosetta",
    ):
        assert f'task_queue=queues["{role}"]' in source


def test_v37_worker_registry_contains_only_callables() -> None:
    assert set(V37_ROLE_CONFIG) == {"v37-control", "v37-generator", "v37-provider"}
    for task_queue, activities, workflows in V37_ROLE_CONFIG.values():
        assert task_queue.startswith("pepagent-")
        assert activities
        assert all(callable(item) for item in activities)
        assert all(inspect.isclass(item) for item in workflows)
    manifest = yaml.safe_load(
        (ROOT / "config/benchmarks/amp_rapid_champion_generation_v37.yaml").read_text(
            encoding="utf-8"
        )
    )
    queues = manifest["execution"]["task_queues"]
    assert V37_ROLE_CONFIG["v37-control"][0] == queues["workflow_and_control"]
    assert V37_ROLE_CONFIG["v37-generator"][0] == queues["generator"]
    assert V37_ROLE_CONFIG["v37-provider"][0] == queues["provider"]
    assert evaluate_v37_sequence_metric in ROLE_CONFIG["metrics"][1]
    assert predict_v37_boltz2_complex in ROLE_CONFIG["boltz2"][1]
    assert score_v37_rosetta_complex in ROLE_CONFIG["rosetta"][1]


def test_v37_physical_wrappers_bind_real_operations_to_attempt_ledgers() -> None:
    source = (ROOT / "src/pepagent/workers/v37_activities.py").read_text(
        encoding="utf-8"
    )
    for activity_name in (
        "evaluate_v37_sequence_metric",
        "predict_v37_boltz2_complex",
        "score_v37_rosetta_complex",
    ):
        function = getattr(v37_activities, activity_name)
        function_source = inspect.getsource(function)
        assert "execute_v37_durable_attempt(" in function_source
    assert "run_v37_guarded_subprocess(" in source
    assert 'logical_id=f"v37:physical:pepshot:{candidate_id}"' in source
    assert 'logical_id="v37:physical:pepshot:contract"' in source


def test_v37_finalizer_validates_closure_before_success_transition() -> None:
    source = inspect.getsource(v37_activities.finalize_v37_run)
    assert source.index("validate_v37_database_object_replay(") < source.index(
        "run.status = RunStatus.SUCCEEDED"
    )


class _DuplicateSession:
    def __init__(self, duplicate: object | None) -> None:
        self.duplicate = duplicate

    async def scalar(self, _query: object) -> object | None:
        return self.duplicate


@pytest.mark.asyncio
async def test_v37_submit_duplicate_gate_fails_closed() -> None:
    await ensure_no_existing_v37_run(
        _DuplicateSession(None),  # type: ignore[arg-type]
        benchmark_id="amp_rapid_champion_generation_v37",
        benchmark_version="v37.0.0-preregistered",
    )
    with pytest.raises(ValueError, match="formal run already exists"):
        await ensure_no_existing_v37_run(
            _DuplicateSession(SimpleNamespace(id="prior-run")),  # type: ignore[arg-type]
            benchmark_id="amp_rapid_champion_generation_v37",
            benchmark_version="v37.0.0-preregistered",
        )


def test_v37_formal_identity_and_workflow_id_are_content_derived() -> None:
    inputs = {
        "benchmark_id": "amp_rapid_champion_generation_v37",
        "benchmark_version": "v37.0.0-preregistered",
        "manifest_sha256": "a" * 64,
    }
    first = build_v37_formal_submission_key(**inputs)
    second = build_v37_formal_submission_key(**inputs)
    assert first == second
    assert len(first) == 64
    assert build_v37_workflow_id(first) == f"pepagent-rapid-champion-v37-{first}"
    assert build_v37_formal_submission_key(**{**inputs, "manifest_sha256": "b" * 64}) != first


def test_v37_preflight_freezes_database_submission_identity() -> None:
    static = build_v37_static_preflight(
        ROOT / "config/benchmarks/amp_rapid_champion_generation_v37.yaml"
    )
    assert static["schema_version"] == "1.2"
    assert static["formal_submission_key"] == build_v37_formal_submission_key(
        benchmark_id=static["benchmark_id"],
        benchmark_version=static["benchmark_version"],
        manifest_sha256=static["manifest_sha256"],
    )


def test_v37_submission_bundle_refuses_tampered_unauthorized_preflight(
    tmp_path: Path,
) -> None:
    manifest_path = ROOT / "config/benchmarks/amp_rapid_champion_generation_v37.yaml"
    experiment_spec_path = ROOT / "config/experiments/acea_v37_rapid_champion_structure.yaml"
    static = build_v37_static_preflight(manifest_path)
    static["config_execution_authorized"] = False
    static["implementation_revision"] = None
    preflight = authorize_v37_submission_preflight(
        static,
        dynamic_gates={
            "implementation_committed_pushed_archived": True,
            "database_schema_exact": True,
            "services_healthy_zero_active_user_workflows": True,
            "provider_releases_exact": True,
            "worker_host_gpu_pid_role_queue_release_exact": True,
            "forbidden_resources_absent": True,
            "no_existing_v37_run_or_workflow": True,
        },
    )
    preflight_path = tmp_path / "preflight.json"
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
    execution = {
        "generator_runtimes": {
            "hydramp": {},
            "ampgan_v2": {},
            "amp_designer": {},
        },
        "metric_plugins_by_name": {
            "physicochemical_developability": {},
            "hemolysis_risk": {},
            "mic_potency": {},
            "mic_potency_amp_read": {},
            "toxicity_risk": {},
        },
        "knowledge_runtime": {},
        "knowledge_query": {},
        "pepshot_runtime": {},
    }
    execution_path = tmp_path / "execution.json"
    execution_path.write_text(json.dumps(execution), encoding="utf-8")
    assert preflight["status"] == "blocked"
    assert "config_execution_authorized" in preflight["failed_gates"]
    with pytest.raises(ValueError, match="submission preflight is not ready"):
        load_v37_submission_bundle(
            manifest_path=manifest_path,
            experiment_spec_path=experiment_spec_path,
            execution_bundle_path=execution_path,
            preflight_path=preflight_path,
        )
