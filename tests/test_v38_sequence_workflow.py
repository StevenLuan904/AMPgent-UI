from __future__ import annotations

import pytest

from pepagent.v38_science_execution import build_default_v38_sequence_contract
from pepagent.workers.v38_temporal_worker import V38_ROLE_CONFIG
from pepagent.workflows.v38_sequence_first import (
    V38SequenceFirstAgentWorkflow,
    _validate_request,
)


def _request() -> dict:
    branches = [
        {
            "target_key": "ec_gyrA_lei800",
            "target_id": "11111111-1111-1111-1111-111111111111",
            "target_sequence_sha256": "1" * 64,
            "coordinate_sha256": "2" * 64,
            "native_pocket_sha256": "3" * 64,
            "wrong_pocket_sha256": "4" * 64,
            "qualification_witness_sha256": "5" * 64,
            "evidence_grade": "A",
            "panel_role": "qualified_target",
            "structure_budget": 48,
            "boltz_seeds_per_candidate": 3,
            "rosetta_decoys_per_pose": 16,
            "target_agnostic_amp_lane_retained": True,
        },
        {
            "target_key": "se_pbp2a_allosteric",
            "target_id": "22222222-2222-2222-2222-222222222222",
            "target_sequence_sha256": "6" * 64,
            "coordinate_sha256": "7" * 64,
            "native_pocket_sha256": "8" * 64,
            "wrong_pocket_sha256": "9" * 64,
            "qualification_witness_sha256": "a" * 64,
            "evidence_grade": "A",
            "panel_role": "qualified_target",
            "structure_budget": 48,
            "boltz_seeds_per_candidate": 3,
            "rosetta_decoys_per_pose": 16,
            "target_agnostic_amp_lane_retained": True,
        },
    ]
    return {
        "submission_preflight": {"status": "ready_to_submit_unique_run"},
        "knowledge_context_pack_sha256": "a" * 64,
        "refinement_provider": {
            "activity_name": "refine_v38_sequences_with_knowledge",
            "task_queue": "pepagent-refinement-provider-v38",
            "provider_task_id": "019fad3e-76b8-7e32-8455-d2e9b31d33e5",
            "release_revision": "provider-release-v1",
            "runtime_manifest_sha256": "b" * 64,
        },
        "execution_contract": build_default_v38_sequence_contract().model_dump(
            mode="json"
        ),
        "multitarget_plan_template": {
            "harness_release_id": "v38-test-release",
            "history_snapshot_sha256": "b" * 64,
            "target_branches": branches,
            "max_parallel_targets": 2,
        },
        "structure_runtime_by_target_key": {
            branch["target_key"]: {
                "target_sequence": "A" * 100,
                "pocket_residues_by_lane": {
                    "native": [1, 2, 3],
                    "wrong_pocket": [50, 51, 52],
                },
                "structure_spec": {"diffusion_samples": 1},
            }
            for branch in branches
        },
        "boltz_seeds": [20270380, 20270381, 20270382],
        "task_queues": {
            "workflow_and_control": "pepagent-control-v38",
            "generator": "pepagent-generator-v38",
            "sequence_metrics": "pepagent-cpu-metrics-v38",
            "structure_boltz": "pepagent-gpu-boltz2-v38",
            "structure_rosetta": "pepagent-cpu-rosetta-v38",
        },
        "generation_concurrency": 3,
        "metric_concurrency": 5,
        "structure_concurrency": 2,
    }


def test_v38_sequence_workflow_accepts_only_full_score_all_contract() -> None:
    request = _request()
    _validate_request(request)

    request["execution_contract"]["cells"].pop()
    with pytest.raises(ValueError, match="nine generator cells"):
        _validate_request(request)


def test_v38_sequence_workflow_rejects_first_k_and_missing_metric_plugin() -> None:
    first_k = _request()
    first_k["execution_contract"]["first_k_retention_forbidden"] = False
    with pytest.raises(ValueError, match="first-K"):
        _validate_request(first_k)

    missing_metric = _request()
    missing_metric["execution_contract"]["metric_plugins"].pop()
    with pytest.raises(ValueError, match="five sequence metric plugins"):
        _validate_request(missing_metric)


def test_v38_sequence_workflow_requires_frozen_refinement_provider() -> None:
    request = _request()
    request.pop("refinement_provider")
    with pytest.raises(ValueError, match="frozen refinement provider"):
        _validate_request(request)

    invalid = _request()
    invalid["refinement_provider"]["runtime_manifest_sha256"] = "not-a-sha"
    with pytest.raises(ValueError, match="runtime identity"):
        _validate_request(invalid)


def test_v38_sequence_workflow_requires_complete_multitarget_runtime() -> None:
    missing_runtime = _request()
    missing_runtime["structure_runtime_by_target_key"].pop("se_pbp2a_allosteric")
    with pytest.raises(ValueError, match="do not cover target branches"):
        _validate_request(missing_runtime)

    duplicate_seed = _request()
    duplicate_seed["boltz_seeds"] = [20270380, 20270380, 20270382]
    with pytest.raises(ValueError, match="three distinct Boltz seeds"):
        _validate_request(duplicate_seed)


def test_v38_control_worker_registers_sequence_workflow_and_admission_activities() -> None:
    _, activities, workflows = V38_ROLE_CONFIG["v38-control"]
    assert V38SequenceFirstAgentWorkflow in workflows
    registered = {activity.__temporal_activity_definition.name for activity in activities}
    assert {
        "persist_v38_score_all_generation",
        "persist_v38_refinement_children",
        "persist_v38_sequence_metric",
        "evaluate_v38_sequence_admission",
        "persist_v38_sequence_admission",
        "plan_v38_multitarget_structure",
    } <= registered
