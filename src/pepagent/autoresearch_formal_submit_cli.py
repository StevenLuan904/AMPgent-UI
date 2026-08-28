from __future__ import annotations

import argparse
import asyncio
import copy
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from pepagent.autoresearch_score_ingest import safe_relative_score_bundle_path
from pepagent.db.models import Artifact, ExperimentRun, Target
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import (
    sha256_bytes,
    sha256_file,
    sha256_json,
    sha256_text,
)
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore, StoredObject
from pepagent.v37_runtime_execution import (
    V37GenericRuntimeExpectation,
    V37GenericRuntimePaths,
    build_v37_generic_launch_receipt,
)
from pepagent.workflows.autoresearch import _validate_request

CONFIG_SCHEMA = "ampgent.autoresearch-formal-six-branch-submission.2"
PREFLIGHT_SCHEMA = "ampgent.autoresearch-formal-six-branch-preflight.2"
WORKFLOW_REQUEST_SCHEMA = "ampgent.autoresearch-workflow-request.1"
WORKFLOW_TYPE = "AutoResearchClosedLoopWorkflow"
WORKFLOW_MEMO_KEY = "ampgent_autoresearch_formal_submission_identity"

CONTROL_QUEUE = "pepagent-autoresearch-control-v1"
GENERATOR_QUEUE = "pepagent-autoresearch-generator-v1"
METRICS_QUEUE = "pepagent-autoresearch-metrics-v1"
BRANCH_KEYS = ("acea", "gyra", "pbp2a", "vegfa", "fgf2", "angpt1")
METRIC_PLUGIN_NAMES = frozenset(
    {
        "hemolysis_risk",
        "mic_potency",
        "mic_potency_amp_read",
        "physicochemical_developability",
        "toxicity_risk",
    }
)
PHYSICOCHEMICAL_RUNTIME_ID = (
    "physicochemical-developability-modlamp-4.3.2-biopython-v39"
)
PHYSICOCHEMICAL_METHOD_VERSION = "2026.08.22-v2"
PHYSICOCHEMICAL_ADAPTER_NAME = "no_site_bootstrap.py"

PEPMLM_REVISION = "898fca941a9057aebdd1a6164b5ee09a1a71780e"
PEPMLM_WEIGHTS_SHA256 = "8a3225bca1f9acd9f701ca2e46597c12bab92320e32b68f380ddf3b6d3b20770"
RUN_ID_NAMESPACE = UUID("422d2f23-c894-4b10-a22e-f499893e1981")
WORKFLOW_ID_NAMESPACE = UUID("8bb6713d-4ef7-4977-b141-194745111dbc")
TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
RECOVERABLE_RUN_STATUSES = frozenset({"created", "running", "waiting"})
ACTOR = "autoresearch-formal-six-branch-submit-cli"


@dataclass(frozen=True)
class AutoResearchFormalBranch:
    branch_key: str
    target_id: UUID
    target_sequence: str
    target_sequence_sha256: str
    request: dict[str, Any]
    request_bytes: bytes
    request_sha256: str
    request_template_sha256: str
    run_identity_sha256: str
    formal_submission_key: str
    run_id: UUID
    workflow_id: str
    seed: dict[str, Any]
    continuation_policy: dict[str, Any]
    parent_run_id: UUID | None


@dataclass(frozen=True)
class AutoResearchFormalPlan:
    config: dict[str, Any]
    preflight: dict[str, Any]
    config_sha256: str
    preflight_sha256: str
    target_manifest_sha256: str
    source_revision: str
    release_sha256: str
    release_archive_path: Path
    release_root: Path
    control_environment_sha256: str
    generator_environment_sha256: str
    metric_runtime_snapshot_sha256: str
    branches: tuple[AutoResearchFormalBranch, ...]


@dataclass(frozen=True)
class AutoResearchFormalReservation:
    created: bool
    branch_specs: dict[str, dict[str, Any]]
    request_artifacts: dict[str, StoredObject]

    def summary(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "recovered": not self.created,
            "branch_count": len(self.branch_specs),
            "branches": [
                {
                    "branch_key": key,
                    "run_id": spec["run_id"],
                    "workflow_id": spec["workflow_id"],
                    "formal_submission_key": spec["formal_submission_key"],
                    "request_artifact_sha256": spec["workflow_request_artifact"]["sha256"],
                }
                for key, spec in sorted(self.branch_specs.items())
            ],
        }


@dataclass(frozen=True)
class TemporalWorkflowBinding:
    handle: WorkflowHandle
    temporal_run_id: str
    recovered: bool


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def _normalize_receipt_mapping(value: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, str) and item.lower() in {"true", "false"}:
            normalized[str(key)] = item.lower() == "true"
        else:
            normalized[str(key)] = item
    return normalized


