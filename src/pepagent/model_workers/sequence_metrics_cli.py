from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pepagent.handoff_metrics import (
    HANDOFF_METRIC_VERSION,
    METRIC_PLUGIN_CONTRACTS,
    normalize_metric_records,
    physicochemical_descriptors,
)
from pepagent.model_workers.sequence_metric_plan import (
    MetricExecutionPlanError,
    build_external_metric_plan,
    consume_external_metric_result,
    execute_external_metric_plan,
    load_external_metric_adapter,
    materialize_external_metric_input,
)


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
                    c_terminal_amidated=bool(parameters.get("c_terminal_amidated", False)),
                    hydrophobic_moment_angle=int(parameters.get("hydrophobic_moment_angle", 100)),
                ),
            }
        )
    return {
        "status": "complete",
        "adapter_version": HANDOFF_METRIC_VERSION,
        "records": normalize_metric_records(plugin_name, rows),
        "raw_rows": rows,
    }


def _external_result(
    plugin_name: str,
    candidates: list[dict[str, Any]],
    work_dir: Path,
    registry_path: Path | None,
    run_id: str,
) -> dict[str, Any]:
    try:
        adapter, registry_sha256 = load_external_metric_adapter(
            registry_path, plugin_name
        )
    except MetricExecutionPlanError as error:
        return {
            "status": "unavailable",
            "adapter_version": None,
            "records": [],
            "reason": str(error),
            "registry_sha256": None,
        }
    if not adapter or not adapter.get("enabled", False):
        return {
            "status": "unavailable",
            "adapter_version": None,
            "records": [],
            "reason": "adapter is absent or disabled in the deployed runtime registry",
            "registry_sha256": registry_sha256,
        }

    try:
        plan = build_external_metric_plan(
            plugin_name=plugin_name,
            adapter=adapter,
            work_dir=work_dir,
            run_id=run_id,
            registry_path=registry_path,
            registry_sha256=registry_sha256,
        )
    except MetricExecutionPlanError as error:
        return {
            "status": "unavailable",
            "adapter_version": adapter.get("version"),
            "records": [],
            "reason": str(error),
            "registry_sha256": registry_sha256,
        }
    materialize_external_metric_input(plan, candidates)
    receipt = execute_external_metric_plan(plan)
    return consume_external_metric_result(
        plan=plan,
        candidates=candidates,
        execution_receipt=receipt,
    )


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
