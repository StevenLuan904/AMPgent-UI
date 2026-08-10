from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from temporalio.client import Client

from pepagent.db.models import ExperimentRun
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.schemas import ExperimentSpec
from pepagent.multiobjective_portfolio import MultiobjectivePortfolioManifest
from pepagent.provenance.hashing import sha256_json
from pepagent.settings import get_settings


def load_submission_contract(path: Path) -> tuple[dict[str, Any], ExperimentSpec]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    MultiobjectivePortfolioManifest.model_validate(payload)
    formal = payload["formal_run"]
    if formal["submitted"] or formal["run_id"] or formal["workflow_id"]:
        raise ValueError("v32 formal run has already been submitted")
    if payload["execution_status"] != "implementation_complete":
        raise ValueError("v32 implementation is not frozen as complete")
    if not formal["authorized"] or not formal["submit_exactly_once"]:
        raise ValueError("v32 exact-once formal run is not authorized")
    revision = str(formal["implementation_revision"])
    if len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
        raise ValueError("v32 requires a frozen 40-character implementation revision")
    target_spec_path = (path.parent / payload["target_spec_path"]).resolve()
    spec = ExperimentSpec.model_validate(
        yaml.safe_load(target_spec_path.read_text(encoding="utf-8"))
    )
    return payload, spec


async def submit(path: Path) -> dict[str, Any]:
    payload, spec = load_submission_contract(path)
    settings = get_settings()
    temporal = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    raw_spec = {
        **spec.model_dump(mode="json"),
        "run_mode": "v32_multiobjective_portfolio",
        "benchmark_id": payload["benchmark_id"],
        "benchmark_version": payload["version"],
        "manifest_sha256": sha256_json(payload),
        "implementation_revision": payload["formal_run"]["implementation_revision"],
        "all_agent_evidence_persisted": True,
        "full_database_replay_required": True,
        "positive_charge_optimization_enabled": False,
        "pepmlm_used": False,
        "amplify_used": False,
    }
    async with SessionFactory() as session, session.begin():
        runs = list(await session.scalars(select(ExperimentRun)))
        duplicate = [
            run
            for run in runs
            if run.spec_json.get("benchmark_id") == payload["benchmark_id"]
            and run.spec_json.get("benchmark_version") == payload["version"]
        ]
        if duplicate:
            raise ValueError(
                "v32 formal run already exists in PostgreSQL: "
                + ", ".join(str(run.id) for run in duplicate)
            )
        repository = ExperimentRepository(session)
        run = await repository.create_run(
            spec,
            actor="v32-exact-once-submission-cli",
            raw_spec_payload=raw_spec,
        )
        run_id = str(run.id)
    workflow_id = f"pepagent-multiobjective-v32-{run_id}"
    await temporal.start_workflow(
        "MultiobjectivePortfolioWorkflow",
        {"run_id": run_id, "manifest": payload},
        id=workflow_id,
        task_queue="pepagent-control",
    )
    return {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "manifest_sha256": raw_spec["manifest_sha256"],
        "implementation_revision": raw_spec["implementation_revision"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit the exact-once v32 formal run")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(submit(args.manifest.resolve())), indent=2))


if __name__ == "__main__":
    main()