def _load_worker_receipt(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8").lstrip("\ufeff")
    if raw.lstrip().startswith("{"):
        return _normalize_receipt_mapping(_load_json(path))
    receipt: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        if "=" not in line:
            raise ValueError(f"worker receipt line has no key/value delimiter: {line}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in receipt:
            raise ValueError("worker receipt has an empty or duplicate key")
        receipt[key] = value.strip()
    if not receipt:
        raise ValueError("worker receipt is empty")
    return _normalize_receipt_mapping(receipt)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_sha256(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64:
        raise ValueError(f"{field_name} must be a 64-character SHA-256 digest")
    try:
        int(normalized, 16)
    except ValueError as error:
        raise ValueError(f"{field_name} must be hexadecimal") from error
    return normalized


def _require_revision(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 40:
        raise ValueError(f"{field_name} must be a frozen 40-character Git revision")
    try:
        int(normalized, 16)
    except ValueError as error:
        raise ValueError(f"{field_name} must be hexadecimal") from error
    return normalized


def _resolve_existing_path(base_path: Path, value: Any, field_name: str) -> Path:
    raw = Path(str(value or ""))
    candidates = [raw] if raw.is_absolute() else [base_path / raw, Path.cwd() / raw]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError(f"{field_name} does not resolve to a file: {value}")


def _resolve_existing_directory(base_path: Path, value: Any, field_name: str) -> Path:
    raw = Path(str(value or ""))
    candidates = [raw] if raw.is_absolute() else [base_path / raw, Path.cwd() / raw]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    raise ValueError(f"{field_name} does not resolve to a directory: {value}")


def _require_path_within_release(path: Path, release_root: Path, *, field_name: str) -> Path:
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(release_root)
    except ValueError as error:
        raise ValueError(f"{field_name} is not bound to the immutable release root") from error
    return resolved


def _require_content_addressed_file(
    path: Path,
    *,
    expected_sha256: str,
    field_name: str,
) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError(f"{field_name} is not a regular immutable file")
    if (
        resolved.name.casefold() != "runtime.local.yaml"
        or resolved.parent.name.casefold() != expected_sha256.casefold()
    ):
        raise ValueError(
            f"{field_name} must be <registry-sha256>/runtime.local.yaml"
        )
    if sha256_file(resolved) != expected_sha256:
        raise ValueError(f"{field_name} bytes drifted before submission")
    return resolved


def _same_resolved_path(first: Any, second: Any) -> bool:
    try:
        return Path(str(first)).resolve(strict=True) == Path(str(second)).resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return False


def _verify_release_installation(
    *,
    release: dict[str, Any],
    source_revision: str,
    config_base_path: Path,
) -> tuple[Path, Path, str]:
    """Verify the local immutable release bytes used by a formal submission.

    A release archive hash alone does not prove which extracted files the request
    references.  Formal v2 configs therefore bind both the archive and its
    content-addressed extraction root.  The exact runtime files under that root
    are rehashed separately below.
    """

    release_sha256 = _require_sha256(release.get("archive_sha256"), "formal release archive")
    archive_path = _resolve_existing_path(
        config_base_path,
        release.get("archive_path_or_uri"),
        "formal release archive path",
    )
    if archive_path.is_symlink() or sha256_file(archive_path) != release_sha256:
        raise ValueError("formal release archive bytes drifted")
    release_root = _resolve_existing_directory(
        config_base_path,
        release.get("extracted_root"),
        "formal extracted release root",
    )
    if release_root.is_symlink() or release_root.name.lower() != release_sha256:
        raise ValueError("formal extracted release root is not content-addressed by the archive")
    source_marker = release_root / ".pepagent-source-revision"
    if not source_marker.is_file() or source_marker.is_symlink():
        raise ValueError("formal extracted release has no immutable source revision marker")
    if source_marker.read_text(encoding="utf-8").strip().lower() != source_revision:
        raise ValueError("formal extracted release source revision drifted")
    return archive_path, release_root, release_sha256


def _verify_metric_runtime_registry_live(
    *,
    plugin_registry: dict[str, Any],
    release_root: Path,
) -> dict[str, Any]:
    """Rehash every byte consumed by all five metric providers.

    Repository-owned adapters, manifests, locks, and the working directory must
    point into the immutable release.  A machine-specific runtime registry may
    instead use a SHA-named content-addressed path.  Provider executables, source
    checkouts, and model roots may be external frozen installations, but their
    complete declared inventories are rehashed by the generic v37 guard.
    """

    if set(plugin_registry) != METRIC_PLUGIN_NAMES:
        raise ValueError("formal metric runtime registry must contain the exact five plugins")
    release_root = release_root.resolve(strict=True)
    plugin_snapshots: dict[str, Any] = {}
    for plugin_name in sorted(plugin_registry):
        descriptor = plugin_registry[plugin_name]
        if not isinstance(descriptor, dict):
            raise ValueError(f"{plugin_name} metric runtime descriptor is not an object")
        runtime_identity_sha256 = _require_sha256(
            descriptor.get("runtime_identity_sha256"),
            f"{plugin_name} runtime identity",
        )
        descriptor_identity = {
            key: value for key, value in descriptor.items() if key != "runtime_identity_sha256"
        }
        if sha256_json(descriptor_identity) != runtime_identity_sha256:
            raise ValueError(f"{plugin_name} metric runtime descriptor self-hash drifted")
        if descriptor.get("plugin_name") != plugin_name or descriptor.get("name") != plugin_name:
            raise ValueError(f"{plugin_name} metric runtime plugin identity drifted")
        guard = descriptor.get("execution_guard")
        if not isinstance(guard, dict) or set(guard) != {"contract", "expectation", "paths"}:
            raise ValueError(f"{plugin_name} metric runtime execution guard is incomplete")
        contract = guard["contract"]
        expectation_payload = guard["expectation"]
        paths_payload = guard["paths"]
        if not isinstance(contract, dict) or not isinstance(expectation_payload, dict):
            raise ValueError(f"{plugin_name} metric runtime contract is incomplete")
        if not isinstance(paths_payload, dict) or set(paths_payload) != {
            "adapter_path",
            "executable_path",
            "model_root",
            "packages_lock_path",
            "runtime_manifest_path",
            "source_root",
        }:
            raise ValueError(f"{plugin_name} metric runtime path bindings are incomplete")
        if set(expectation_payload) != {"execution_contract_sha256", "runtime_id"}:
            raise ValueError(f"{plugin_name} metric runtime expectation is incomplete")
        expectation = V37GenericRuntimeExpectation(
            runtime_id=str(expectation_payload["runtime_id"]),
            execution_contract_sha256=_require_sha256(
                expectation_payload["execution_contract_sha256"],
                f"{plugin_name} execution contract",
            ),
        )
        if descriptor.get("runtime_id") != expectation.runtime_id:
            raise ValueError(f"{plugin_name} metric runtime identity drifted")
        for descriptor_field, guard_field in {
            "adapter_path": "adapter_path",
            "python_path": "executable_path",
            "runtime_manifest_path": "runtime_manifest_path",
            "packages_lock_path": "packages_lock_path",
            "source_root": "source_root",
            "model_root": "model_root",
        }.items():
            if descriptor_field in descriptor and not _same_resolved_path(
                descriptor[descriptor_field], paths_payload[guard_field]
            ):
                raise ValueError(
                    f"{plugin_name} top-level {descriptor_field} differs from its execution guard"
                )
        if not _same_resolved_path(descriptor.get("cwd"), release_root):
            raise ValueError(f"{plugin_name} working directory is not the immutable release root")

        paths = V37GenericRuntimePaths(
            executable_path=Path(str(paths_payload["executable_path"])),
            runtime_manifest_path=Path(str(paths_payload["runtime_manifest_path"])),
            packages_lock_path=Path(str(paths_payload["packages_lock_path"])),
            source_root=Path(str(paths_payload["source_root"])),
            model_root=Path(str(paths_payload["model_root"])),
            adapter_path=(
                Path(str(paths_payload["adapter_path"]))
                if paths_payload["adapter_path"] is not None
                else None
            ),
        )
        if paths.adapter_path is None:
            raise ValueError(f"{plugin_name} formal metric runtime has no frozen adapter")
        for path, field_name in (
            (paths.adapter_path, f"{plugin_name} adapter"),
            (paths.runtime_manifest_path, f"{plugin_name} runtime manifest"),
            (paths.packages_lock_path, f"{plugin_name} package lock"),
        ):
            _require_path_within_release(path, release_root, field_name=field_name)
        registry_path_value = descriptor.get("registry_path")
        if registry_path_value is not None:
            registry_sha256 = _require_sha256(
                descriptor.get("registry_sha256"), f"{plugin_name} registry"
            )
            registry_path = Path(str(registry_path_value)).resolve(strict=True)
            try:
                _require_path_within_release(
                    registry_path,
                    release_root,
                    field_name=f"{plugin_name} registry",
                )
            except ValueError:
                _require_content_addressed_file(
                    registry_path,
                    expected_sha256=registry_sha256,
                    field_name=f"{plugin_name} registry",
                )
            if sha256_file(registry_path) != registry_sha256:
                raise ValueError(f"{plugin_name} live registry bytes drifted before submission")
        if descriptor.get("runtime_manifest_sha256") != contract.get(
            "runtime_manifest_sha256"
        ):
            raise ValueError(f"{plugin_name} runtime manifest identity drifted")
        entities = contract.get("command_entities") or {}
        executable_index = int(entities.get("executable_index", -1))
        adapter_index_value = entities.get("adapter_index")
        adapter_index = int(adapter_index_value) if adapter_index_value is not None else None
        if executable_index != 0 or adapter_index not in {1, 2}:
            raise ValueError(f"{plugin_name} formal metric command mapping is invalid")
        command = ["formal-runtime-entity"] * (max(executable_index, adapter_index) + 1)
        command[executable_index] = str(paths.executable_path.resolve(strict=True))
        command[adapter_index] = str(paths.adapter_path.resolve(strict=True))
        if adapter_index == 2:
            command[1] = "-S"
        receipt = build_v37_generic_launch_receipt(
            contract=contract,
            expectation=expectation,
            paths=paths,
            command=command,
            cwd=release_root,
            env={},
            input_paths={},
            stage="pre_snapshot",
        )
        runtime_manifest = _load_json(paths.runtime_manifest_path)
        if runtime_manifest.get("metric_plugin") != plugin_name:
            raise ValueError(f"{plugin_name} runtime manifest plugin identity drifted")
        if plugin_name == "physicochemical_developability":
            implementation = runtime_manifest.get("implementation") or {}
            adapter = contract.get("adapter") or {}
            if (
                expectation.runtime_id != PHYSICOCHEMICAL_RUNTIME_ID
                or runtime_manifest.get("runtime_id") != PHYSICOCHEMICAL_RUNTIME_ID
                or implementation.get("method_version") != PHYSICOCHEMICAL_METHOD_VERSION
                or not str(implementation.get("adapter") or "").endswith(
                    PHYSICOCHEMICAL_ADAPTER_NAME
                )
                or not str(implementation.get("implementation_module") or "").endswith("cli.py")
                or entities.get("adapter_index") != 2
                or adapter.get("path") != PHYSICOCHEMICAL_ADAPTER_NAME
            ):
                raise ValueError(
                    "physicochemical runtime is not the versioned v39/Biopython method-v2 binding"
                )
            _require_path_within_release(
                paths.source_root,
                release_root,
                field_name="physicochemical source release",
            )
            _require_path_within_release(
                paths.model_root,
                release_root,
                field_name="physicochemical model release",
            )
        plugin_snapshots[plugin_name] = {
            "runtime_id": expectation.runtime_id,
            "runtime_identity_sha256": runtime_identity_sha256,
            "execution_contract_sha256": expectation.execution_contract_sha256,
            "byte_identity_sha256": receipt["byte_identity_sha256"],
        }
    identity = {
        "schema_version": "ampgent.metric-runtime-live-snapshot.1",
        "plugin_count": len(plugin_snapshots),
        "plugins": plugin_snapshots,
    }
    return {**identity, "snapshot_sha256": sha256_json(identity)}


def _expected_queues(config: dict[str, Any]) -> dict[str, str]:
    temporal = config.get("temporal") or {}
    expected = {
        "control_queue": CONTROL_QUEUE,
        "generator_queue": GENERATOR_QUEUE,
        "metrics_queue": METRICS_QUEUE,
    }
    if temporal != expected:
        raise ValueError("AutoResearch formal Temporal queues are not the isolated v1 queues")
    return expected


def _identity_seed(seed: dict[str, Any]) -> dict[str, Any]:
    required = {
        "bundle_key",
        "bundle_receipt_path",
        "bundle_receipt_sha256",
        "source_map_path",
        "source_map_sha256",
        "remote_cas_uri",
    }
    if set(seed) != required:
        raise ValueError("AutoResearch branch seed identity fields differ from the contract")
    bundle_receipt_path = safe_relative_score_bundle_path(str(seed["bundle_receipt_path"]))
    source_map_path = safe_relative_score_bundle_path(str(seed["source_map_path"]))
    return {
        "bundle_key": str(seed["bundle_key"]),
        "bundle_receipt_path": bundle_receipt_path,
        "bundle_receipt_sha256": _require_sha256(
            seed["bundle_receipt_sha256"], "seed bundle receipt"
        ),
        "source_map_sha256": _require_sha256(seed["source_map_sha256"], "seed source-map receipt"),
        "source_map_path": source_map_path,
        "remote_cas_uri": str(seed["remote_cas_uri"]),
    }


def derive_autoresearch_branch_identity(
    *,
    config: dict[str, Any],
    branch: dict[str, Any],
    request_template: dict[str, Any],
) -> dict[str, Any]:
    """Derive non-circular immutable identities for one formal branch.

    ``run_id`` is UUID5 over the request *template* and all frozen runtime/model/
    seed identities. The finalized request then contains that run ID. Its SHA is
    included in the formal key, from which the deterministic workflow UUID is
    derived. This avoids making a request hash depend recursively on itself.
    """

    template = copy.deepcopy(request_template)
    template.pop("run_id", None)
    branch_key = str(branch.get("branch_key") or "")
    if branch_key not in BRANCH_KEYS:
        raise ValueError(f"unsupported AutoResearch formal branch: {branch_key}")
    release = config.get("release") or {}
    runtime = config.get("runtime") or {}
    model = config.get("model") or {}
    target_manifest = config.get("target_manifest") or {}
    queues = _expected_queues(config)
    seed_identity = _identity_seed(branch.get("seed") or {})
    predecessor_raw = branch.get("predecessor_run_id")
    predecessor_run_id = UUID(str(predecessor_raw)) if predecessor_raw not in {None, ""} else None
    request_template_sha256 = sha256_json(template)
    run_identity = {
        "schema_version": "ampgent.autoresearch-formal-run-identity.1",
        "branch_key": branch_key,
        "target_id": str(UUID(str(branch["target_id"]))),
        "target_sequence_sha256": _require_sha256(
            branch["target_sequence_sha256"], "branch target sequence"
        ),
        "request_template_sha256": request_template_sha256,
        "source_revision": _require_revision(config["source_revision"], "formal source revision"),
        "release_sha256": _require_sha256(release["archive_sha256"], "formal release archive"),
        "target_manifest_sha256": _require_sha256(target_manifest["sha256"], "target manifest"),
        "control_environment_sha256": _require_sha256(
            runtime["control_environment_sha256"], "control environment"
        ),
        "generator_environment_sha256": _require_sha256(
            runtime["generator_environment_sha256"], "generator environment"
        ),
        "metric_plugin_registry_sha256": _require_sha256(
            runtime["metric_plugin_registry_sha256"], "metric plugin registry"
        ),
        "pepmlm_revision": str(model["pepmlm_revision"]),
        "pepmlm_weights_sha256": _require_sha256(model["pepmlm_weights_sha256"], "PepMLM weights"),
        "queues": queues,
        "seed": seed_identity,
        "continuation_policy": branch["continuation_policy"],
        "predecessor_run_id": (str(predecessor_run_id) if predecessor_run_id is not None else None),
        "historical_outputs_reused": False,
    }
    run_identity_sha256 = sha256_json(run_identity)
    run_id = uuid.uuid5(RUN_ID_NAMESPACE, run_identity_sha256)
    request = {**template, "run_id": str(run_id)}
    request_sha256 = sha256_json(request)
    formal_identity = {
        **run_identity,
        "schema_version": "ampgent.autoresearch-formal-submission-identity.1",
        "run_identity_sha256": run_identity_sha256,
        "request_sha256": request_sha256,
        "run_id": str(run_id),
    }
    formal_submission_key = sha256_json(formal_identity)
    workflow_uuid = uuid.uuid5(WORKFLOW_ID_NAMESPACE, formal_submission_key)
    workflow_id = f"pepagent-autoresearch-v1-{branch_key}-{workflow_uuid}"
    return {
        "run_id": str(run_id),
        "workflow_id": workflow_id,
        "formal_submission_key": formal_submission_key,
        "request": request,
        "request_sha256": request_sha256,
        "request_template_sha256": request_template_sha256,
        "run_identity_sha256": run_identity_sha256,
        "formal_identity": formal_identity,
    }


def _validate_seed_request(
    branch_key: str,
    seed: dict[str, Any],
    request: dict[str, Any],
) -> None:
    imported = request.get("seed_score_bundle_import")
    if not isinstance(imported, dict):
        raise ValueError(f"{branch_key} request has no frozen seed score bundle")
    expected = {
        "bundle_key": str(seed["bundle_key"]),
        "bundle_receipt_path": str(seed["bundle_receipt_path"]),
        "bundle_receipt_sha256": str(seed["bundle_receipt_sha256"]),
        "source_map_receipt_path": str(seed["source_map_path"]),
        "source_map_receipt_sha256": str(seed["source_map_sha256"]),
        "source_map_storage_uri": str(seed["remote_cas_uri"]),
        "target_key": branch_key,
    }
    safe_relative_score_bundle_path(str(imported.get("bundle_receipt_path") or ""))
    safe_relative_score_bundle_path(str(imported.get("source_map_receipt_path") or ""))
    for key, value in expected.items():
        if imported.get(key) != value:
            raise ValueError(f"{branch_key} seed import field drifted: {key}")


def _validate_request_bindings(
    *, config: dict[str, Any], branch: dict[str, Any], request: dict[str, Any]
) -> None:
    branch_key = str(branch["branch_key"])
    predecessor = branch.get("predecessor_run_id")
    expected_predecessor = str(UUID(str(predecessor))) if predecessor not in {None, ""} else None
    if request.get("predecessor_run_id") != expected_predecessor:
        raise ValueError(f"{branch_key} predecessor run identity drifted")
    runtime = config["runtime"]
    release = config["release"]
    model = config["model"]
    queues = request.get("task_queues") or {}
    expected_queues = {
        "workflow_and_control": CONTROL_QUEUE,
        "action_execution": GENERATOR_QUEUE,
        "sequence_metrics": METRICS_QUEUE,
    }
    if queues != expected_queues:
        raise ValueError(f"{branch_key} request uses a non-formal task queue")
    if request.get("branch_key") != branch_key:
        raise ValueError(f"{branch_key} request branch identity drifted")
    if request.get("control_environment_sha256") != runtime["control_environment_sha256"]:
        raise ValueError(f"{branch_key} control environment identity drifted")
    if request.get("metric_plugins_by_name") != runtime["metric_plugins_by_name"]:
        raise ValueError(f"{branch_key} metric plugin registry drifted")
    if request.get("continuation_policy") != branch["continuation_policy"]:
        raise ValueError(f"{branch_key} continuation policy drifted")
    planner = request.get("planner_provider") or {}
    if planner.get("task_queue") != CONTROL_QUEUE:
        raise ValueError(f"{branch_key} planner is not on the formal control queue")
    executor = request.get("action_executor") or {}
    expected_executor = {
        "operator_environment_sha256": runtime["generator_environment_sha256"],
        "operator_release_sha256": release["archive_sha256"],
        "target_sequence": branch["target_sequence"],
        "target_sequence_sha256": branch["target_sequence_sha256"],
        "pepmlm_revision": model["pepmlm_revision"],
        "pepmlm_weights_sha256": model["pepmlm_weights_sha256"],
    }
    for key, value in expected_executor.items():
        if executor.get(key) != value:
            raise ValueError(f"{branch_key} action executor field drifted: {key}")
    _validate_seed_request(branch_key, branch["seed"], request)


def _validate_control_worker_receipt(
    *,
    preflight: dict[str, Any],
    preflight_base_path: Path,
    config: dict[str, Any],
) -> None:
    matching = [
        item
        for item in preflight.get("checks") or []
        if item.get("name") == "control_worker_receipt_identity"
    ]
    if len(matching) != 1 or matching[0].get("status") != "passed":
        raise ValueError("preflight does not prove the local control worker receipt")
    evidence = matching[0].get("evidence") or {}
    if evidence.get("mode") != "autoresearch-local":
        raise ValueError("control worker receipt was not launched in autoresearch-local mode")
    receipt_path = _resolve_existing_path(
        preflight_base_path,
        evidence.get("receipt_path"),
        "control worker receipt path",
    )
    receipt_sha256 = _require_sha256(evidence.get("receipt_sha256"), "control worker receipt")
    if sha256_file(receipt_path) != receipt_sha256:
        raise ValueError("control worker receipt content changed after preflight")
    receipt = _load_worker_receipt(receipt_path)
    embedded = evidence.get("receipt")
    if embedded is not None and _normalize_receipt_mapping(embedded) != receipt:
        raise ValueError("embedded and on-disk control worker receipts differ")
    expected = {
        "schema_version": "v38.local-sequence-worker-receipt.1",
        "role": "autoresearch-control",
        "task_queue": CONTROL_QUEUE,
        "source_revision": config["source_revision"],
        "release_sha256": config["release"]["archive_sha256"],
        "environment_sha256": config["runtime"]["control_environment_sha256"],
        "task_queue_verified_from_release": True,
        "ampgent_owned": True,
        "foreign": False,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ValueError(f"control worker receipt field drifted: {key}")


def _validate_generator_worker_receipt(
    *,
    preflight: dict[str, Any],
    preflight_base_path: Path,
    config: dict[str, Any],
) -> None:
    matching = [
        item
        for item in preflight.get("checks") or []
        if item.get("name") == "generator_worker_receipt_identity"
    ]
    if len(matching) != 1 or matching[0].get("status") != "passed":
        raise ValueError("preflight does not prove the remote generator receipt")
    evidence = matching[0].get("evidence") or {}
    if evidence.get("mode") != "autoresearch-generator":
        raise ValueError("generator receipt was not launched in autoresearch-generator mode")
    receipt_path = _resolve_existing_path(
        preflight_base_path,
        evidence.get("receipt_path"),
        "generator worker receipt path",
    )
    receipt_sha256 = _require_sha256(evidence.get("receipt_sha256"), "generator worker receipt")
    if sha256_file(receipt_path) != receipt_sha256:
        raise ValueError("generator worker receipt content changed after preflight")
    receipt = _load_worker_receipt(receipt_path)
    embedded = evidence.get("receipt")
    if embedded is not None and _normalize_receipt_mapping(embedded) != receipt:
        raise ValueError("embedded and on-disk generator worker receipts differ")
    expected = {
        "schema": "autoresearch.remote-generator-worker-receipt.1",
        "role": "autoresearch-generator",
        "task_queue": GENERATOR_QUEUE,
        "task_queue_verified_from_release": True,
        "physical_host": "192.168.99.32",
        "resource": "1",
        "gpu_preflight": "idle_no_compute_process_or_cuda_declaration",
        "release_sha256": config["release"]["archive_sha256"],
        "source_revision": config["source_revision"],
        "environment_sha256": config["runtime"]["generator_environment_sha256"],
        "service_tunnel_preflight": "passed",
        "model_revision": config["model"]["pepmlm_revision"],
        "weights_sha256": config["model"]["pepmlm_weights_sha256"],
        "ampgent_owned": True,
        "foreign": False,
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ValueError(f"generator worker receipt field drifted: {key}")
    if not str(receipt.get("gpu_uuid") or "").startswith("GPU-"):
        raise ValueError("generator worker receipt has no frozen GPU UUID")
    if not str(receipt.get("pid") or "").isdigit():
        raise ValueError("generator worker receipt has no exact PID")


def _validate_preflight(
    *,
    config: dict[str, Any],
    config_sha256: str,
    preflight: dict[str, Any],
    preflight_base_path: Path,
    branches: tuple[AutoResearchFormalBranch, ...],
    metric_runtime_snapshot_sha256: str,
) -> None:
    if preflight.get("schema_version") != PREFLIGHT_SCHEMA:
        raise ValueError("AutoResearch formal preflight schema is not frozen")
    if preflight.get("status") != "ready" or preflight.get("blockers") != []:
        raise ValueError("AutoResearch formal preflight is not ready")
    if preflight.get("config_sha256") != config_sha256:
        raise ValueError("AutoResearch config changed after preflight")
    if preflight.get("source_revision") != config["source_revision"]:
        raise ValueError("AutoResearch source revision changed after preflight")
    if preflight.get("release_sha256") != config["release"]["archive_sha256"]:
        raise ValueError("AutoResearch release changed after preflight")
    if preflight.get("branch_count") != len(BRANCH_KEYS):
        raise ValueError("AutoResearch preflight does not cover six branches")
    checks = preflight.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("AutoResearch preflight has no machine checks")
    if any(item.get("status") != "passed" for item in checks):
        raise ValueError("AutoResearch preflight contains a non-passing check")
    migration_checks = [item for item in checks if item.get("name") == "database_migration_0016"]
    if len(migration_checks) != 1 or (
        (migration_checks[0].get("evidence") or {}).get("migration") != "0016_autoresearch_evidence"
    ):
        raise ValueError(
            "preflight does not prove remote database migration 0016_autoresearch_evidence"
        )
    expected = {
        item.branch_key: {
            "branch_key": item.branch_key,
            "request_sha256": item.request_sha256,
            "formal_submission_key": item.formal_submission_key,
            "run_id": str(item.run_id),
            "workflow_id": item.workflow_id,
            "seed_receipt_sha256": item.seed["bundle_receipt_sha256"],
            "source_map_sha256": item.seed["source_map_sha256"],
            "status": "ready",
            "predecessor_run_id": (
                str(item.parent_run_id) if item.parent_run_id is not None else None
            ),
            "historical_outputs_reused": False,
        }
        for item in branches
    }
    actual_rows = preflight.get("branches")
    if not isinstance(actual_rows, list) or len(actual_rows) != len(BRANCH_KEYS):
        raise ValueError("AutoResearch preflight branch identities are incomplete")
    actual = {str(item.get("branch_key")): item for item in actual_rows}
    if actual != expected:
        raise ValueError("AutoResearch preflight branch identity drifted")
    runtime_checks = [
        item for item in checks if item.get("name") == "metric_runtime_live_bytes"
    ]
    if len(runtime_checks) != 1:
        raise ValueError("preflight does not prove the five metric runtime byte identities")
    runtime_evidence = runtime_checks[0].get("evidence") or {}
    if runtime_evidence != {
        "schema_version": "ampgent.metric-runtime-live-snapshot.1",
        "snapshot_sha256": metric_runtime_snapshot_sha256,
        "plugin_count": len(METRIC_PLUGIN_NAMES),
        "plugin_names": sorted(METRIC_PLUGIN_NAMES),
    }:
        raise ValueError("preflight metric runtime byte identity drifted")
    _validate_control_worker_receipt(
        preflight=preflight,
        preflight_base_path=preflight_base_path,
        config=config,
    )
    _validate_generator_worker_receipt(
        preflight=preflight,
        preflight_base_path=preflight_base_path,
        config=config,
    )


def build_autoresearch_formal_plan(
    *,
    config: dict[str, Any],
    preflight: dict[str, Any],
    config_base_path: Path,
    preflight_base_path: Path,
) -> AutoResearchFormalPlan:
    if config.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError("AutoResearch formal submission config schema is not frozen")
    source_revision = _require_revision(config.get("source_revision"), "formal source revision")
    release = config.get("release") or {}
    if release.get("source_revision") != source_revision:
        raise ValueError("formal release and source revisions differ")
    release_archive_path, release_root, release_sha256 = _verify_release_installation(
        release=release,
        source_revision=source_revision,
        config_base_path=config_base_path,
    )
    target_manifest_identity = config.get("target_manifest") or {}
    target_manifest_sha256 = _require_sha256(
        target_manifest_identity.get("sha256"), "target manifest"
    )
    target_manifest_path = _resolve_existing_path(
        config_base_path,
        target_manifest_identity.get("path"),
        "target manifest path",
    )
    if sha256_file(target_manifest_path) != target_manifest_sha256:
        raise ValueError("target manifest changed after config freeze")
    target_manifest = _load_json(target_manifest_path)
    manifest_rows = target_manifest.get("targets") or []
    manifest_targets = {str(item["target_key"]): item for item in manifest_rows}
    if len(manifest_rows) != len(BRANCH_KEYS) or set(manifest_targets) != set(BRANCH_KEYS):
        raise ValueError("target manifest does not contain the exact six branches")
    runtime = config.get("runtime") or {}
    control_environment_sha256 = _require_sha256(
        runtime.get("control_environment_sha256"), "control environment"
    )
    generator_environment_sha256 = _require_sha256(
        runtime.get("generator_environment_sha256"), "generator environment"
    )
    plugin_registry = runtime.get("metric_plugins_by_name")
    if not isinstance(plugin_registry, dict) or not plugin_registry:
        raise ValueError("formal metric plugin registry is empty")
    if sha256_json(plugin_registry) != _require_sha256(
        runtime.get("metric_plugin_registry_sha256"), "metric plugin registry"
    ):
        raise ValueError("formal metric plugin registry SHA differs from its payload")
    metric_runtime_snapshot = _verify_metric_runtime_registry_live(
        plugin_registry=plugin_registry,
        release_root=release_root,
    )
    metric_runtime_snapshot_sha256 = str(metric_runtime_snapshot["snapshot_sha256"])
    _expected_queues(config)
    model = config.get("model") or {}
    if model != {
        "pepmlm_revision": PEPMLM_REVISION,
        "pepmlm_weights_sha256": PEPMLM_WEIGHTS_SHA256,
    }:
        raise ValueError("formal PepMLM model identity differs from the frozen release")
    config_branches = config.get("branches")
    if not isinstance(config_branches, list) or len(config_branches) != len(BRANCH_KEYS):
        raise ValueError("formal config must contain exactly six branches")
    if [item.get("branch_key") for item in config_branches] != list(BRANCH_KEYS):
        raise ValueError("formal branches must use canonical order and lowercase keys")
    built: list[AutoResearchFormalBranch] = []
    for branch in config_branches:
        branch_key = str(branch["branch_key"])
        manifest_item = manifest_targets[branch_key]
        target_sequence = "".join(str(branch.get("target_sequence") or "").split()).upper()
        target_sequence_sha256 = _require_sha256(
            branch.get("target_sequence_sha256"), f"{branch_key} target sequence"
        )
        if (
            target_sequence != manifest_item["sequence"]
            or target_sequence_sha256 != manifest_item["sequence_sha256"]
            or sha256_text(target_sequence) != target_sequence_sha256
        ):
            raise ValueError(f"{branch_key} target sequence differs from the manifest")
        request_path = _resolve_existing_path(
            config_base_path, branch.get("request_path"), f"{branch_key} request path"
        )
        loaded_request = _load_json(request_path)
        derived = derive_autoresearch_branch_identity(
            config=config,
            branch=branch,
            request_template=loaded_request,
        )
        if loaded_request != derived["request"]:
            raise ValueError(f"{branch_key} request run identity is not deterministic")
        expected_config_identity = {
            "request_sha256": derived["request_sha256"],
            "formal_submission_key": derived["formal_submission_key"],
            "run_id": derived["run_id"],
            "workflow_id": derived["workflow_id"],
        }
        for key, value in expected_config_identity.items():
            if branch.get(key) != value:
                raise ValueError(f"{branch_key} config identity field drifted: {key}")
        _validate_request_bindings(config=config, branch=branch, request=loaded_request)
        _validate_request(loaded_request)
        request_bytes = _canonical_json_bytes(loaded_request)
        if sha256_bytes(request_bytes) != derived["request_sha256"]:
            raise AssertionError("canonical request serialization is not stable")
        built.append(
            AutoResearchFormalBranch(
                branch_key=branch_key,
                target_id=UUID(str(branch["target_id"])),
                target_sequence=target_sequence,
                target_sequence_sha256=target_sequence_sha256,
                request=loaded_request,
                request_bytes=request_bytes,
                request_sha256=derived["request_sha256"],
                request_template_sha256=derived["request_template_sha256"],
                run_identity_sha256=derived["run_identity_sha256"],
                formal_submission_key=derived["formal_submission_key"],
                run_id=UUID(derived["run_id"]),
                workflow_id=derived["workflow_id"],
                seed=copy.deepcopy(branch["seed"]),
                continuation_policy=copy.deepcopy(branch["continuation_policy"]),
                parent_run_id=(
                    UUID(str(branch["predecessor_run_id"]))
                    if branch.get("predecessor_run_id") not in {None, ""}
                    else None
                ),
            )
        )
    branches = tuple(built)
    for field_name, identities in {
        "target_id": {item.target_id for item in branches},
        "run_id": {item.run_id for item in branches},
        "workflow_id": {item.workflow_id for item in branches},
        "formal_submission_key": {item.formal_submission_key for item in branches},
    }.items():
        if len(identities) != len(BRANCH_KEYS):
            raise ValueError(f"formal branch {field_name} identities are not unique")
    config_sha256 = sha256_json(config)
    _validate_preflight(
        config=config,
        config_sha256=config_sha256,
        preflight=preflight,
        preflight_base_path=preflight_base_path,
        branches=branches,
        metric_runtime_snapshot_sha256=metric_runtime_snapshot_sha256,
    )
    return AutoResearchFormalPlan(
        config=copy.deepcopy(config),
        preflight=copy.deepcopy(preflight),
        config_sha256=config_sha256,
        preflight_sha256=sha256_json(preflight),
        target_manifest_sha256=target_manifest_sha256,
        source_revision=source_revision,
        release_sha256=release_sha256,
        release_archive_path=release_archive_path,
        release_root=release_root,
        control_environment_sha256=control_environment_sha256,
        generator_environment_sha256=generator_environment_sha256,
        metric_runtime_snapshot_sha256=metric_runtime_snapshot_sha256,
        branches=branches,
    )


def load_autoresearch_formal_plan(
    *, config_path: Path, preflight_path: Path
) -> AutoResearchFormalPlan:
    config_path = config_path.resolve()
    preflight_path = preflight_path.resolve()
    return build_autoresearch_formal_plan(
        config=_load_json(config_path),
        preflight=_load_json(preflight_path),
        config_base_path=config_path.parent,
        preflight_base_path=preflight_path.parent,
    )


def _revalidate_plan_runtime_boundary(plan: AutoResearchFormalPlan) -> None:
    """Fail before a durable mutation if release or provider bytes changed."""

    if (
        not plan.release_archive_path.is_file()
        or plan.release_archive_path.is_symlink()
        or sha256_file(plan.release_archive_path) != plan.release_sha256
    ):
        raise ValueError("formal release archive bytes drifted before submission")
    if (
        not plan.release_root.is_dir()
        or plan.release_root.is_symlink()
        or plan.release_root.name.lower() != plan.release_sha256
    ):
        raise ValueError("formal extracted release root drifted before submission")
    marker = plan.release_root / ".pepagent-source-revision"
    if (
        not marker.is_file()
        or marker.is_symlink()
        or marker.read_text(encoding="utf-8").strip().lower() != plan.source_revision
    ):
        raise ValueError("formal extracted release source revision drifted before submission")
    snapshot = _verify_metric_runtime_registry_live(
        plugin_registry=plan.config["runtime"]["metric_plugins_by_name"],
        release_root=plan.release_root,
    )
    if snapshot["snapshot_sha256"] != plan.metric_runtime_snapshot_sha256:
        raise ValueError("formal metric runtime bytes drifted after plan validation")


def _advisory_lock_id(config_sha256: str) -> int:
    return int.from_bytes(bytes.fromhex(config_sha256)[:8], byteorder="big", signed=True)


def _artifact_payload(stored: StoredObject) -> dict[str, Any]:
    return {
        "sha256": stored.sha256,
        "size_bytes": stored.size_bytes,
        "media_type": stored.media_type,
        "storage_uri": stored.uri,
    }


def _build_run_spec(
    plan: AutoResearchFormalPlan,
    branch: AutoResearchFormalBranch,
    stored: StoredObject,
) -> dict[str, Any]:
    return {
        "schema_version": "ampgent.autoresearch-formal-branch-run.2",
        "run_kind": "autoresearch_closed_loop_formal_branch",
        "run_id": str(branch.run_id),
        "branch_key": branch.branch_key,
        "target_id": str(branch.target_id),
        "target_sequence_sha256": branch.target_sequence_sha256,
        "formal_submission_key": branch.formal_submission_key,
        "workflow_type": WORKFLOW_TYPE,
        "workflow_id": branch.workflow_id,
        "workflow_request_sha256": branch.request_sha256,
        "workflow_request_template_sha256": branch.request_template_sha256,
        "workflow_request_artifact": _artifact_payload(stored),
        "run_identity_sha256": branch.run_identity_sha256,
        "submission_config_sha256": plan.config_sha256,
        "submission_preflight_sha256": plan.preflight_sha256,
        "source_revision": plan.source_revision,
        "release_sha256": plan.release_sha256,
        "target_manifest_sha256": plan.target_manifest_sha256,
        "control_environment_sha256": plan.control_environment_sha256,
        "generator_environment_sha256": plan.generator_environment_sha256,
        "metric_plugin_registry_sha256": plan.config["runtime"]["metric_plugin_registry_sha256"],
        "metric_runtime_snapshot_sha256": plan.metric_runtime_snapshot_sha256,
        "model": copy.deepcopy(plan.config["model"]),
        "seed": copy.deepcopy(branch.seed),
        "continuation_policy": copy.deepcopy(branch.continuation_policy),
        "task_queues": copy.deepcopy(branch.request["task_queues"]),
        "workflow_request": copy.deepcopy(branch.request),
        "parent_run_id": (str(branch.parent_run_id) if branch.parent_run_id is not None else None),
        "historical_outputs_reused": False,
        "old_workflow_reused": False,
    }


def _validate_existing_runs(
    *,
    existing: list[ExperimentRun],
    plan: AutoResearchFormalPlan,
    specs: dict[str, dict[str, Any]],
) -> None:
    if len(existing) != len(plan.branches):
        raise ValueError("formal AutoResearch reservation is partially present")
    by_id = {item.id: item for item in existing}
    if set(by_id) != {item.run_id for item in plan.branches}:
        raise ValueError("an AutoResearch identity is owned by another run")
    for branch in plan.branches:
        run = by_id[branch.run_id]
        expected = specs[branch.branch_key]
        if run.status in TERMINAL_RUN_STATUSES:
            raise ValueError("terminal AutoResearch runs cannot be reused")
        if run.status not in RECOVERABLE_RUN_STATUSES:
            raise ValueError(f"unsupported AutoResearch run status: {run.status}")
        if (
            run.target_id != branch.target_id
            or run.formal_submission_key != branch.formal_submission_key
            or run.temporal_workflow_id != branch.workflow_id
            or run.parent_run_id != branch.parent_run_id
            or run.spec_json != expected
            or run.spec_sha256 != sha256_json(expected)
        ):
            raise ValueError(f"existing {branch.branch_key} reservation identity drifted")


async def reserve_autoresearch_formal_plan(
    plan: AutoResearchFormalPlan,
    *,
    session_factory: Callable[[], Any] = SessionFactory,
    object_store_factory: Callable[[], ContentAddressedObjectStore] = (ContentAddressedObjectStore),
    repository_factory: Callable[[Any], ExperimentRepository] = ExperimentRepository,
) -> AutoResearchFormalReservation:
    _revalidate_plan_runtime_boundary(plan)
    object_store = await asyncio.to_thread(object_store_factory)
    stored_by_branch: dict[str, StoredObject] = {}
    for branch in plan.branches:
        stored = await asyncio.to_thread(
            object_store.put_bytes, branch.request_bytes, "application/json"
        )
        if stored.sha256 != branch.request_sha256:
            raise ValueError("request CAS object identity differs from the frozen request")
        stored_by_branch[branch.branch_key] = stored
    specs = {
        branch.branch_key: _build_run_spec(plan, branch, stored_by_branch[branch.branch_key])
        for branch in plan.branches
    }
    run_ids = [item.run_id for item in plan.branches]
    formal_keys = [item.formal_submission_key for item in plan.branches]
    workflow_ids = [item.workflow_id for item in plan.branches]
    async with session_factory() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _advisory_lock_id(plan.config_sha256)},
        )
        existing = list(
            await session.scalars(
                select(ExperimentRun).where(
                    or_(
                        ExperimentRun.id.in_(run_ids),
                        ExperimentRun.formal_submission_key.in_(formal_keys),
                        ExperimentRun.temporal_workflow_id.in_(workflow_ids),
                    )
                )
            )
        )
        if existing:
            _validate_existing_runs(existing=existing, plan=plan, specs=specs)
            return AutoResearchFormalReservation(
                created=False,
                branch_specs=specs,
                request_artifacts=stored_by_branch,
            )
        predecessor_ids = {
            item.parent_run_id for item in plan.branches if item.parent_run_id is not None
        }
        if predecessor_ids:
            predecessors = list(
                await session.scalars(
                    select(ExperimentRun).where(ExperimentRun.id.in_(predecessor_ids))
                )
            )
            by_predecessor_id = {item.id: item for item in predecessors}
            if set(by_predecessor_id) != predecessor_ids:
                raise ValueError("formal AutoResearch predecessor rows are incomplete")
            for branch in plan.branches:
                if branch.parent_run_id is None:
                    continue
                predecessor = by_predecessor_id[branch.parent_run_id]
                if predecessor.status != "failed":
                    raise ValueError("AutoResearch successor predecessor must be failed")
                if predecessor.target_id != branch.target_id:
                    raise ValueError("AutoResearch successor predecessor target drifted")
        targets = list(
            await session.scalars(
                select(Target).where(Target.id.in_({item.target_id for item in plan.branches}))
            )
        )
        by_target_id = {item.id: item for item in targets}
        if set(by_target_id) != {item.target_id for item in plan.branches}:
            raise ValueError("formal AutoResearch target rows are incomplete")
        for branch in plan.branches:
            target = by_target_id[branch.target_id]
            if (
                target.sequence != branch.target_sequence
                or target.sequence_sha256 != branch.target_sequence_sha256
            ):
                raise ValueError(f"{branch.branch_key} durable target identity drifted")
        inserted_ids: list[UUID] = []
        for branch in plan.branches:
            spec = specs[branch.branch_key]
            statement = (
                postgresql_insert(ExperimentRun)
                .values(
                    id=branch.run_id,
                    target_id=branch.target_id,
                    spec_json=spec,
                    spec_sha256=sha256_json(spec),
                    formal_submission_key=branch.formal_submission_key,
                    status="created",
                    temporal_workflow_id=branch.workflow_id,
                    parent_run_id=branch.parent_run_id,
                )
                .on_conflict_do_nothing(index_elements=[ExperimentRun.formal_submission_key])
                .returning(ExperimentRun.id)
            )
            inserted_id = (await session.execute(statement)).scalar_one_or_none()
            if inserted_id != branch.run_id:
                raise ValueError("formal AutoResearch reservation collided during insert")
            inserted_ids.append(inserted_id)
        if inserted_ids != run_ids:
            raise AssertionError("formal AutoResearch six-run insert was not exact")
        for branch in plan.branches:
            stored = stored_by_branch[branch.branch_key]
            await session.execute(
                postgresql_insert(Artifact)
                .values(
                    id=uuid.uuid4(),
                    sha256=stored.sha256,
                    size_bytes=stored.size_bytes,
                    media_type=stored.media_type,
                    storage_uri=stored.uri,
                    metadata_json={
                        "role": "autoresearch_formal_workflow_request",
                        "immutable": True,
                        "branch_key": branch.branch_key,
                        "run_id": str(branch.run_id),
                        "predecessor_run_id": (
                            str(branch.parent_run_id) if branch.parent_run_id is not None else None
                        ),
                        "historical_outputs_reused": False,
                    },
                )
                .on_conflict_do_nothing(index_elements=[Artifact.sha256])
            )
        repository = repository_factory(session)
        for branch in plan.branches:
            spec = specs[branch.branch_key]
            await repository.append_event("run", branch.run_id, "run.created", ACTOR, spec)
            await repository.append_event(
                "run",
                branch.run_id,
                "autoresearch.formal_workflow_reserved",
                ACTOR,
                {
                    "workflow_id": branch.workflow_id,
                    "formal_submission_key": branch.formal_submission_key,
                    "request_sha256": branch.request_sha256,
                    "request_artifact_sha256": stored_by_branch[branch.branch_key].sha256,
                },
            )
    return AutoResearchFormalReservation(
        created=True,
        branch_specs=specs,
        request_artifacts=stored_by_branch,
    )


def _workflow_memo_identity(
    plan: AutoResearchFormalPlan, branch: AutoResearchFormalBranch
) -> dict[str, Any]:
    return {
        "workflow_type": WORKFLOW_TYPE,
        "branch_key": branch.branch_key,
        "run_id": str(branch.run_id),
        "request_sha256": branch.request_sha256,
        "formal_submission_key": branch.formal_submission_key,
        "release_sha256": plan.release_sha256,
        "control_environment_sha256": plan.control_environment_sha256,
        "generator_environment_sha256": plan.generator_environment_sha256,
        "metric_runtime_snapshot_sha256": plan.metric_runtime_snapshot_sha256,
        "pepmlm_revision": plan.config["model"]["pepmlm_revision"],
        "pepmlm_weights_sha256": plan.config["model"]["pepmlm_weights_sha256"],
        "seed_receipt_sha256": branch.seed["bundle_receipt_sha256"],
        "source_map_sha256": branch.seed["source_map_sha256"],
        "predecessor_run_id": (
            str(branch.parent_run_id) if branch.parent_run_id is not None else None
        ),
        "historical_outputs_reused": False,
    }


async def _start_or_recover_autoresearch_workflow(
    client: Client,
    *,
    plan: AutoResearchFormalPlan,
    branch: AutoResearchFormalBranch,
) -> TemporalWorkflowBinding:
    identity = _workflow_memo_identity(plan, branch)
    recovered = False
    try:
        handle = await client.start_workflow(
            WORKFLOW_TYPE,
            branch.request,
            id=branch.workflow_id,
            task_queue=CONTROL_QUEUE,
            memo={WORKFLOW_MEMO_KEY: identity},
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
        )
    except WorkflowAlreadyStartedError as error:
        recovered = True
        handle = client.get_workflow_handle(branch.workflow_id)
        description = await handle.describe()
        if getattr(description, "workflow_type", None) != WORKFLOW_TYPE:
            raise ValueError(f"existing {branch.branch_key} workflow type differs") from error
        memo = getattr(description, "memo", None)
        if not isinstance(memo, dict) or memo.get(WORKFLOW_MEMO_KEY) != identity:
            raise ValueError(
                f"existing {branch.branch_key} workflow memo identity drifted"
            ) from error
    description = await handle.describe()
    temporal_run_id = str(getattr(description, "run_id", "") or "")
    if not temporal_run_id:
        raise ValueError(f"{branch.branch_key} Temporal run ID is empty")
    return TemporalWorkflowBinding(
        handle=handle,
        temporal_run_id=temporal_run_id,
        recovered=recovered,
    )


async def submit_autoresearch_formal_plan(
    plan: AutoResearchFormalPlan,
    reservation: AutoResearchFormalReservation,
    *,
    client: Client,
    session_factory: Callable[[], Any] = SessionFactory,
    repository_factory: Callable[[Any], ExperimentRepository] = ExperimentRepository,
) -> dict[str, Any]:
    _revalidate_plan_runtime_boundary(plan)
    if set(reservation.branch_specs) != set(BRANCH_KEYS):
        raise ValueError("submission requires the exact six-branch reservation")
    bindings: dict[str, TemporalWorkflowBinding] = {}
    for branch in plan.branches:
        bindings[branch.branch_key] = await _start_or_recover_autoresearch_workflow(
            client, plan=plan, branch=branch
        )
    async with session_factory() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _advisory_lock_id(plan.config_sha256)},
        )
        existing = list(
            await session.scalars(
                select(ExperimentRun).where(
                    ExperimentRun.id.in_([item.run_id for item in plan.branches])
                )
            )
        )
        _validate_existing_runs(
            existing=existing,
            plan=plan,
            specs=reservation.branch_specs,
        )
        by_id = {item.id: item for item in existing}
        repository = repository_factory(session)
        for branch in plan.branches:
            run = by_id[branch.run_id]
            binding = bindings[branch.branch_key]
            if run.temporal_run_id not in (None, binding.temporal_run_id):
                raise ValueError(f"{branch.branch_key} Temporal run identity drifted")
            newly_bound = run.status == "created" and run.temporal_run_id is None
            if run.status in {"running", "waiting"} and run.temporal_run_id is None:
                raise ValueError(f"{branch.branch_key} active run has no Temporal identity")
            run.temporal_run_id = binding.temporal_run_id
            if run.status == "created":
                run.status = "running"
            if newly_bound:
                await repository.append_event(
                    "run",
                    branch.run_id,
                    "autoresearch.formal_workflow_submitted",
                    ACTOR,
                    {
                        "workflow_id": branch.workflow_id,
                        "temporal_run_id": binding.temporal_run_id,
                        "formal_submission_key": branch.formal_submission_key,
                        "request_sha256": branch.request_sha256,
                    },
                )
    return {
        "submitted": True,
        "branch_count": len(bindings),
        "branches": [
            {
                "branch_key": branch.branch_key,
                "run_id": str(branch.run_id),
                "workflow_id": branch.workflow_id,
                "temporal_run_id": bindings[branch.branch_key].temporal_run_id,
                "recovered": bindings[branch.branch_key].recovered,
            }
            for branch in plan.branches
        ],
    }


async def execute_autoresearch_formal_plan(
    plan: AutoResearchFormalPlan,
    *,
    reserve_only: bool,
) -> dict[str, Any]:
    reservation = await reserve_autoresearch_formal_plan(plan)
    result: dict[str, Any] = {
        "executed": True,
        "reserve_only": reserve_only,
        "config_sha256": plan.config_sha256,
        "preflight_sha256": plan.preflight_sha256,
        "reservation": reservation.summary(),
    }
    if reserve_only:
        return result
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    result["submission"] = await submit_autoresearch_formal_plan(plan, reservation, client=client)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate, reserve, and exact-once submit six AutoResearch branches"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="explicitly authorize CAS/DB/Temporal mutations",
    )
    parser.add_argument(
        "--reserve-only",
        action="store_true",
        help="with --execute, reserve six runs and stop before Temporal",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    plan = load_autoresearch_formal_plan(config_path=args.config, preflight_path=args.preflight)
    if args.reserve_only and not args.execute:
        raise SystemExit("--reserve-only requires explicit --execute")
    if not args.execute:
        print(
            json.dumps(
                {
                    "executed": False,
                    "inert": True,
                    "status": "validated",
                    "config_sha256": plan.config_sha256,
                    "preflight_sha256": plan.preflight_sha256,
                    "branch_count": len(plan.branches),
                },
                sort_keys=True,
            )
        )
        return 0
    result = asyncio.run(execute_autoresearch_formal_plan(plan, reserve_only=args.reserve_only))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
