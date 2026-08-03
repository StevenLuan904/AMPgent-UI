from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from pepagent.handoff_metrics import (
    HANDOFF_METRIC_VERSION,
    METRIC_PLUGIN_CONTRACTS,
    normalize_metric_records,
    physicochemical_descriptors,
)
from pepagent.provenance.hashing import sha256_file


def _builtin_result(
    plugin_name: str,
    candidates: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    if plugin_name != "physicochemical_developability":
        raise KeyError(plugin_name)
    rows = []
    for candidate in candidates:
        rows.append(
            {
                "candidate_id": candidate["id"],
                "sequence": candidate["sequence"],
                "status": "complete",
                **physicochemical_descriptors(
                    candidate["sequence"],
                    ph=float(parameters.get("ph", 7.4)),
                    c_terminal_amidated=bool(
                        parameters.get("c_terminal_amidated", False)
                    ),
                    hydrophobic_moment_angle=int(
                        parameters.get("hydrophobic_moment_angle", 100)
                    ),
                ),
            }
        )
    return {
        "status": "complete",
        "adapter_version": HANDOFF_METRIC_VERSION,
        "records": normalize_metric_records(plugin_name, rows),
        "raw_rows": rows,
    }


def _load_registry(path: Path | None) -> tuple[dict[str, Any], str | None]:
    if path is None or not path.exists():
        return {}, None
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload.get("adapters", {}), sha256_file(path)


def _external_result(
    plugin_name: str,
    candidates: list[dict[str, Any]],
    work_dir: Path,
    registry_path: Path | None,
    run_id: str,
) -> dict[str, Any]:
    registry, registry_sha256 = _load_registry(registry_path)
    adapter = registry.get(plugin_name)
    if not adapter or not adapter.get("enabled", False):
        return {
            "status": "unavailable",
            "adapter_version": None,
            "records": [],
            "reason": "adapter is absent or disabled in the deployed runtime registry",
            "registry_sha256": registry_sha256,
        }

    work_dir.mkdir(parents=True, exist_ok=True)
    input_path = work_dir / "candidates.csv"
    output_path = work_dir / "predictions.csv"
    raw_output_dir = work_dir / "raw"
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    # An activity retry reuses its deterministic work directory. Never accept a
    # prediction file left by an earlier failed or timed-out adapter attempt.
    output_path.unlink(missing_ok=True)
    with input_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["candidate_id", "sequence"])
        writer.writeheader()
        writer.writerows(
            {"candidate_id": item["id"], "sequence": item["sequence"]}
            for item in candidates
        )

    command_template = adapter.get("command")
    if not isinstance(command_template, list) or not command_template:
        return {
            "status": "unavailable",
            "adapter_version": adapter.get("version"),
            "records": [],
            "reason": "runtime registry entry has no command array",
            "registry_sha256": registry_sha256,
        }
    config_path = adapter.get("config_path")
    replacements = {
        "input": str(input_path),
        "output": str(output_path),
        "config": str(Path(config_path).resolve()) if config_path else "",
        "raw_output_dir": str(raw_output_dir),
        "run_id": run_id,
    }
    command = [str(value).format(**replacements) for value in command_template]
    timeout_seconds = int(adapter.get("timeout_seconds", 1800))
    try:
        completed = subprocess.run(
            command,
            cwd=adapter.get("working_directory"),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {
            "status": "unavailable",
            "adapter_version": adapter.get("version"),
            "records": [],
            "reason": f"external adapter could not complete: {type(error).__name__}: {error}",
            "registry_sha256": registry_sha256,
            "command_argv": command,
        }
    if completed.returncode != 0 or not output_path.exists():
        return {
            "status": "unavailable",
            "adapter_version": adapter.get("version"),
            "records": [],
            "reason": f"external adapter exited with code {completed.returncode}",
            "registry_sha256": registry_sha256,
            "command_argv": command,
            "stdout_tail": completed.stdout[-8000:],
            "stderr_tail": completed.stderr[-8000:],
        }
    with output_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    expected = {str(item["id"]): item["sequence"] for item in candidates}
    returned: dict[str, str] = {}
    for row in rows:
        candidate_id = str(row.get("candidate_id") or row.get("internal_id") or "")
        sequence = row.get("sequence", "")
        if candidate_id not in expected or expected[candidate_id] != sequence:
            return {
                "status": "unavailable",
                "adapter_version": adapter.get("version"),
                "records": [],
                "reason": "adapter output contains an unknown candidate or sequence mismatch",
                "registry_sha256": registry_sha256,
            }
        if candidate_id in returned:
            return {
                "status": "unavailable",
                "adapter_version": adapter.get("version"),
                "records": [],
                "reason": "adapter output contains duplicate candidate rows",
                "registry_sha256": registry_sha256,
            }
        returned[candidate_id] = sequence
    if returned.keys() != expected.keys():
        return {
            "status": "unavailable",
            "adapter_version": adapter.get("version"),
            "records": [],
            "reason": "adapter output is missing one or more candidate rows",
            "registry_sha256": registry_sha256,
        }
    return {
        "status": "complete",
        "adapter_version": adapter.get("version"),
        "records": normalize_metric_records(plugin_name, rows),
        "raw_rows": rows,
        "registry_sha256": registry_sha256,
        "command_argv": command,
        "stdout_tail": completed.stdout[-8000:],
        "stderr_tail": completed.stderr[-8000:],
        "model_uri": adapter.get("model_uri"),
        "weights_sha256": adapter.get("weights_sha256"),
        "limitations": adapter.get("limitations", []),
    }


def evaluate(request: dict[str, Any], work_dir: Path, registry_path: Path | None) -> dict[str, Any]:
    plugin_name = request["plugin"]["name"]
    contract = METRIC_PLUGIN_CONTRACTS[plugin_name]
    if contract["provider"] == "builtin":
        result = _builtin_result(
            plugin_name,
            request["candidates"],
            request["plugin"].get("parameters", {}),
        )
    else:
        result = _external_result(
            plugin_name,
            request["candidates"],
            work_dir,
            registry_path,
            request["run_id"],
        )
    return {
        "plugin": request["plugin"],
        "contract": contract,
        "candidate_count": len(request["candidates"]),
        **result,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--registry", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    result = evaluate(request, args.work_dir, args.registry)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"plugin": request["plugin"]["name"], "status": result["status"]}))


if __name__ == "__main__":
    main()
