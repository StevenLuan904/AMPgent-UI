from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from pepagent.model_workers.sequence_metric_plan import (
    build_external_metric_plan,
    consume_external_metric_result,
    load_external_metric_adapter,
    materialize_external_metric_input,
)
from pepagent.v37_runtime_execution import (
    V37GenericRuntimeExpectation,
    V37GenericRuntimePaths,
    run_v37_guarded_provider_subprocess,
)
from pepagent.workers.v37_activities import (
    _isolated_v37_runtime_environment,
    _prepare_builtin_metric_python_bootstrap,
)

SMOKE_SEQUENCES = ("VIRIAWRRILQKLGEKLAKAT", "AFSKWWKKLKSKIRSKLVTKGYA")


def _smoke_candidates(count: int) -> list[dict[str, str]]:
    if count < 1:
        raise ValueError("smoke candidate count must be positive")
    return [
        {
            "id": f"v38-smoke-{index + 1:04d}",
            "sequence": SMOKE_SEQUENCES[index % len(SMOKE_SEQUENCES)],
        }
        for index in range(count)
    ]


def _load_descriptor(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"runtime descriptor is not an object: {path}")
    return payload


def _guard(runtime: dict[str, Any]) -> tuple[dict[str, Any], Any, Any]:
    guard = runtime["execution_guard"]
    paths_payload = guard["paths"]
    return (
        guard["contract"],
        V37GenericRuntimeExpectation(**guard["expectation"]),
        V37GenericRuntimePaths(
            executable_path=Path(paths_payload["executable_path"]),
            runtime_manifest_path=Path(paths_payload["runtime_manifest_path"]),
            packages_lock_path=Path(paths_payload["packages_lock_path"]),
            source_root=Path(paths_payload["source_root"]),
            model_root=Path(paths_payload["model_root"]),
            adapter_path=Path(paths_payload["adapter_path"]),
        ),
    )


async def _launch(
    runtime: dict[str, Any],
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    input_paths: dict[str, Path],
) -> tuple[str, dict[str, Any]]:
    command, environment = _prepare_builtin_metric_python_bootstrap(
        command=command, runtime=runtime, environment=environment
    )
    contract, expectation, paths = _guard(runtime)

    async def ignore(_receipt: dict[str, Any]) -> None:
        return None

    return await run_v37_guarded_provider_subprocess(
        command,
        contract=contract,
        expectation=expectation,
        paths=paths,
        receipt_writer=ignore,
        aggregate_receipt_writer=ignore,
        cwd=cwd,
        env=environment,
        input_paths=input_paths,
    )


async def _smoke_physicochemical(
    runtime: dict[str, Any], work: Path, candidates: list[dict[str, str]]
) -> dict[str, Any]:
    request_path = work / "physicochemical-request.json"
    output_path = work / "physicochemical-result.json"
    request_path.write_text(
        json.dumps(
            {
                "run_id": "v38-runtime-smoke",
                "plugin": {
                    "name": "physicochemical_developability",
                    "parameters": {
                        "ph": 7.4,
                        "c_terminal_amidated": False,
                        "hydrophobic_moment_angle": 100,
                    },
                },
                "candidates": candidates,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    paths = runtime["execution_guard"]["paths"]
    _, receipt = await _launch(
        runtime,
        [
            paths["executable_path"],
            paths["adapter_path"],
            "--request",
            str(request_path),
            "--output",
            str(output_path),
        ],
        cwd=Path(runtime["cwd"]),
        environment=_isolated_v37_runtime_environment(),
        input_paths={"request": request_path},
    )
    result = json.loads(output_path.read_text(encoding="utf-8"))
    if result.get("candidate_count") != len(candidates):
        raise ValueError("physicochemical smoke candidate count drifted")
    return {
        "candidate_count": result["candidate_count"],
        "launch_receipt_sha256": receipt["launch_receipt_sha256"],
        "returncode": receipt["returncode"],
    }


async def _smoke_macrel(
    runtime: dict[str, Any], work: Path, candidates: list[dict[str, str]]
) -> dict[str, Any]:
    registry_path = Path(runtime["registry_path"])
    adapter, registry_sha256 = load_external_metric_adapter(registry_path, "hemolysis_risk")
    if adapter is None or registry_sha256 != runtime["registry_sha256"]:
        raise ValueError("Macrel smoke registry identity drifted")
    metric_work = work / "macrel"
    plan = build_external_metric_plan(
        plugin_name="hemolysis_risk",
        adapter=adapter,
        work_dir=metric_work,
        run_id="v38-runtime-smoke",
        registry_path=registry_path,
        registry_sha256=registry_sha256,
    )
    materialize_external_metric_input(plan, candidates)
    stdout, receipt = await _launch(
        runtime,
        list(plan["command_argv"]),
        cwd=Path(plan["working_directory"]) if plan.get("working_directory") else metric_work,
        environment=_isolated_v37_runtime_environment(plan["environment"]),
        input_paths={
            "candidate_input": Path(plan["input"]["candidates_csv"]),
            "metric_registry": registry_path,
        },
    )
    result = consume_external_metric_result(
        plan=plan,
        candidates=candidates,
        execution_receipt={
            "status": "completed",
            "returncode": receipt["returncode"],
            "stdout": stdout,
            "stderr": receipt["stderr_tail"],
            "command_argv": receipt["pre_snapshot"]["identity"]["command"],
        },
    )
    return {
        "record_count": len(result["records"]),
        "launch_receipt_sha256": receipt["launch_receipt_sha256"],
        "returncode": receipt["returncode"],
    }


async def _main(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = _smoke_candidates(args.candidate_count)
    physicochemical = _load_descriptor(args.physicochemical_descriptor)
    macrel = _load_descriptor(args.macrel_descriptor)
    return {
        "schema_version": "v38.sequence-metric-runtime-smoke.1",
        "candidate_count": len(candidates),
        "physicochemical": await _smoke_physicochemical(physicochemical, output_dir, candidates),
        "hemolysis_macrel": await _smoke_macrel(macrel, output_dir, candidates),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--physicochemical-descriptor", type=Path, required=True)
    parser.add_argument("--macrel-descriptor", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--candidate-count", type=int, default=2)
    args = parser.parse_args()
    result = asyncio.run(_main(args))
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
