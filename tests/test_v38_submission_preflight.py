from copy import deepcopy

import pytest

from pepagent.v38_preflight import build_v38_submission_preflight
from pepagent.v38_science_execution import build_default_v38_sequence_contract


def _request() -> dict:
    branches = [
        {
            "target_key": key,
            "target_id": target_id,
            "target_sequence_sha256": character * 64,
            "coordinate_sha256": character * 64,
            "native_pocket_sha256": "a" * 64,
            "wrong_pocket_sha256": "b" * 64,
            "qualification_witness_sha256": "c" * 64,
            "evidence_grade": "A",
            "panel_role": "qualified_target",
            "structure_budget": 48,
            "boltz_seeds_per_candidate": 3,
            "rosetta_decoys_per_pose": 16,
            "target_agnostic_amp_lane_retained": True,
        }
        for key, target_id, character in (
            ("ec_gyrA_lei800", "11111111-1111-1111-1111-111111111111", "1"),
            ("se_pbp2a_allosteric", "22222222-2222-2222-2222-222222222222", "2"),
        )
    ]
    return {
        "knowledge_context_pack_sha256": "d" * 64,
        "refinement_provider": {
            "activity_name": "refine_v38_sequences_with_knowledge",
            "task_queue": "pepagent-refinement-provider-v38",
            "provider_task_id": "019fad3e-76b8-7e32-8455-d2e9b31d33e5",
            "release_revision": "provider-v1",
            "runtime_manifest_sha256": "e" * 64,
        },
        "execution_contract": build_default_v38_sequence_contract().model_dump(mode="json"),
        "multitarget_plan_template": {
            "harness_release_id": "v38-test",
            "history_snapshot_sha256": "f" * 64,
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
                "structure_spec": {},
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
        "structure_concurrency": 1,
    }


def _state() -> dict:
    return {
        "schema_version": "v38.agent-controller-state.1",
        "controller_run_id": "11111111-1111-1111-1111-111111111111",
        "formal_submission_key": "a" * 64,
        "formal_science_workflow_submitted": False,
        "candidate_generation_started": False,
        "blockers": [],
        "durable_counts": {
            "candidates": 0,
            "occurrences": 0,
            "evaluations": 0,
            "structure_evidence_records": 0,
            "decisions": 0,
            "replay_evidence_links": 0,
            "tool_calls": 0,
        },
        "history_snapshot_sha256": "f" * 64,
        "history_terminal_run_count": 54,
        "sequence_worker_release": {
            "source_revision": "c" * 40,
            "release_sha256": "d" * 64,
        },
    }


def _placement() -> dict:
    queues = {
        "v38-control": "pepagent-control-v38",
        "v38-generator": "pepagent-generator-v38",
        "v38-metrics": "pepagent-cpu-metrics-v38",
        "v38-boltz": "pepagent-gpu-boltz2-v38",
        "v38-rosetta": "pepagent-cpu-rosetta-v38",
    }
    workers = {
        role: {
            "role": role,
            "task_queue": queue,
            "pid": index + 1,
            "poller_identity": f"{role}-poller",
            "source_revision": "c" * 40,
            "release_sha256": "d" * 64,
            "ampgent_owned": True,
            "foreign": False,
            "resource": "local",
        }
        for index, (role, queue) in enumerate(queues.items())
    }
    workers["v38-boltz"]["resource"] = "192.168.99.32:1"
    workers["v38-boltz"]["weights_sha256"] = "9" * 64
    workers["v38-boltz"]["runtime_cache_attestation"] = {
        "schema_version": "v38.boltz-runtime-cache-attestation.1",
        "boltz_executable": "/opt/pepagent/bin/boltz",
        "weights": {
            "filename": "boltz2_conf.ckpt",
            "size_bytes": 2_286_561_469,
            "sha256": "9" * 64,
        },
        "molecular_archive": {
            "filename": "mols.tar",
            "size_bytes": 1_855_662_080,
            "sha256": "39e076d96dbec6b4e86982bbda16f3a53a2a60c9bdc17828d88f6f9a0c7d1fd7",
        },
        "molecule_file_count": 45_227,
        "guarded_smoke_sha256": "8" * 64,
    }
    workers["v38-rosetta"]["resource"] = "synth:cpu"
    return {
        "schema_version": "v38.worker-placement.1",
        "workers": workers,
        "refinement_provider": {
            "task_queue": "pepagent-refinement-provider-v38",
            "poller_identity": "provider-poller",
            "runtime_manifest_sha256": "e" * 64,
        },
    }


def test_v38_preflight_binds_one_unsubmitted_request_and_full_placement() -> None:
    result = build_v38_submission_preflight(
        request_template=_request(),
        controller_state=_state(),
        worker_placement=_placement(),
        benchmark_sha256="1" * 64,
        target_panel_sha256="2" * 64,
    )
    assert result["status"] == "ready_to_submit_unique_run"
    assert result["execution_authorized"] is True
    assert set(result["worker_component_identities"]) == set(_placement()["workers"])


def test_v38_preflight_allows_explicitly_bound_structure_release_identity() -> None:
    placement = _placement()
    placement["workers"]["v38-rosetta"].update(
        source_revision="f" * 40,
        release_sha256="0" * 64,
    )
    result = build_v38_submission_preflight(
        request_template=_request(),
        controller_state=_state(),
        worker_placement=placement,
        benchmark_sha256="1" * 64,
        target_panel_sha256="2" * 64,
    )
    assert result["worker_component_identities"]["v38-rosetta"] == {
        "source_revision": "f" * 40,
        "release_sha256": "0" * 64,
    }
    assert result["failed_gates"] == []
    assert result["workflow_id"].endswith(result["formal_submission_key"])


def test_v38_recovery_preflight_accepts_newer_bound_history() -> None:
    state = _state()
    state["history_terminal_run_count"] = 55
    request = _request()
    request["multitarget_plan_template"]["history_snapshot_sha256"] = state[
        "history_snapshot_sha256"
    ]

    result = build_v38_submission_preflight(
        request_template=request,
        controller_state=state,
        worker_placement=_placement(),
        benchmark_sha256="1" * 64,
        target_panel_sha256="2" * 64,
    )

    assert result["history_terminal_run_count"] == 55


def test_v38_preflight_rejects_a_template_that_self_asserts_readiness() -> None:
    request = _request()
    request["submission_preflight"] = {"status": "ready_to_submit_unique_run"}
    with pytest.raises(ValueError, match="cannot self-assert"):
        build_v38_submission_preflight(
            request_template=request,
            controller_state=_state(),
            worker_placement=_placement(),
            benchmark_sha256="1" * 64,
            target_panel_sha256="2" * 64,
        )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda state, placement: state.update(blockers=["blocked"]), "still has blockers"),
        (
            lambda state, placement: placement["workers"]["v38-boltz"].update(
                resource="192.168.99.32:2"
            ),
            "prohibited GPU",
        ),
        (
            lambda state, placement: placement["workers"]["v38-metrics"].update(
                source_revision="f" * 40
            ),
            "sequence workers do not share one immutable source",
        ),
        (
            lambda state, placement: placement["workers"]["v38-boltz"].pop(
                "runtime_cache_attestation"
            ),
            "lacks a verified runtime cache attestation",
        ),
        (
            lambda state, placement: placement["workers"]["v38-boltz"][
                "runtime_cache_attestation"
            ]["weights"].update(size_bytes=0),
            "runtime cache attestation is invalid",
        ),
    ],
)
def test_v38_preflight_fails_closed_on_control_or_placement_drift(
    mutation, match: str
) -> None:
    state = deepcopy(_state())
    placement = deepcopy(_placement())
    mutation(state, placement)
    with pytest.raises(ValueError, match=match):
        build_v38_submission_preflight(
            request_template=_request(),
            controller_state=state,
            worker_placement=placement,
            benchmark_sha256="1" * 64,
            target_panel_sha256="2" * 64,
        )
