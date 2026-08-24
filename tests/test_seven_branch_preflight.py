from __future__ import annotations

from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_file
from pepagent.seven_branch_design import SevenBranchDesignContract
from pepagent.seven_branch_preflight import build_seven_branch_submission_preflight

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
