from __future__ import annotations

import csv
import math
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from pepagent.handoff_metrics import normalize_metric_records
from pepagent.provenance.hashing import sha256_file

PLAN_SCHEMA_VERSION = "sequence-metric-execution-plan-v1"
PYTHON_BOOTSTRAP_ENVIRONMENT_KEYS = frozenset(
    {
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
        "VIRTUAL_ENV",
        "__PYVENV_LAUNCHER__",
    }
)


class MetricExecutionPlanError(ValueError):
    """Raised when a runtime registry entry cannot form an executable contract."""


def isolated_external_runtime_environment(
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an OS environment isolated from a parent Python worker bootstrap."""

    environment = {
        str(key): str(value)
        for key, value in os.environ.items()
        if key.upper() not in PYTHON_BOOTSTRAP_ENVIRONMENT_KEYS
    }
    explicit = {str(key): str(value) for key, value in (overrides or {}).items()}
    forbidden = sorted(
        key for key in explicit if key.upper() in PYTHON_BOOTSTRAP_ENVIRONMENT_KEYS
    )
    if forbidden:
        raise MetricExecutionPlanError(
            "external runtime environment cannot override parent Python bootstrap keys: "
            + ", ".join(forbidden)
        )
    environment.update(explicit)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def load_external_metric_adapter(
    registry_path: Path | None, plugin_name: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Load one adapter plus the exact registry digest used to select it."""

    if registry_path is None or not registry_path.exists():
        return None, None
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    adapters = payload.get("adapters", {})
    if not isinstance(adapters, Mapping):
        raise MetricExecutionPlanError("runtime registry adapters must be a mapping")
    adapter = adapters.get(plugin_name)
    if adapter is not None and not isinstance(adapter, Mapping):
        raise MetricExecutionPlanError("runtime registry adapter must be a mapping")
    return (dict(adapter) if adapter is not None else None), sha256_file(registry_path)


def _string_mapping(value: Any, *, field: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise MetricExecutionPlanError(f"{field} must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, (str, int, float, bool)):
            raise MetricExecutionPlanError(f"{field} must contain scalar string keys and values")
        result[key] = str(item)
    return dict(sorted(result.items()))


def _declared_digest_inventory(adapter: Mapping[str, Any]) -> dict[str, Any]:
    return {key: adapter[key] for key in sorted(adapter) if "sha256" in key.lower()}


def _path_inventory(
    *,
    command: Sequence[str],
    working_directory: str | None,
    config_path: str | None,
) -> list[str]:
    paths: list[str] = []
    for value in [command[0], working_directory, config_path]:
        if value and value not in paths:
            paths.append(value)
    for index, value in enumerate(command[:-1]):
        if value.startswith("--") and value.endswith(
            ("path", "file", "directory", "repository", "model", "checkpoint", "features")
        ):
            candidate = command[index + 1]
            if candidate and candidate not in paths:
                paths.append(candidate)
    return paths


def build_external_metric_plan(
    *,
    plugin_name: str,
    adapter: Mapping[str, Any],
    work_dir: Path,
    run_id: str,
    registry_path: Path | None,
    registry_sha256: str | None,
) -> dict[str, Any]:
    """Build a JSON-safe provider execution contract without touching the filesystem."""

    command_template = adapter.get("command")
    if not isinstance(command_template, list) or not command_template:
        raise MetricExecutionPlanError("runtime registry entry has no command array")
    if not all(isinstance(item, (str, int, float)) for item in command_template):
        raise MetricExecutionPlanError("runtime registry command must contain scalar values")

    input_path = work_dir / "candidates.csv"
    output_path = work_dir / "predictions.csv"
    raw_output_dir = work_dir / "raw"
    config_path = adapter.get("config_path")
    if config_path is not None and not isinstance(config_path, str):
        raise MetricExecutionPlanError("config_path must be a string")
    resolved_config_path = str(Path(config_path).resolve()) if config_path else None
    replacements = {
        "input": str(input_path),
        "output": str(output_path),
        "config": resolved_config_path or "",
        "raw_output_dir": str(raw_output_dir),
        "run_id": run_id,
    }
    try:
        command = [str(value).format(**replacements) for value in command_template]
    except (KeyError, ValueError) as error:
        raise MetricExecutionPlanError(
            f"runtime registry command template is invalid: {error}"
        ) from error

    working_directory = adapter.get("working_directory")
    if working_directory is not None and not isinstance(working_directory, str):
        raise MetricExecutionPlanError("working_directory must be a string")
    environment = _string_mapping(adapter.get("environment"), field="environment")
    timeout_seconds = int(adapter.get("timeout_seconds", 1800))
    if timeout_seconds <= 0:
        raise MetricExecutionPlanError("timeout_seconds must be positive")

    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plugin_name": plugin_name,
        "adapter_version": adapter.get("version"),
        "registry_sha256": registry_sha256,
        "executable": command[0],
        "arguments": command[1:],
        "command_argv": command,
        "working_directory": working_directory,
        "environment": environment,
        "inherits_parent_environment": False,
        "timeout_seconds": timeout_seconds,
        "input": {"candidates_csv": str(input_path)},
        "output": {
            "predictions_csv": str(output_path),
            "raw_output_directory": str(raw_output_dir),
        },
        "source_inventory": {
            "registry_path": str(registry_path) if registry_path else None,
            "registry_sha256": registry_sha256,
            "config_path": resolved_config_path,
            "source_revision": adapter.get("source_revision"),
            "runtime_revision": adapter.get("runtime_revision"),
            "path_inventory": _path_inventory(
                command=command,
                working_directory=working_directory,
                config_path=resolved_config_path,
            ),
            "declared_digest_inventory": _declared_digest_inventory(adapter),
        },
        "model_inventory": {
            "model_uri": adapter.get("model_uri"),
            "weights_sha256": adapter.get("weights_sha256"),
            "model_config_sha256": adapter.get("model_config_sha256"),
            "inventory": adapter.get("model_inventory", []),
        },
        "limitations": adapter.get("limitations", []),
    }


def validate_external_metric_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise MetricExecutionPlanError("unsupported metric execution plan schema")
    command = plan.get("command_argv")
    if not isinstance(command, list) or not command or command[0] != plan.get("executable"):
        raise MetricExecutionPlanError("execution plan command/executable mapping is invalid")
    if command[1:] != plan.get("arguments"):
        raise MetricExecutionPlanError("execution plan argument mapping is invalid")
    if not isinstance(plan.get("environment"), Mapping):
        raise MetricExecutionPlanError("execution plan environment is invalid")
    if plan.get("inherits_parent_environment") is not False:
        raise MetricExecutionPlanError(
            "execution plan must isolate the provider runtime from its parent environment"
        )
    if int(plan.get("timeout_seconds", 0)) <= 0:
        raise MetricExecutionPlanError("execution plan timeout is invalid")
    for section, key in (("input", "candidates_csv"), ("output", "predictions_csv")):
        payload = plan.get(section)
        if not isinstance(payload, Mapping) or not payload.get(key):
            raise MetricExecutionPlanError(f"execution plan {section} mapping is invalid")


def materialize_external_metric_input(
    plan: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Write deterministic input after the caller has accepted the execution plan."""

    validate_external_metric_plan(plan)
    input_path = Path(str(plan["input"]["candidates_csv"]))
    output_path = Path(str(plan["output"]["predictions_csv"]))
    raw_output_dir = Path(str(plan["output"]["raw_output_directory"]))
    input_path.parent.mkdir(parents=True, exist_ok=True)
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    with input_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["candidate_id", "sequence"])
        writer.writeheader()
        writer.writerows(
            {"candidate_id": item["id"], "sequence": item["sequence"]} for item in candidates
        )
    return {
        "candidate_count": len(candidates),
        "input_sha256": sha256_file(input_path),
        "input_path": str(input_path),
    }


def execute_external_metric_plan(
    plan: Mapping[str, Any],
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Execute an already accepted plan and return a JSON-safe process receipt."""

    validate_external_metric_plan(plan)
    environment = isolated_external_runtime_environment(plan["environment"])
    try:
        completed = runner(
            list(plan["command_argv"]),
            cwd=plan.get("working_directory"),
            env=environment,
            capture_output=True,
            text=True,
            timeout=int(plan["timeout_seconds"]),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "status": "execution_error",
            "error_type": type(error).__name__,
            "error_message": str(error),
            "command_argv": list(plan["command_argv"]),
        }
    return {
        "status": "completed",
        "returncode": int(completed.returncode),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "command_argv": list(plan["command_argv"]),
    }


def consume_external_metric_result(
    *,
    plan: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    execution_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate process/output identity and normalize the provider result."""

    validate_external_metric_plan(plan)
    common = {
        "adapter_version": plan.get("adapter_version"),
        "registry_sha256": plan.get("registry_sha256"),
        "command_argv": list(plan["command_argv"]),
    }
    if execution_receipt.get("status") != "completed":
        return {
            "status": "unavailable",
            "records": [],
            "reason": "external adapter could not complete: "
            f"{execution_receipt.get('error_type', 'ExecutionError')}: "
            f"{execution_receipt.get('error_message', 'unknown execution failure')}",
            **common,
        }

    output_path = Path(str(plan["output"]["predictions_csv"]))
    returncode = int(execution_receipt.get("returncode", -1))
    stdout = str(execution_receipt.get("stdout", ""))
    stderr = str(execution_receipt.get("stderr", ""))
    if returncode != 0 or not output_path.exists():
        return {
            "status": "unavailable",
            "records": [],
            "reason": f"external adapter exited with code {returncode}",
            "stdout_tail": stdout[-8000:],
            "stderr_tail": stderr[-8000:],
            **common,
        }

    with output_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    expected = {str(item["id"]): str(item["sequence"]) for item in candidates}
    returned: dict[str, str] = {}
    for row in rows:
        candidate_id = str(row.get("candidate_id") or row.get("internal_id") or "")
        sequence = row.get("sequence", "")
        if candidate_id not in expected or expected[candidate_id] != sequence:
            return {
                "status": "unavailable",
                "records": [],
                "reason": "adapter output contains an unknown candidate or sequence mismatch",
                **common,
            }
        if candidate_id in returned:
            return {
                "status": "unavailable",
                "records": [],
                "reason": "adapter output contains duplicate candidate rows",
                **common,
            }
        returned[candidate_id] = sequence
    if list(returned) != list(expected):
        return {
            "status": "unavailable",
            "records": [],
            "reason": "adapter output candidate rows differ in identity or exact order",
            **common,
        }
    records = normalize_metric_records(str(plan["plugin_name"]), rows)
    if any(
        observation.get("numeric_value") is not None
        and not math.isfinite(float(observation["numeric_value"]))
        for record in records
        for observation in record.get("observations", [])
    ):
        return {
            "status": "unavailable",
            "records": [],
            "reason": "adapter output contains a non-finite numeric value",
            **common,
        }
    return {
        "status": "complete",
        "records": records,
        "raw_rows": rows,
        "stdout_tail": stdout[-8000:],
        "stderr_tail": stderr[-8000:],
        "model_uri": plan["model_inventory"].get("model_uri"),
        "weights_sha256": plan["model_inventory"].get("weights_sha256"),
        "limitations": plan.get("limitations", []),
        **common,
    }
