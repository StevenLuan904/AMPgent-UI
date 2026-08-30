from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any
from uuid import UUID

import yaml

from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.workflows.autoresearch import _validate_request

BRANCH_KEYS = frozenset({"acea", "angpt1", "fgf2", "gyra", "pbp2a", "vegfa"})
# v4 is intentionally isolated from the mixed v3 poller population.  A successor
# frozen against these queues can only be consumed by a worker from the release
# that understands the migrated runtime registry contract.
CPU_SUCCESSOR_CONTROL_QUEUE = "pepagent-autoresearch-cpu-successor-control-v4"
CPU_SUCCESSOR_PERSISTENCE_QUEUE = "pepagent-autoresearch-cpu-successor-persistence-v4"
CPU_SUCCESSOR_METRICS_QUEUE = "pepagent-autoresearch-cpu-successor-metrics-v4"
CPU_SUCCESSOR_NO_GPU_QUEUE = "pepagent-autoresearch-cpu-successor-no-gpu-v4"
RUN_ID_NAMESPACE = UUID("cc724227-dab3-4ddb-b187-2a744d012561")
WORKFLOW_ID_NAMESPACE = UUID("fb03516f-52e7-4507-aacd-6f09adae2563")


@dataclass(frozen=True)
class FrozenAutoResearchSuccessor:
    request: dict[str, Any]
    request_sha256: str
    request_template_sha256: str
    run_identity_sha256: str
    formal_submission_key: str
    run_id: UUID
    workflow_id: str
    receipt: dict[str, Any]


@dataclass(frozen=True)
class CpuOnlySuccessorRuntimeReadiness:
    ready_to_submit: bool
    reason_codes: tuple[str, ...]
    required_live_pollers: dict[str, dict[str, int]]
    generator_poller_required: bool = False


@dataclass(frozen=True)
class FrozenExternalMetricRegistryMigration:
    source_path: str
    source_sha256: str
    destination_path: str
    destination_sha256: str
    content: bytes
    plugin_names: tuple[str, ...]


def freeze_external_metric_registry_migration(
    *,
    predecessor_request: dict[str, Any],
    source_registry_bytes: bytes,
    old_release_root: str,
    new_release_root: str,
    registry_cache_root: str | Path,
) -> FrozenExternalMetricRegistryMigration:
    """Freeze one content-addressed registry whose commands match execution guards."""

    descriptors = predecessor_request.get("metric_plugins_by_name") or {}
    external = {
        str(name): descriptor
        for name, descriptor in descriptors.items()
        if isinstance(descriptor, dict) and descriptor.get("registry_path")
    }
    if not external:
        raise ValueError("predecessor request has no external metric registry bindings")
    source_paths = {str(item.get("registry_path")) for item in external.values()}
    source_hashes = {str(item.get("registry_sha256")) for item in external.values()}
    if len(source_paths) != 1 or len(source_hashes) != 1:
        raise ValueError("external metric plugins do not share one frozen registry identity")
    source_path = next(iter(source_paths))
    source_sha256 = _require_sha256(next(iter(source_hashes)), "metric registry")
    if sha256_bytes(source_registry_bytes) != source_sha256:
        raise ValueError("external metric registry bytes differ from the frozen identity")
    registry = yaml.safe_load(source_registry_bytes.decode("utf-8"))
    if not isinstance(registry, dict) or not isinstance(registry.get("adapters"), dict):
        raise ValueError("external metric registry is malformed")

    migrated = copy.deepcopy(registry)
    for plugin_name, descriptor in sorted(external.items()):
        adapter = migrated["adapters"].get(plugin_name)
        guard = descriptor.get("execution_guard") or {}
        contract = guard.get("contract") or {}
        paths = guard.get("paths") or {}
        command = adapter.get("command") if isinstance(adapter, dict) else None
        adapter_index = (contract.get("command_entities") or {}).get("adapter_index")
        if (
            not isinstance(command, list)
            or adapter_index is None
            or int(adapter_index) < 0
            or len(command) <= int(adapter_index)
        ):
            raise ValueError(f"external metric registry command is incomplete: {plugin_name}")
        declared = str(paths.get("adapter_path") or "")
        if not declared:
            raise ValueError(f"external metric execution guard lacks adapter: {plugin_name}")
        destination_adapter, replacement_count = _rewrite_release_paths(
            declared,
            old_release_root=old_release_root,
            new_release_root=new_release_root,
        )
        if replacement_count != 1:
            raise ValueError(f"external metric adapter is not release-bound: {plugin_name}")
        existing_adapter = str(command[int(adapter_index)])
        if PureWindowsPath(existing_adapter).name.casefold() != PureWindowsPath(
            destination_adapter
        ).name.casefold():
            raise ValueError(f"external metric registry adapter identity differs: {plugin_name}")
        command[int(adapter_index)] = destination_adapter

    content = yaml.safe_dump(
        migrated,
        allow_unicode=True,
        sort_keys=True,
    ).encode("utf-8")
    destination_sha256 = sha256_bytes(content)
    destination_path = (
        Path(registry_cache_root) / destination_sha256 / "runtime.local.yaml"
    )
    return FrozenExternalMetricRegistryMigration(
        source_path=source_path,
        source_sha256=source_sha256,
        destination_path=str(destination_path),
        destination_sha256=destination_sha256,
        content=content,
        plugin_names=tuple(sorted(external)),
    )


