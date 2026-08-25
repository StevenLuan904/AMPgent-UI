from __future__ import annotations

from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_file
from pepagent.seven_branch_design import SevenBranchDesignContract
from pepagent.seven_branch_preflight import (
    build_seven_branch_quality_continuation_preflight,
    build_seven_branch_submission_preflight,
)

ROOT = Path(__file__).resolve().parents[1]


def _request_template() -> dict:
    adapter = (
        ROOT
        / "src"
        / "pepagent"
        / "model_workers"
        / "physicochemical_runtime"
        / "no_site_bootstrap.py"
    ).resolve()
    runtime_id = "physicochemical-developability-modlamp-4.3.2-biopython-v39"
    return {
        "worker_source_revision": "b" * 40,
        "metric_plugins_by_name": {
            "physicochemical_developability": {
                "runtime_id": runtime_id,
                "execution_guard": {
                    "contract": {
                        "runtime_id": runtime_id,
                        "adapter": {
                            "path": "no_site_bootstrap.py",
                            "sha256": sha256_file(adapter),
                        },
                        "command_entities": {"adapter_index": 2},
                    },
                    "paths": {"adapter_path": str(adapter)},
                },
            }
        },
    }


def _placement(source: str, release: str) -> dict:
    queues = {
        "v38-control": "pepagent-control-v38",
        "v38-generator": "pepagent-generator-v38",
        "v38-metrics": "pepagent-cpu-metrics-v38",
        "v39-target-sequence": "pepagent-gpu-target-sequence-v39",
    }
    workers = {
        role: {
            "role": role,
            "task_queue": queue,
            "pid": index + 1,
            "ampgent_owned": True,
            "foreign": False,
            "poller_identity": f"poller-{role}",
            "source_revision": source,
            "release_sha256": release,
        }
        for index, (role, queue) in enumerate(queues.items())
    }
    workers["v39-target-sequence"].update(
        {
            "physical_host": "192.168.99.19",
            "gpu_index": 2,
            "weights_sha256": "a" * 64,
        }
    )
    return {"schema_version": "ampgent.seven-branch-worker-placement.1", "workers": workers}


def _local_placement(source: str, release: str) -> dict:
    placement = _placement(source, release)
    placement["workers"].pop("v39-target-sequence")
    return placement


def _smoke(source: str, release: str) -> dict:
    return {
        "schema_version": "ampgent.six-target-conditional-smoke.1",
        "source_revision": source,
        "release_sha256": release,
        "same_executable_subprocess": True,
        "all_succeeded": True,
        "targets": [
            {
                "returncode": 0,
                "device": "cuda",
                "conditional_ppl": 1.0,
                "stderr_tail": "",
            }
            for _ in range(6)
        ],
    }


def _quality_schedule_smoke(branch_key: str = "target_agnostic_amp") -> dict:
    return {
        "schema_version": "ampgent.seven-branch-quality-schedule-smoke.1",
        "status": "schedule_frozen_not_submitted",
        "branch_key": branch_key,
        "schedule_schema_version": "ampgent.seven_branch_top_up_schedule.v2",
        "evidence_snapshot_sha256": "d" * 64,
        "quality_progress_sha256": "e" * 64,
        "schedule_sha256": "f" * 64,
        "expected_raw_occurrences": 3000,
        "preflight_reused_for_submission": False,
        "temporal_submitted": False,
        "formal_runs_reserved": False,
    }


def _quality_runtime_smoke(source: str, release: str, contract: SevenBranchDesignContract) -> dict:
    return {
        "schema_version": "ampgent.seven-branch-quality-runtime-smoke.1",
        "source_revision": source,
        "release_sha256": release,
        "same_release_executable": True,
        "generator_succeeded": True,
        "metrics_succeeded": True,
        "database_persisted": False,
        "required_sequence_metrics": sorted(contract.required_sequence_metrics),
    }


