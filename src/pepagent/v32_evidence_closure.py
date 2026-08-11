from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select

from pepagent.db.models import (
    Artifact,
    EvidenceArtifact,
    ExperimentRun,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.enums import RunStatus
from pepagent.evidence_replay import build_database_evidence_graph, replay_v32_portfolio
from pepagent.multiobjective_portfolio import MultiobjectivePortfolioManifest
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.storage.object_store import ContentAddressedObjectStore
from pepagent.v32_acceptance import _canonical_json_bytes
from pepagent.workers.activities import _register_artifact

CLOSURE_VERSION = "v32.closure.0.1"


def load_submitted_manifest(commit: str, repository_path: str) -> dict[str, Any]:
    raw = subprocess.check_output(["git", "show", f"{commit}:{repository_path}"])
    return yaml.safe_load(raw.decode("utf-8"))


async def _artifact_bytes(
    run_id: uuid.UUID, role: str, expected_sha256: str
) -> bytes:
    async with SessionFactory() as session:
        artifact = await session.scalar(
            select(Artifact)
            .join(EvidenceArtifact, EvidenceArtifact.artifact_id == Artifact.id)
            .join(ToolCall, ToolCall.id == EvidenceArtifact.tool_call_id)
            .where(ToolCall.run_id == run_id, EvidenceArtifact.role == role)
        )
    if artifact is None or artifact.sha256 != expected_sha256:
        raise ValueError(f"missing or mismatched artifact: {run_id}/{role}")
    payload = await asyncio.to_thread(
        ContentAddressedObjectStore().get_bytes, artifact.storage_uri
    )
    if sha256_bytes(payload) != expected_sha256:
        raise OSError(f"artifact byte SHA mismatch: {run_id}/{role}")
    return payload


async def _create_closure_run(
    contract: dict[str, Any], acceptance_run: ExperimentRun
) -> ExperimentRun:
    async with SessionFactory() as session, session.begin():
        existing = list(
            await session.scalars(
                select(ExperimentRun).where(
                    ExperimentRun.parent_run_id == acceptance_run.id
                )
            )
        )
        if any(item.spec_json.get("benchmark_id") == contract["benchmark_id"] for item in existing):
            raise ValueError("v32 evidence closure run already exists")
        run = ExperimentRun(
            target_id=acceptance_run.target_id,
            spec_json=contract,
            spec_sha256=sha256_json(contract),
            status=RunStatus.RUNNING,
            parent_run_id=acceptance_run.id,
            temporal_workflow_id=f"database-object-{contract['benchmark_id']}",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        repository = ExperimentRepository(session)
        await repository.append_event(
            "run",
            run.id,
            "run.created",
            "v32-evidence-closure",
            {
                "v32_run_id": contract["v32_run_id"],
                "acceptance_run_id": str(acceptance_run.id),
            },
        )
        await repository.append_event(
            "run",
            run.id,
            "run.started",
            "v32-evidence-closure",
            {"mode": "database_object_store_only"},
        )
        return run


async def execute_closure(config_path: Path) -> dict[str, Any]:
    config_text = await asyncio.to_thread(config_path.read_text, encoding="utf-8")
    contract = yaml.safe_load(config_text)
    if not contract["execution_authorized"]:
        raise ValueError("v32 evidence closure execution is not authorized")
    v32_run_id = uuid.UUID(contract["v32_run_id"])
    acceptance_run_id = uuid.UUID(contract["acceptance_run_id"])
    async with SessionFactory() as session:
        v32_run = await session.get(ExperimentRun, v32_run_id)
        acceptance_run = await session.get(ExperimentRun, acceptance_run_id)
        if v32_run is None or v32_run.status != RunStatus.SUCCEEDED:
            raise ValueError("v32 parent run is not succeeded")
        if acceptance_run is None or acceptance_run.status != RunStatus.SUCCEEDED:
            raise ValueError("v32 acceptance run is not succeeded")
        if acceptance_run.parent_run_id != v32_run_id:
            raise ValueError("acceptance run does not point to the exact v32 parent")

    manifest_source = contract["submitted_manifest"]
    submitted_manifest = await asyncio.to_thread(
        load_submitted_manifest,
        manifest_source["git_commit"],
        manifest_source["repository_path"],
    )
    manifest_sha256 = sha256_json(submitted_manifest)
    if manifest_sha256 != manifest_source["canonical_json_sha256"]:
        raise ValueError("submitted v32 manifest canonical SHA mismatch")
    if v32_run.spec_json.get("manifest_sha256") != manifest_sha256:
        raise ValueError("submitted manifest does not match the v32 run record")
    manifest_bytes = _canonical_json_bytes(submitted_manifest)
    manifest_artifact = await asyncio.to_thread(
        ContentAddressedObjectStore().put_bytes, manifest_bytes, "application/json"
    )

    portfolio_bytes = await _artifact_bytes(
        v32_run_id,
        "portfolio_output",
        contract["expected_parent_artifacts"]["portfolio_output"],
    )
    replay_bytes = await _artifact_bytes(
        v32_run_id,
        "database_replay_bundle",
        contract["expected_parent_artifacts"]["database_replay_bundle"],
    )
    acceptance_payloads = {
        role: await _artifact_bytes(acceptance_run_id, role, digest)
        for role, digest in contract["expected_acceptance_artifacts"].items()
    }
    source_manifest = MultiobjectivePortfolioManifest.model_validate(
        json.loads(manifest_bytes)
    )
    async with SessionFactory() as session:
        graph = await build_database_evidence_graph(session, v32_run_id)
    replayed = replay_v32_portfolio(graph, source_manifest)
    portfolio = json.loads(portfolio_bytes)
    stored_replay = json.loads(replay_bytes)
    acceptance = json.loads(acceptance_payloads["acceptance_manifest_json"])
    derived_replay = json.loads(acceptance_payloads["derived_replay_bundle_json"])
    if replayed != portfolio or replayed != stored_replay["replayed_portfolio"]:
        raise ValueError("object-stored manifest cannot reproduce the v32 portfolio")
    if acceptance["verdict"] != "ready_for_v33_preregistration":
        raise ValueError("acceptance child is not ready for v33 preregistration")
    if not all(acceptance["v33_readiness_gates"].values()):
        raise ValueError("one or more v33 preregistration gates failed")
    if derived_replay["exact_derived_replay"] is not True:
        raise ValueError("acceptance derived replay is not exact")
    closure = {
        "schema_version": "1.0",
        "v32_run_id": str(v32_run_id),
        "acceptance_run_id": str(acceptance_run_id),
        "submitted_manifest_canonical_sha256": manifest_sha256,
        "submitted_manifest_artifact_sha256": manifest_artifact.sha256,
        "parent_graph_sha256": graph["graph_sha256"],
        "portfolio_artifact_sha256": sha256_bytes(portfolio_bytes),
        "parent_replay_artifact_sha256": sha256_bytes(replay_bytes),
        "acceptance_artifact_sha256": {
            role: sha256_bytes(payload) for role, payload in acceptance_payloads.items()
        },
        "database_object_store_only_replay": True,
        "v32_parent_backwrite": False,
        "acceptance_child_backwrite": False,
        "verdict": "ready_for_v33_preregistration",
        "claim_scope": "computational_hypothesis_portfolio_only",
    }
    closure_bytes = _canonical_json_bytes(closure)
    closure_artifact = await asyncio.to_thread(
        ContentAddressedObjectStore().put_bytes, closure_bytes, "application/json"
    )
    run = await _create_closure_run(contract, acceptance_run)
    try:
        async with SessionFactory() as session, session.begin():
            repository = ExperimentRepository(session)
            seal_call = await repository.record_completed_tool_call(
                run.id,
                "v32-submitted-manifest-sealer",
                CLOSURE_VERSION,
                sha256_json({"git": manifest_source["git_commit"]}),
                {
                    "git_commit": manifest_source["git_commit"],
                    "repository_path": manifest_source["repository_path"],
                    "expected_canonical_sha256": manifest_source["canonical_json_sha256"],
                },
                {"canonical_json": True},
                {
                    "canonical_sha256": manifest_sha256,
                    "artifact_sha256": manifest_artifact.sha256,
                },
                model_uri="git-object://v32-submitted-manifest",
            )
            await _register_artifact(
                session,
                seal_call.id,
                asdict(manifest_artifact),
                "submitted_v32_manifest_json",
                {
                    "canonical_json_sha256": manifest_sha256,
                    "source_git_commit": manifest_source["git_commit"],
                },
            )
            audit_call = await repository.record_completed_tool_call(
                run.id,
                "v32-database-object-store-final-auditor",
                CLOSURE_VERSION,
                sha256_json({"implementation_revision": contract["implementation"]["revision"]}),
                {
                    "v32_run_id": str(v32_run_id),
                    "acceptance_run_id": str(acceptance_run_id),
                    "submitted_manifest_artifact_sha256": manifest_artifact.sha256,
                    "expected_artifacts": {
                        **contract["expected_parent_artifacts"],
                        **contract["expected_acceptance_artifacts"],
                    },
                },
                {"filesystem_intermediates_used": False, "parent_backwrite": False},
                closure,
                model_uri="deterministic://v32-database-object-store-final-auditor",
            )
            await repository.record_tool_dependency(
                audit_call.id, seal_call.id, "uses_content_addressed_submitted_manifest"
            )
            closure_row = await _register_artifact(
                session,
                audit_call.id,
                asdict(closure_artifact),
                "v32_evidence_closure_json",
                {"database_object_store_only_replay": True},
            )
            prompt = (
                "Use only persisted database evidence and content-addressed artifacts to verify "
                "the final v32 portfolio and its acceptance exports."
            )
            response = json.dumps(
                closure, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            decision = await repository.record_agent_decision(
                run.id,
                0,
                "v32_database_object_store_evidence_closure",
                "deterministic-evidence-closure-agent",
                CLOSURE_VERSION,
                prompt,
                response,
                closure,
                response_artifact_id=closure_row.id,
            )
            await repository.record_agent_tool_edge(
                decision.id, seal_call.id, "input", "observes_submitted_manifest_artifact"
            )
            await repository.record_agent_tool_edge(
                decision.id, audit_call.id, "output", "materializes_evidence_closure"
            )
            locked = await session.get(ExperimentRun, run.id, with_for_update=True)
            locked.status = RunStatus.SUCCEEDED
            locked.finished_at = datetime.now(UTC)
            await repository.append_event(
                "run",
                run.id,
                "v32.evidence_closure_completed",
                "deterministic-evidence-closure-agent",
                {
                    "closure_artifact_sha256": closure_artifact.sha256,
                    "verdict": closure["verdict"],
                },
            )
    except Exception:
        async with SessionFactory() as session, session.begin():
            repository = ExperimentRepository(session)
            locked = await session.get(ExperimentRun, run.id, with_for_update=True)
            locked.status = RunStatus.FAILED
            locked.finished_at = datetime.now(UTC)
            await repository.append_event(
                "run", run.id, "v32.evidence_closure_failed", "v32-evidence-closure", {}
            )
        raise
    return {
        "run_id": str(run.id),
        "parent_run_id": str(acceptance_run_id),
        "verdict": closure["verdict"],
        "submitted_manifest_artifact": asdict(manifest_artifact),
        "closure_artifact": asdict(closure_artifact),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(execute_closure(args.config)), indent=2))


if __name__ == "__main__":
    main()
