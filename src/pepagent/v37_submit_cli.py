from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import WorkflowAlreadyStartedError

from pepagent.db.models import ExperimentRun
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.schemas import ExperimentSpec
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.settings import get_settings
from pepagent.v37_preregistration import (
    load_v37_preregistration,
    validate_v37_experiment_spec,
)


async def ensure_no_existing_v37_run(
    session: Any, *, benchmark_id: str, benchmark_version: str
) -> None:
    duplicate = await session.scalar(
        select(ExperimentRun).where(
            ExperimentRun.spec_json["benchmark_id"].astext == benchmark_id,
            ExperimentRun.spec_json["benchmark_version"].astext == benchmark_version,
        )
    )
    if duplicate is not None:
        raise ValueError(f"v37 formal run already exists: {duplicate.id}")


def _same_v37_submission(existing: ExperimentRun, raw_spec: dict[str, Any]) -> bool:
    immutable_keys = (
        "benchmark_id",
        "benchmark_version",
        "manifest_sha256",
        "submission_preflight_sha256",
        "execution_bundle_sha256",
        "experiment_spec_sha256",
    )
    return all(existing.spec_json.get(key) == raw_spec[key] for key in immutable_keys)


async def _start_or_recover_workflow(
    client: Client,
    *,
    workflow_id: str,
    request: dict[str, Any],
) -> WorkflowHandle:
    try:
        return await client.start_workflow(
            "RapidChampionGenerationV37Workflow",
            request,
            id=workflow_id,
            task_queue="pepagent-control-v37",
        )
    except WorkflowAlreadyStartedError:
        return client.get_workflow_handle(workflow_id)


def load_v37_submission_bundle(
    *,
    manifest_path: Path,
    experiment_spec_path: Path,
    execution_bundle_path: Path,
    preflight_path: Path,
) -> tuple[dict[str, Any], ExperimentSpec, dict[str, Any], dict[str, Any]]:
    manifest = load_v37_preregistration(manifest_path)
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    experiment_binding = validate_v37_experiment_spec(
        manifest,
        manifest_path,
        spec_path_override=experiment_spec_path,
    )
    spec = ExperimentSpec.model_validate(
        yaml.safe_load(experiment_spec_path.read_text(encoding="utf-8"))
    )
    execution = json.loads(execution_bundle_path.read_text(encoding="utf-8"))
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    if preflight.get("status") != "ready_to_submit_unique_run":
        raise ValueError("v37 submission preflight is not ready")
    if preflight.get("config_sha256") != sha256_bytes(manifest_path.read_bytes()):
        raise ValueError("v37 submission preflight belongs to another manifest")
    if preflight.get("experiment_spec") != experiment_binding:
        raise ValueError("v37 submission preflight experiment spec binding drifted")
    if preflight.get("formal_run_submitted") is not False:
        raise ValueError("v37 submission preflight already records a submission")
    required_runtime_keys = {
        "generator_runtimes",
        "metric_plugins_by_name",
        "knowledge_runtime",
        "knowledge_query",
        "pepshot_runtime",
    }
    if not required_runtime_keys.issubset(execution):
        raise ValueError("v37 execution bundle is incomplete")
    expected_generators = {
        item["generator_id"] for item in manifest.generators["engines"]
    }
    if set(execution["generator_runtimes"]) != expected_generators:
        raise ValueError("v37 generator runtime set drifted")
    expected_metrics = {
        item["name"]
        for item in manifest.stage_1_sequence_evaluation["metric_plugins"]
    }
    if set(execution["metric_plugins_by_name"]) != expected_metrics:
        raise ValueError("v37 metric plugin set drifted")
    return payload, spec, execution, preflight


async def submit_v37_once(
    *,
    manifest_path: Path,
    experiment_spec_path: Path,
    execution_bundle_path: Path,
    preflight_path: Path,
) -> dict[str, Any]:
    manifest, spec, execution, preflight = load_v37_submission_bundle(
        manifest_path=manifest_path,
        experiment_spec_path=experiment_spec_path,
        execution_bundle_path=execution_bundle_path,
        preflight_path=preflight_path,
    )
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    execution_bundle_bytes = await asyncio.to_thread(execution_bundle_path.read_bytes)
    experiment_spec_bytes = await asyncio.to_thread(experiment_spec_path.read_bytes)
    raw_experiment_spec = yaml.safe_load(experiment_spec_bytes)
    raw_spec = {
        **spec.model_dump(mode="json"),
        "run_mode": "v37_rapid_champion_generation",
        "benchmark_id": manifest["benchmark_id"],
        "benchmark_version": manifest["version"],
        "manifest_sha256": sha256_json(manifest),
        "submission_preflight_sha256": preflight["submission_preflight_sha256"],
        "execution_bundle_sha256": sha256_bytes(execution_bundle_bytes),
        "experiment_spec_sha256": sha256_bytes(experiment_spec_bytes),
        "all_agent_evidence_persisted": True,
        "database_object_store_replay_required": True,
    }
    async with SessionFactory() as session, session.begin():
        existing = await session.scalar(
            select(ExperimentRun).where(
                ExperimentRun.spec_json["benchmark_id"].astext
                == manifest["benchmark_id"],
                ExperimentRun.spec_json["benchmark_version"].astext
                == manifest["version"],
            )
        )
        repository = ExperimentRepository(session)
        if existing is not None:
            if not _same_v37_submission(existing, raw_spec):
                raise ValueError(f"different v37 formal run already exists: {existing.id}")
            run = existing
        else:
            run = await repository.create_run(
                spec,
                actor="v37-exact-once-submission-cli",
                raw_spec_payload=raw_spec,
            )
        run_id = str(run.id)
        workflow_id = f"pepagent-rapid-champion-v37-{run_id}"
        if run.temporal_workflow_id not in {None, workflow_id}:
            raise ValueError("v37 database workflow reservation drifted")
        if run.temporal_workflow_id is None:
            run.temporal_workflow_id = workflow_id
            await repository.append_event(
                "run",
                run.id,
                "run.workflow_reserved",
                "v37-exact-once-submission-cli",
                {"workflow_id": workflow_id},
            )
    await _start_or_recover_workflow(
        client,
        workflow_id=workflow_id,
        request={
            "run_id": run_id,
            "manifest": manifest,
            "experiment_spec": raw_experiment_spec,
            "submission_preflight": preflight,
            **execution,
        },
    )
    return {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "manifest_sha256": raw_spec["manifest_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit the unique v37 formal run")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--experiment-spec", type=Path, required=True)
    parser.add_argument("--execution-bundle", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("v37 submission is inert without explicit --execute")
    result = asyncio.run(
        submit_v37_once(
            manifest_path=args.manifest.resolve(),
            experiment_spec_path=args.experiment_spec.resolve(),
            execution_bundle_path=args.execution_bundle.resolve(),
            preflight_path=args.preflight.resolve(),
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
