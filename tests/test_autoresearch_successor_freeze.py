from __future__ import annotations

import copy
from typing import Any

import pytest
import yaml

from pepagent.autoresearch_closed_loop import ContinuationPolicy, MultiFrontArchivePolicy
from pepagent.autoresearch_successor_freeze import (
    assess_cpu_only_successor_runtime_readiness,
    freeze_cpu_only_successor,
    freeze_external_metric_registry_migration,
)
from pepagent.provenance.hashing import sha256_bytes, sha256_json, sha256_text
from pepagent.v38_science_execution import build_default_v38_sequence_contract
from pepagent.workflows.autoresearch import _validate_request

PREDECESSOR_ID = "10000000-0000-0000-0000-000000000001"
OLD_ROOT = r"D:\ampgent-v9-clean\var\platform\releases-v39-quality\old-release"
NEW_ROOT = r"D:\workspace\agent-platform\var\platform\releases-v39-quality\new-release"


def _runtime_descriptor(name: str, *, under_release: bool) -> dict[str, Any]:
    root = OLD_ROOT if under_release else r"E:\external-models"
    descriptor = {
        "name": name,
        "adapter_path": rf"{root}\adapters\{name}.py",
        "cwd": rf"{root}\runtime",
        "execution_guard": {
            "packages_lock_path": rf"{root}\locks\requirements.lock",
            "runtime_manifest_path": rf"{root}\manifests\runtime.json",
        },
    }
    descriptor["runtime_identity_sha256"] = sha256_json(descriptor)
    return descriptor


def _request() -> dict[str, Any]:
    contract = build_default_v38_sequence_contract().model_dump(mode="json")
    plugins = {
        name: _runtime_descriptor(name, under_release=index == 0)
        for index, name in enumerate(contract["metric_plugins"])
    }
    target = "MKTIIALSYIFCLVFADYKDDDDK"
    return {
        "schema_version": "ampgent.autoresearch-workflow-request.1",
        "run_id": PREDECESSOR_ID,
        "branch_key": "PBP2a",
        "execution_contract": contract,
        "metric_plugins_by_name": plugins,
        "task_queues": {
            "workflow_and_control": "pepagent-autoresearch-control-v1",
            "action_execution": "pepagent-autoresearch-generator-v1",
            "sequence_metrics": "pepagent-autoresearch-metrics-v1",
        },
        "planner_provider": {
            "activity_name": "plan_autoresearch_actions",
            "task_queue": "pepagent-autoresearch-control-v1",
            "planner_contract": {"de_novo_quota": 0.2, "pepmlm_targeted_enabled": True},
        },
        "action_executor": {
            "operator_environment_sha256": "d" * 64,
            "operator_release_sha256": "e" * 64,
            "target_sequence": target,
            "target_sequence_sha256": sha256_text(target),
        },
        "initial_action_plan": {"actions": [{"kind": "old-plan"}]},
        "archive_policy": MultiFrontArchivePolicy().model_dump(mode="json"),
        "continuation_policy": ContinuationPolicy(
            maximum_generations_per_run=5,
            minimum_high_quality_candidates=50,
            stagnation_patience_generations=1,
        ).model_dump(mode="json"),
        "control_environment_sha256": "c" * 64,
    }


def _freeze(request: dict[str, Any] | None = None, **overrides: Any):
    request = request or _request()
    inputs = {
        "predecessor_request": request,
        "predecessor_run_id": PREDECESSOR_ID,
        "predecessor_request_sha256": sha256_json(request),
        "latest_generation": 3,
        "source_revision": "a" * 40,
        "release_sha256": "b" * 64,
        "old_release_root": OLD_ROOT,
        "new_release_root": NEW_ROOT,
        "eligibility_sha256": "f" * 64,
    }
    inputs.update(overrides)
    return freeze_cpu_only_successor(**inputs)


def _remove_old_release_paths(request: dict[str, Any]) -> None:
    for descriptor in request["metric_plugins_by_name"].values():
        descriptor["adapter_path"] = r"E:\elsewhere\adapter.py"
        descriptor["cwd"] = r"E:\elsewhere"
        descriptor["execution_guard"].update(
            packages_lock_path=r"E:\elsewhere\lock",
            runtime_manifest_path=r"E:\elsewhere\manifest",
        )


def test_freeze_is_deterministic_valid_and_advances_generation() -> None:
    first = _freeze()
    second = _freeze()

    assert first == second
    _validate_request(first.request)
    assert first.request["predecessor_run_id"] == PREDECESSOR_ID
    assert first.request["start_iteration_no"] == 4
    assert first.request["maximum_iterations_per_workflow_execution"] == 2
    assert "initial_action_plan" not in first.request
    assert first.request["historical_outputs_reused"] is False
    assert first.receipt["submitted"] is False