def _require_sha256(value: object, label: str) -> str:
    normalized = str(value).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return normalized


def _require_revision(value: object) -> str:
    normalized = str(value).lower()
    if not re.fullmatch(r"[0-9a-f]{40}", normalized):
        raise ValueError("source revision must be a lowercase 40-character Git revision")
    return normalized


def _rewrite_release_paths(
    value: Any,
    *,
    old_release_root: str,
    new_release_root: str,
) -> tuple[Any, int]:
    count = 0
    old_variants = {
        old_release_root.rstrip("\\/"),
        old_release_root.rstrip("\\/").replace("\\", "/"),
    }

    def rewrite(item: Any) -> Any:
        nonlocal count
        if isinstance(item, str):
            rewritten = item
            for old in sorted(old_variants, key=len, reverse=True):
                pattern = re.compile(re.escape(old), re.IGNORECASE)
                rewritten, replacements = pattern.subn(lambda _: new_release_root, rewritten)
                count += replacements
            return rewritten
        if isinstance(item, list):
            return [rewrite(child) for child in item]
        if isinstance(item, tuple):
            return tuple(rewrite(child) for child in item)
        if isinstance(item, dict):
            rewritten = {key: rewrite(child) for key, child in item.items()}
            if "runtime_identity_sha256" in rewritten:
                payload = {
                    key: child
                    for key, child in rewritten.items()
                    if key != "runtime_identity_sha256"
                }
                rewritten["runtime_identity_sha256"] = sha256_json(payload)
            return rewritten
        return item

    return rewrite(value), count


def _contains_release_root(value: Any, release_root: str) -> bool:
    roots = {
        release_root.rstrip("\\/").lower(),
        release_root.rstrip("\\/").replace("\\", "/").lower(),
    }
    if isinstance(value, str):
        lowered = value.lower()
        return any(root in lowered for root in roots)
    if isinstance(value, (list, tuple)):
        return any(_contains_release_root(child, release_root) for child in value)
    if isinstance(value, dict):
        return any(_contains_release_root(child, release_root) for child in value.values())
    return False


def assess_cpu_only_successor_runtime_readiness(
    *,
    request: dict[str, Any],
    freeze_receipt: dict[str, Any],
    predecessor_database_status: str,
    predecessor_temporal_status: str,
    existing_successor_count: int,
    live_pollers: dict[str, dict[str, int]],
    release_paths_verified: bool,
) -> CpuOnlySuccessorRuntimeReadiness:
    """Fail closed unless a frozen successor can progress without GPU work."""

    _validate_request(request)
    queues = request.get("task_queues") or {}
    control_queue = str(queues.get("workflow_and_control") or "")
    persistence_queue = str(queues.get("persistence") or "")
    metrics_queue = str(queues.get("sequence_metrics") or "")
    required = {
        control_queue: {"workflow": 1, "activity": 1},
        persistence_queue: {"activity": 1},
        metrics_queue: {"activity": 1},
    }
    reasons: list[str] = []
    if predecessor_database_status.lower() != "failed":
        reasons.append("predecessor_database_not_failed")
    if predecessor_temporal_status.upper() != "FAILED":
        reasons.append("predecessor_temporal_not_failed")
    if int(existing_successor_count) != 0:
        reasons.append("successor_already_exists")
    planner_contract = (request.get("planner_provider") or {}).get("planner_contract") or {}
    if planner_contract.get("pepmlm_targeted_enabled") is not False:
        reasons.append("pepmlm_targeted_not_disabled")
    if request.get("historical_outputs_reused") is not False:
        reasons.append("historical_outputs_reuse_not_disabled")
    if freeze_receipt.get("generator_gpu_work_required") is not False:
        reasons.append("generator_gpu_work_not_explicitly_disabled")
    if freeze_receipt.get("new_gpu_tasks_allowed") is not False:
        reasons.append("new_gpu_tasks_not_explicitly_prohibited")
    if freeze_receipt.get("submitted") is not False:
        reasons.append("freeze_receipt_already_submitted")
    if not release_paths_verified:
        reasons.append("release_paths_not_verified")
    for queue, kinds in required.items():
        observed = live_pollers.get(queue) or {}
        for kind, minimum in kinds.items():
            if int(observed.get(kind, 0)) < minimum:
                reasons.append(f"missing_live_{kind}_poller:{queue}")
    return CpuOnlySuccessorRuntimeReadiness(
        ready_to_submit=not reasons,
        reason_codes=tuple(reasons),
        required_live_pollers=required,
    )


