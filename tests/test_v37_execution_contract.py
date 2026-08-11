from copy import deepcopy
from pathlib import Path

import pytest

from pepagent.v37_evidence import build_v37_evidence_plan
from pepagent.v37_preflight import (
    authorize_v37_submission_preflight,
    build_v37_static_preflight,
)
from pepagent.v37_preregistration import (
    V37Manifest,
    load_v37_preregistration,
    validate_v37_experiment_spec,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_rapid_champion_generation_v37.yaml"


def test_v37_manifest_and_evidence_plan_are_exact() -> None:
    manifest = load_v37_preregistration(CONFIG)
    plan = build_v37_evidence_plan(manifest)
    assert len(plan["generator_calls"]) == 9
    assert len(plan["metric_calls"]) == 5
    assert plan["expected_candidate_count"] == 900
    assert plan["expected_structure_shortlist"] == 48
    assert len(plan["required_tool_call_ids"]) == 21


def test_v37_manifest_rejects_single_generator_drift() -> None:
    manifest = load_v37_preregistration(CONFIG)
    payload = manifest.model_dump(mode="python")
    drifted = deepcopy(payload)
    drifted["generators"]["engines"] = drifted["generators"]["engines"][-1:]
    with pytest.raises(ValueError, match="generator order"):
        V37Manifest.model_validate(drifted)


def test_v37_static_and_dynamic_preflight_never_submit() -> None:
    static = build_v37_static_preflight(CONFIG)
    assert static["direction_authorized"] is True
    assert static["execution_authorized"] is False
    assert static["formal_run_submitted"] is False
    gates = {
        "implementation_committed_pushed_archived": True,
        "database_schema_exact": True,
        "services_healthy_zero_active_user_workflows": True,
        "provider_releases_exact": True,
        "worker_host_gpu_pid_role_queue_release_exact": True,
        "forbidden_resources_absent": True,
        "no_existing_v37_run_or_workflow": True,
    }
    ready = authorize_v37_submission_preflight(static, dynamic_gates=gates)
    assert ready["status"] == "ready_to_submit_unique_run"
    assert ready["execution_authorized"] is True
    assert ready["formal_run_submitted"] is False


def test_v37_dynamic_preflight_lists_failed_gate() -> None:
    static = build_v37_static_preflight(CONFIG)
    gates = {
        "implementation_committed_pushed_archived": True,
        "database_schema_exact": False,
        "services_healthy_zero_active_user_workflows": True,
        "provider_releases_exact": True,
        "worker_host_gpu_pid_role_queue_release_exact": True,
        "forbidden_resources_absent": True,
        "no_existing_v37_run_or_workflow": True,
    }
    blocked = authorize_v37_submission_preflight(static, dynamic_gates=gates)
    assert blocked["status"] == "blocked"
    assert blocked["failed_gates"] == ["database_schema_exact"]


def test_v37_experiment_spec_is_exact_and_drift_fails_closed(tmp_path: Path) -> None:
    manifest = load_v37_preregistration(CONFIG)
    verified = validate_v37_experiment_spec(manifest, CONFIG)
    assert verified["boltz_seeds"] == [20270380, 20270381, 20270382]
    assert verified["rosetta_decoys_per_pose"] == 16

    source = ROOT / "config" / "experiments" / "acea_v37_rapid_champion_structure.yaml"
    drifted = tmp_path / source.name
    drifted.write_text(
        source.read_text(encoding="utf-8").replace("rosetta_nstruct: 16", "rosetta_nstruct: 8"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="experiment spec SHA drifted"):
        validate_v37_experiment_spec(manifest, CONFIG, spec_path_override=drifted)