def test_freeze_does_not_regress_when_predecessor_failed_before_first_action() -> None:
    predecessor = _request()
    predecessor["start_iteration_no"] = 4

    frozen = _freeze(
        predecessor,
        predecessor_request_sha256=sha256_json(predecessor),
        latest_generation=0,
    )

    assert frozen.request["start_iteration_no"] == 4
    assert frozen.receipt["predecessor_start_iteration_no"] == 4
    assert frozen.receipt["latest_persisted_generation"] == 0
    assert frozen.receipt["start_iteration_no"] == 4


def test_freeze_rewrites_only_release_paths_and_refreshes_runtime_identity() -> None:
    frozen = _freeze()
    descriptors = frozen.request["metric_plugins_by_name"]
    migrated = next(value for value in descriptors.values() if NEW_ROOT in value["adapter_path"])
    external = next(
        value for value in descriptors.values() if r"E:\external-models" in value["adapter_path"]
    )

    assert OLD_ROOT.lower() not in str(frozen.request).lower()
    assert external["cwd"] == r"E:\external-models\runtime"
    identity = migrated["runtime_identity_sha256"]
    assert identity == sha256_json(
        {key: value for key, value in migrated.items() if key != "runtime_identity_sha256"}
    )
    assert frozen.receipt["release_path_rewrite_count"] == 4


def test_freeze_migrates_external_registry_commands_to_guarded_release(tmp_path) -> None:
    request = _request()
    source_registry = {
        "adapters": {
            name: {
                "enabled": True,
                "command": [
                    r"C:\runtime\python.exe",
                    rf"D:\historical-release\{name}.py",
                    "--input",
                    "{input}",
                ],
            }
            for name in request["metric_plugins_by_name"]
        }
    }
    source_bytes = yaml.safe_dump(source_registry, sort_keys=True).encode("utf-8")
    source_path = r"D:\cache\old-registry\runtime.local.yaml"
    source_sha256 = sha256_bytes(source_bytes)
    migrated_names = []
    for name, descriptor in request["metric_plugins_by_name"].items():
        if OLD_ROOT not in descriptor["adapter_path"]:
            continue
        migrated_names.append(name)
        descriptor["registry_path"] = source_path
        descriptor["registry_sha256"] = source_sha256
        descriptor["execution_guard"] = {
            "contract": {"command_entities": {"adapter_index": 1}},
            "paths": {"adapter_path": descriptor["adapter_path"]},
        }
        descriptor["runtime_identity_sha256"] = sha256_json(
            {
                key: value
                for key, value in descriptor.items()
                if key != "runtime_identity_sha256"
            }
        )

    migration = freeze_external_metric_registry_migration(
        predecessor_request=request,
        source_registry_bytes=source_bytes,
        old_release_root=OLD_ROOT,
        new_release_root=NEW_ROOT,
        registry_cache_root=tmp_path / "registries",
    )
    frozen = _freeze(
        request,
        predecessor_request_sha256=sha256_json(request),
        external_metric_registry_migration=migration,
    )
    migrated_registry = yaml.safe_load(migration.content)

    assert migration.destination_sha256 == sha256_bytes(migration.content)
    assert migration.destination_path.endswith(
        f"{migration.destination_sha256}\\runtime.local.yaml"
    )
    for name in migrated_names:
        descriptor = frozen.request["metric_plugins_by_name"][name]
        assert descriptor["registry_path"] == migration.destination_path
        assert descriptor["registry_sha256"] == migration.destination_sha256
        assert migrated_registry["adapters"][name]["command"][1] == descriptor[
            "execution_guard"
        ]["paths"]["adapter_path"]
        assert descriptor["runtime_identity_sha256"] == sha256_json(
            {
                key: value
                for key, value in descriptor.items()
                if key != "runtime_identity_sha256"
            }
        )


def test_freeze_rejects_unmigrated_external_registry() -> None:
    request = _request()
    for descriptor in request["metric_plugins_by_name"].values():
        descriptor["registry_path"] = r"D:\cache\old\runtime.local.yaml"
        descriptor["registry_sha256"] = "1" * 64

    with pytest.raises(ValueError, match="registry migration is required"):
        _freeze(request, predecessor_request_sha256=sha256_json(request))