def freeze_cpu_only_successor(
    *,
    predecessor_request: dict[str, Any],
    predecessor_run_id: UUID | str,
    predecessor_request_sha256: str,
    latest_generation: int,
    source_revision: str,
    release_sha256: str,
    old_release_root: str,
    new_release_root: str,
    eligibility_sha256: str,
    external_metric_registry_migration: FrozenExternalMetricRegistryMigration | None = None,
) -> FrozenAutoResearchSuccessor:
    """Freeze a deterministic CPU-only successor without submitting it.

    The function is deliberately pure: it performs no database, Temporal, GPU,
    filesystem, or process operation. Historical outputs are identity evidence
    only and are never imported into the successor request.
    """

    predecessor_sha = _require_sha256(predecessor_request_sha256, "predecessor request")
    if sha256_json(predecessor_request) != predecessor_sha:
        raise ValueError("predecessor request hash does not match the supplied request")
    predecessor_id = UUID(str(predecessor_run_id))
    predecessor_request_id = UUID(str(predecessor_request.get("run_id")))
    if predecessor_request_id != predecessor_id:
        raise ValueError("predecessor request run_id differs from predecessor_run_id")
    if int(latest_generation) < 0:
        raise ValueError("latest generation must be non-negative")
    branch_key = str(predecessor_request.get("branch_key") or "").lower()
    if branch_key not in BRANCH_KEYS:
        raise ValueError(f"unsupported AutoResearch branch: {branch_key}")
    revision = _require_revision(source_revision)
    release = _require_sha256(release_sha256, "release archive")
    eligibility = _require_sha256(eligibility_sha256, "successor eligibility")
    old_root = str(old_release_root).strip().rstrip("\\/")
    new_root = str(new_release_root).strip().rstrip("\\/")
    if not old_root or not new_root or old_root.lower() == new_root.lower():
        raise ValueError("old and new release roots must be non-empty and different")

    predecessor_control_environment = predecessor_request.get("control_environment_sha256")
    predecessor_executor = predecessor_request.get("action_executor") or {}
    predecessor_operator_environment = predecessor_executor.get("operator_environment_sha256")
    predecessor_target = {
        "target_sequence": predecessor_executor.get("target_sequence"),
        "target_sequence_sha256": predecessor_executor.get("target_sequence_sha256"),
    }

    template, rewrite_count = _rewrite_release_paths(
        copy.deepcopy(predecessor_request),
        old_release_root=old_root,
        new_release_root=new_root,
    )
    if rewrite_count < 1:
        raise ValueError("predecessor request contains no path under the old release root")
    template.pop("run_id", None)
    template.pop("initial_action_plan", None)
    template["predecessor_run_id"] = str(predecessor_id)
    predecessor_start_iteration = int(predecessor_request.get("start_iteration_no", 0))
    start_iteration_no = max(int(latest_generation) + 1, predecessor_start_iteration)
    template["start_iteration_no"] = start_iteration_no
    template["maximum_iterations_per_workflow_execution"] = 2
    template["historical_outputs_reused"] = False
    template["successor_eligibility_sha256"] = eligibility
    queues = dict(template.get("task_queues") or {})
    queues.update(
        {
            "workflow_and_control": CPU_SUCCESSOR_CONTROL_QUEUE,
            "action_execution": CPU_SUCCESSOR_NO_GPU_QUEUE,
            "sequence_metrics": CPU_SUCCESSOR_METRICS_QUEUE,
            "persistence": CPU_SUCCESSOR_PERSISTENCE_QUEUE,
        }
    )
    template["task_queues"] = queues
    provider = copy.deepcopy(template.get("planner_provider") or {})
    provider["task_queue"] = CPU_SUCCESSOR_CONTROL_QUEUE
    planner_contract = dict(provider.get("planner_contract") or {})
    planner_contract["pepmlm_targeted_enabled"] = False
    provider["planner_contract"] = planner_contract
    template["planner_provider"] = provider
    executor = dict(template.get("action_executor") or {})
    executor["operator_release_sha256"] = release
    template["action_executor"] = executor

    registry_bound_plugins = {
        str(name): descriptor
        for name, descriptor in (template.get("metric_plugins_by_name") or {}).items()
        if isinstance(descriptor, dict) and descriptor.get("registry_path")
    }
    if registry_bound_plugins:
        migration = external_metric_registry_migration
        if migration is None:
            raise ValueError("external metric registry migration is required")
        if set(registry_bound_plugins) != set(migration.plugin_names):
            raise ValueError("external metric registry migration plugin set differs")
        for descriptor in registry_bound_plugins.values():
            if str(descriptor.get("registry_path")) != migration.source_path:
                raise ValueError("external metric registry source path differs")
            if str(descriptor.get("registry_sha256")) != migration.source_sha256:
                raise ValueError("external metric registry source hash differs")
            descriptor["registry_path"] = migration.destination_path
            descriptor["registry_sha256"] = migration.destination_sha256
            descriptor["runtime_identity_sha256"] = sha256_json(
                {
                    key: value
                    for key, value in descriptor.items()
                    if key != "runtime_identity_sha256"
                }
            )

    if _contains_release_root(template, old_root):
        raise ValueError("old release root remains after successor path migration")
    if template.get("control_environment_sha256") != predecessor_control_environment:
        raise ValueError("control environment identity changed during successor freeze")
    if executor.get("operator_environment_sha256") != predecessor_operator_environment:
        raise ValueError("generator environment identity changed during successor freeze")
    if {key: executor.get(key) for key in predecessor_target} != predecessor_target:
        raise ValueError("target identity changed during successor freeze")

    request_template_sha256 = sha256_json(template)
    run_identity = {
        "schema_version": "ampgent.autoresearch-cpu-successor-run-identity.1",
        "branch_key": branch_key,
        "predecessor_run_id": str(predecessor_id),
        "predecessor_request_sha256": predecessor_sha,
        "request_template_sha256": request_template_sha256,
        "source_revision": revision,
        "release_sha256": release,
        "control_environment_sha256": predecessor_control_environment,
        "generator_environment_sha256": predecessor_operator_environment,
        "eligibility_sha256": eligibility,
        "historical_outputs_reused": False,
        "generator_gpu_work_required": False,
        "new_gpu_tasks_allowed": False,
    }
    run_identity_sha256 = sha256_json(run_identity)
    run_id = uuid.uuid5(RUN_ID_NAMESPACE, run_identity_sha256)
    request = {**template, "run_id": str(run_id)}
    _validate_request(request)
    request_sha256 = sha256_json(request)
    formal_identity = {
        **run_identity,
        "schema_version": "ampgent.autoresearch-cpu-successor-submission-identity.1",
        "run_identity_sha256": run_identity_sha256,
        "request_sha256": request_sha256,
        "run_id": str(run_id),
    }
    formal_submission_key = sha256_json(formal_identity)
    workflow_uuid = uuid.uuid5(WORKFLOW_ID_NAMESPACE, formal_submission_key)
    workflow_id = f"pepagent-autoresearch-cpu-successor-v1-{branch_key}-{workflow_uuid}"
    receipt = {
        "schema_version": "ampgent.autoresearch-cpu-successor-freeze-receipt.1",
        "branch_key": branch_key,
        "predecessor_run_id": str(predecessor_id),
        "predecessor_request_sha256": predecessor_sha,
        "run_id": str(run_id),
        "workflow_id": workflow_id,
        "request_sha256": request_sha256,
        "request_template_sha256": request_template_sha256,
        "run_identity_sha256": run_identity_sha256,
        "formal_submission_key": formal_submission_key,
        "source_revision": revision,
        "release_sha256": release,
        "eligibility_sha256": eligibility,
        "predecessor_start_iteration_no": predecessor_start_iteration,
        "latest_persisted_generation": int(latest_generation),
        "start_iteration_no": start_iteration_no,
        "release_path_rewrite_count": rewrite_count,
        "historical_outputs_reused": False,
        "generator_gpu_work_required": False,
        "new_gpu_tasks_allowed": False,
        "submitted": False,
    }
    if external_metric_registry_migration is not None:
        receipt["external_metric_registry_migration"] = {
            "source_path": external_metric_registry_migration.source_path,
            "source_sha256": external_metric_registry_migration.source_sha256,
            "destination_path": external_metric_registry_migration.destination_path,
            "destination_sha256": external_metric_registry_migration.destination_sha256,
            "plugin_names": list(external_metric_registry_migration.plugin_names),
        }
    return FrozenAutoResearchSuccessor(
        request=request,
        request_sha256=request_sha256,
        request_template_sha256=request_template_sha256,
        run_identity_sha256=run_identity_sha256,
        formal_submission_key=formal_submission_key,
        run_id=run_id,
        workflow_id=workflow_id,
        receipt=receipt,
    )
