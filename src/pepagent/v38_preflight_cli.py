from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml
from sqlalchemy import select

from pepagent.db.models import Target, TargetPocket
from pepagent.db.session import SessionFactory
from pepagent.provenance.environment import fingerprint_runtime
from pepagent.provenance.hashing import sha256_file
from pepagent.v38_preflight import build_v38_submission_preflight
from pepagent.v38_request_builder import build_v38_request_template


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"YAML root is not an object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


async def _load_target_runtimes(panel: dict[str, Any]) -> dict[str, dict[str, Any]]:
    branches = panel.get("branches")
    if not isinstance(branches, list):
        raise ValueError("v38 panel branches are absent")
    target_ids = {UUID(str(item["target_id"])) for item in branches}
    pocket_ids = {
        UUID(str(item[key]))
        for item in branches
        for key in ("primary_pocket_id", "wrong_pocket_id")
    }
    async with SessionFactory() as session:
        targets = list(
            await session.scalars(select(Target).where(Target.id.in_(target_ids)))
        )
        pockets = list(
            await session.scalars(select(TargetPocket).where(TargetPocket.id.in_(pocket_ids)))
        )
    if {item.id for item in targets} != target_ids:
        raise ValueError("v38 target database rows do not cover the panel")
    if {item.id for item in pockets} != pocket_ids:
        raise ValueError("v38 pocket database rows do not cover the panel")
    pockets_by_target: dict[UUID, dict[str, list[int]]] = {}
    for pocket in pockets:
        pockets_by_target.setdefault(pocket.target_id, {})[str(pocket.id)] = [
            int(item) for item in pocket.residue_indices
        ]
    return {
        str(target.id): {
            "target_sequence": target.sequence,
            "pockets_by_id": pockets_by_target.get(target.id, {}),
        }
        for target in targets
    }


async def build_v38_preflight_artifacts(
    *,
    benchmark_path: Path,
    panel_path: Path,
    controller_state_path: Path,
    worker_placement_path: Path,
    generator_manifest_path: Path,
    execution_bundle_path: Path,
    structure_spec_path: Path,
    request_output_path: Path,
    preflight_output_path: Path,
) -> dict[str, Any]:
    benchmark = _load_yaml(benchmark_path)
    panel = _load_yaml(panel_path)
    controller = _load_json(controller_state_path)
    placement = _load_json(worker_placement_path)
    target_runtimes = await _load_target_runtimes(panel)
    request = build_v38_request_template(
        benchmark=benchmark,
        panel=panel,
        controller_state=controller,
        worker_placement=placement,
        generator_manifest=_load_yaml(generator_manifest_path),
        execution_bundle=_load_json(execution_bundle_path),
        structure_spec=_load_yaml(structure_spec_path),
        target_runtime_by_id=target_runtimes,
        control_environment_sha256=fingerprint_runtime()[0],
    )
    preflight = build_v38_submission_preflight(
        request_template=request,
        controller_state=controller,
        worker_placement=placement,
        benchmark_sha256=sha256_file(benchmark_path),
        target_panel_sha256=sha256_file(panel_path),
    )
    _write_json(request_output_path, request)
    _write_json(preflight_output_path, preflight)
    return {
        "status": preflight["status"],
        "formal_submission_key": preflight["formal_submission_key"],
        "workflow_id": preflight["workflow_id"],
        "request_template_sha256": preflight["request_template_sha256"],
        "request_output": str(request_output_path),
        "preflight_output": str(preflight_output_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build v38 executable request and unique-run preflight; never submit"
    )
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--controller-state", type=Path, required=True)
    parser.add_argument("--worker-placement", type=Path, required=True)
    parser.add_argument("--generator-manifest", type=Path, required=True)
    parser.add_argument("--execution-bundle", type=Path, required=True)
    parser.add_argument("--structure-spec", type=Path, required=True)
    parser.add_argument("--request-output", type=Path, required=True)
    parser.add_argument("--preflight-output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        build_v38_preflight_artifacts(
            benchmark_path=args.benchmark.resolve(),
            panel_path=args.panel.resolve(),
            controller_state_path=args.controller_state.resolve(),
            worker_placement_path=args.worker_placement.resolve(),
            generator_manifest_path=args.generator_manifest.resolve(),
            execution_bundle_path=args.execution_bundle.resolve(),
            structure_spec_path=args.structure_spec.resolve(),
            request_output_path=args.request_output.resolve(),
            preflight_output_path=args.preflight_output.resolve(),
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