def test_freeze_preserves_environments_and_target_but_updates_release_controls() -> None:
    predecessor = _request()
    frozen = _freeze(predecessor)

    assert frozen.request["control_environment_sha256"] == "c" * 64
    assert frozen.request["action_executor"]["operator_environment_sha256"] == "d" * 64
    assert frozen.request["action_executor"]["operator_release_sha256"] == "b" * 64
    assert frozen.request["action_executor"]["target_sequence"] == (
        predecessor["action_executor"]["target_sequence"]
    )
    assert frozen.request["task_queues"] == {
        "workflow_and_control": "pepagent-autoresearch-cpu-successor-control-v5",
        "action_execution": "pepagent-autoresearch-cpu-successor-no-gpu-v5",
        "sequence_metrics": "pepagent-autoresearch-cpu-successor-metrics-v5",
        "persistence": "pepagent-autoresearch-cpu-successor-persistence-v5",
    }
    assert frozen.request["planner_provider"]["task_queue"] == (
        "pepagent-autoresearch-cpu-successor-control-v5"
    )
    assert (
        frozen.request["planner_provider"]["planner_contract"]["pepmlm_targeted_enabled"]
        is False
    )
    assert frozen.receipt["generator_gpu_work_required"] is False
    assert frozen.receipt["new_gpu_tasks_allowed"] is False


def test_eligibility_is_part_of_successor_identity() -> None:
    first = _freeze()
    second = _freeze(eligibility_sha256="9" * 64)

    assert first.run_id != second.run_id
    assert first.workflow_id != second.workflow_id
    assert first.request_sha256 != second.request_sha256


def _live_pollers(frozen) -> dict[str, dict[str, int]]:
    queues = frozen.request["task_queues"]
    return {
        queues["workflow_and_control"]: {"workflow": 1, "activity": 1},
        queues["persistence"]: {"activity": 1},
        queues["sequence_metrics"]: {"activity": 1},
    }


def test_cpu_successor_runtime_readiness_requires_no_generator_poller() -> None:
    frozen = _freeze()
    readiness = assess_cpu_only_successor_runtime_readiness(
        request=frozen.request,
        freeze_receipt=frozen.receipt,
        predecessor_database_status="failed",
        predecessor_temporal_status="FAILED",
        existing_successor_count=0,
        live_pollers=_live_pollers(frozen),
        release_paths_verified=True,
    )

    assert readiness.ready_to_submit is True
    assert readiness.reason_codes == ()
    assert readiness.generator_poller_required is False
    assert frozen.request["task_queues"]["action_execution"] not in (
        readiness.required_live_pollers
    )


def test_cpu_successor_runtime_readiness_fails_closed_without_pollers() -> None:
    frozen = _freeze()
    readiness = assess_cpu_only_successor_runtime_readiness(
        request=frozen.request,
        freeze_receipt=frozen.receipt,
        predecessor_database_status="failed",
        predecessor_temporal_status="FAILED",
        existing_successor_count=0,
        live_pollers={},
        release_paths_verified=True,
    )

    assert readiness.ready_to_submit is False
    assert len(readiness.reason_codes) == 4
    assert all(reason.startswith("missing_live_") for reason in readiness.reason_codes)


def test_cpu_successor_runtime_readiness_rejects_gpu_or_identity_drift() -> None:
    frozen = _freeze()
    receipt = {**frozen.receipt, "generator_gpu_work_required": True}
    request = copy.deepcopy(frozen.request)
    request["historical_outputs_reused"] = True
    readiness = assess_cpu_only_successor_runtime_readiness(
        request=request,
        freeze_receipt=receipt,
        predecessor_database_status="running",
        predecessor_temporal_status="RUNNING",
        existing_successor_count=1,
        live_pollers=_live_pollers(frozen),
        release_paths_verified=False,
    )

    assert readiness.ready_to_submit is False
    assert "predecessor_database_not_failed" in readiness.reason_codes
    assert "predecessor_temporal_not_failed" in readiness.reason_codes
    assert "successor_already_exists" in readiness.reason_codes
    assert "historical_outputs_reuse_not_disabled" in readiness.reason_codes
    assert "generator_gpu_work_not_explicitly_disabled" in readiness.reason_codes
    assert "release_paths_not_verified" in readiness.reason_codes


@pytest.mark.parametrize(
    ("mutation", "overrides", "match"),
    [
        (lambda request: request, {"predecessor_request_sha256": "0" * 64}, "hash does not match"),
        (
            lambda request: request.update(branch_key="unknown"),
            {},
            "unsupported AutoResearch branch",
        ),
        (lambda request: request, {"latest_generation": -1}, "must be non-negative"),
        (
            _remove_old_release_paths,
            {},
            "contains no path",
        ),
    ],
)
def test_freeze_fails_closed(mutation, overrides, match: str) -> None:
    request = _request()
    mutation(request)
    if "predecessor_request_sha256" not in overrides:
        overrides["predecessor_request_sha256"] = sha256_json(request)

    with pytest.raises(ValueError, match=match):
        _freeze(request, **overrides)