def _quality_preflight(
    *,
    source: str,
    release: str,
    branch_keys: tuple[str, ...],
    placement: dict,
    target_smoke: dict | None = None,
) -> dict:
    contract_path = ROOT / "config/workflows/ampgent_seven_branch_design_v1.json"
    contract = SevenBranchDesignContract.model_validate_json(
        contract_path.read_text(encoding="utf-8")
    )
    return build_seven_branch_quality_continuation_preflight(
        request_template=_request_template(),
        design_contract=contract,
        design_contract_path=contract_path,
        target_manifest_path=ROOT
        / "config/targets/ampgent_six_target_sequence_manifest_20260824.json",
        model_selection_path=ROOT
        / "config/targets/ampgent_target_sequence_model_selection_20260824.json",
        worker_placement=placement,
        quality_schedule_smoke=_quality_schedule_smoke(branch_keys[0]),
        quality_runtime_smoke=_quality_runtime_smoke(source, release, contract),
        source_revision=source,
        release_sha256=release,
        branch_keys=branch_keys,
        execution_authorized=True,
        target_smoke=target_smoke,
    )


def test_seven_branch_preflight_binds_6600_raw_and_1900_delivery() -> None:
    source = "b" * 40
    release = "c" * 64
    contract_path = ROOT / "config/workflows/ampgent_seven_branch_design_v1.json"
    target_path = ROOT / "config/targets/ampgent_six_target_sequence_manifest_20260824.json"
    model_path = ROOT / "config/targets/ampgent_target_sequence_model_selection_20260824.json"
    contract = SevenBranchDesignContract.model_validate_json(
        contract_path.read_text(encoding="utf-8")
    )
    preflight = build_seven_branch_submission_preflight(
        request_template=_request_template(),
        design_contract=contract,
        design_contract_path=contract_path,
        target_manifest_path=target_path,
        model_selection_path=model_path,
        worker_placement=_placement(source, release),
        target_smoke=_smoke(source, release),
        source_revision=source,
        release_sha256=release,
        execution_authorized=True,
    )
    assert preflight["status"] == "ready_to_submit_unique_run"
    assert preflight["initial_raw_occurrences"] == 6600
    assert preflight["delivery_quota"] == 1900
    assert preflight["required_sequence_metric_count"] == 12


def test_seven_branch_preflight_rejects_prohibited_gpu() -> None:
    source = "b" * 40
    release = "c" * 64
    contract_path = ROOT / "config/workflows/ampgent_seven_branch_design_v1.json"
    contract = SevenBranchDesignContract.model_validate_json(
        contract_path.read_text(encoding="utf-8")
    )
    placement = _placement(source, release)
    placement["workers"]["v39-target-sequence"]["physical_host"] = "192.168.99.32:2"
    with pytest.raises(ValueError, match="prohibited GPU"):
        build_seven_branch_submission_preflight(
            request_template=_request_template(),
            design_contract=contract,
            design_contract_path=contract_path,
            target_manifest_path=ROOT
            / "config/targets/ampgent_six_target_sequence_manifest_20260824.json",
            model_selection_path=ROOT
            / "config/targets/ampgent_target_sequence_model_selection_20260824.json",
            worker_placement=placement,
            target_smoke=_smoke(source, release),
            source_revision=source,
            release_sha256=release,
            execution_authorized=True,
        )


def test_agnostic_quality_continuation_needs_only_three_local_roles() -> None:
    source = "b" * 40
    release = "c" * 64
    preflight = _quality_preflight(
        source=source,
        release=release,
        branch_keys=("target_agnostic_amp",),
        placement=_local_placement(source, release),
    )
    assert preflight["status"] == "ready_to_submit_unique_run"
    assert preflight["target_sequence_required"] is False
    assert preflight["required_sequence_metric_count"] == 12
    assert preflight["expected_raw_occurrences"] == 3000


def test_target_quality_continuation_requires_target_worker_and_smoke() -> None:
    source = "b" * 40
    release = "c" * 64
    with pytest.raises(ValueError, match="exactly four roles"):
        _quality_preflight(
            source=source,
            release=release,
            branch_keys=("acea",),
            placement=_local_placement(source, release),
        )
    with pytest.raises(ValueError, match="requires target smoke"):
        _quality_preflight(
            source=source,
            release=release,
            branch_keys=("acea",),
            placement=_placement(source, release),
        )


def test_quality_continuation_rejects_stale_release() -> None:
    source = "b" * 40
    release = "c" * 64
    with pytest.raises(ValueError, match="v38-control"):
        _quality_preflight(
            source=source,
            release=release,
            branch_keys=("target_agnostic_amp",),
            placement=_local_placement(source, "a" * 64),
        )
