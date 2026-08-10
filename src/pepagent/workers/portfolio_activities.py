from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import select
from temporalio import activity

from pepagent.db.models import (
    AgentDecision,
    AgentDecisionToolCallEdge,
    Artifact,
    Candidate,
    Evaluation,
    EvidenceArtifact,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.developability import CANONICAL_AMINO_ACIDS, sequence_developability_metrics
from pepagent.domain.enums import CandidateStatus
from pepagent.evidence_replay import build_database_evidence_graph, replay_v32_portfolio
from pepagent.multiobjective_portfolio import MultiobjectivePortfolioManifest
from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.settings import get_settings
from pepagent.workers.activities import _register_artifact, _store_file, _store_json

PORTFOLIO_ACTIVITY_VERSION = "v32.0.0"


def _verify_file(path: Path, expected_sha256: str, role: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"missing {role}: {path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise OSError(f"{role} SHA mismatch: expected {expected_sha256}, got {actual}")


async def _run_amp_designer(request: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    runtime = request["manifest"]["generator_runtime"]
    paths = await asyncio.to_thread(
        lambda: {
            "python": Path(runtime["python_path"]).resolve(),
            "environment": Path(runtime["environment_manifest_path"]).resolve(),
            "adapter": Path(runtime["adapter_path"]).resolve(),
            "config": Path(runtime["model_config_path"]).resolve(),
            "weights": Path(runtime["model_weights_path"]).resolve(),
            "vocab": Path(runtime["vocab_path"]).resolve(),
        }
    )
    _verify_file(paths["python"], runtime["python_sha256"], "generator Python")
    _verify_file(
        paths["environment"],
        runtime["environment_manifest_sha256"],
        "generator environment manifest",
    )
    _verify_file(paths["adapter"], runtime["adapter_sha256"], "generator adapter")
    _verify_file(paths["config"], runtime["model_config_sha256"], "generator config")
    _verify_file(paths["weights"], runtime["model_weights_sha256"], "generator weights")
    _verify_file(paths["vocab"], runtime["vocab_sha256"], "generator vocabulary")
    await asyncio.to_thread(work_dir.mkdir, parents=True, exist_ok=True)
    input_path = work_dir / "request.json"
    output_path = work_dir / "raw-output.json"
    generation_request = {
        "generator_id": "amp_designer",
        "seed": request["seed"],
        "raw_proposal_budget": 1000,
        "batch_size": 100,
        "batches": 10,
        "top_k": 10,
        "top_p": 1.0,
        "temperature": None,
        "decode_steps": 34,
        "device": "cpu",
    }
    await asyncio.to_thread(
        input_path.write_text,
        json.dumps(generation_request, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    process = await asyncio.create_subprocess_exec(
        str(paths["python"]),
        str(paths["adapter"]),
        "--request",
        str(input_path),
        "--output",
        str(output_path),
        "--config",
        str(paths["config"]),
        "--weights",
        str(paths["weights"]),
        "--vocab",
        str(paths["vocab"]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )
    output_tail: list[str] = []
    while True:
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=30)
        except TimeoutError:
            activity.heartbeat({"seed": request["seed"], "status": "generating"})
            continue
        if not line:
            break
        output_tail.append(line.decode(errors="replace").rstrip())
        output_tail = output_tail[-40:]
    return_code = await process.wait()
    if return_code != 0:
        raise RuntimeError(
            f"AMP-Designer exited with {return_code}: " + "\n".join(output_tail)[-8000:]
        )
    return json.loads(await asyncio.to_thread(output_path.read_text, encoding="utf-8"))


@activity.defn(name="generate_amp_designer_v32")
async def generate_amp_designer_v32(request: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    work_dir = Path(settings.work_root) / request["run_id"] / "v32" / f"seed-{request['seed']}"
    result = await _run_amp_designer(request, work_dir)
    if len(result.get("records", [])) != 1000:
        raise ValueError("v32 AMP-Designer output must contain exactly 1000 raw records")
    runtime = request["manifest"]["generator_runtime"]
    environment = {
        "external_python_sha256": runtime["python_sha256"],
        "environment_manifest_sha256": runtime["environment_manifest_sha256"],
        "host": platform.node(),
        "platform": platform.platform(),
        "orchestrator_python": sys.version,
        "activity_attempt": activity.info().attempt,
    }
    raw_artifact = await _store_json(result)
    runtime_identity_artifact = await _store_json(environment)
    environment_lock_artifact = await _store_file(Path(runtime["environment_manifest_path"]))
    adapter_artifact = await _store_file(Path(runtime["adapter_path"]))
    return {
        "result": result,
        "provenance": {
            "tool_name": "amp-designer-v32-generation",
            "tool_version": result["adapter_version"],
            "model_uri": "local://amp-designer-zenodo-15051980",
            "weights_sha256": runtime["model_weights_sha256"],
            "environment_sha256": runtime["environment_manifest_sha256"],
            "attempt": activity.info().attempt,
            "raw_output_artifact": asdict(raw_artifact),
            "runtime_identity_artifact": asdict(runtime_identity_artifact),
            "environment_lock_artifact": asdict(environment_lock_artifact),
            "adapter_artifact": asdict(adapter_artifact),
        },
    }


@activity.defn(name="persist_v32_generation_batch")
async def persist_v32_generation_batch(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    generated = request["generated"]
    result = generated["result"]
    provenance = generated["provenance"]
    manifest = MultiobjectivePortfolioManifest.model_validate(request["manifest"])
    seed = int(result["seed"])
    if seed not in manifest.seeds:
        raise ValueError(f"unexpected v32 generator seed: {seed}")
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            provenance["tool_name"],
            provenance["tool_version"],
            provenance["environment_sha256"],
            {"seed": seed, "raw_proposal_budget": manifest.raw_proposal_budget_per_seed},
            result["sampling"],
            result,
            weights_sha256=provenance["weights_sha256"],
            model_uri=provenance["model_uri"],
            random_seed=seed,
            attempt=provenance["attempt"],
        )
        existing_candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == run_id, Candidate.generator_call_id == call.id)
                .order_by(Candidate.proposal_rank, Candidate.id)
            )
        )
        if existing_candidates:
            if len(existing_candidates) > manifest.evaluated_valid_unique_per_seed:
                raise ValueError("v32 generation retry recovered an oversized seed cohort")
            recovered = [
                {
                    "id": str(candidate.id),
                    "sequence": candidate.sequence,
                    "sequence_sha256": candidate.sequence_sha256,
                    "seed": candidate.metadata_json["generator_seed"],
                    "raw_rank": candidate.metadata_json["raw_rank"],
                }
                for candidate in existing_candidates
            ]
            return {
                "seed": seed,
                "candidate_count": len(recovered),
                "candidates": recovered,
                "generator_tool_call_id": str(call.id),
                "idempotently_recovered": True,
            }
        await _register_artifact(
            session,
            call.id,
            provenance["raw_output_artifact"],
            "raw_generator_output",
            {"benchmark_id": manifest.benchmark_id, "generator_seed": seed},
        )
        await _register_artifact(
            session,
            call.id,
            provenance["runtime_identity_artifact"],
            "runtime_identity",
            {"benchmark_id": manifest.benchmark_id, "generator_seed": seed},
        )
        await _register_artifact(
            session,
            call.id,
            provenance["environment_lock_artifact"],
            "environment_manifest",
            {"benchmark_id": manifest.benchmark_id, "generator_seed": seed},
        )
        await _register_artifact(
            session,
            call.id,
            provenance["adapter_artifact"],
            "executed_adapter_source",
            {"benchmark_id": manifest.benchmark_id, "generator_seed": seed},
        )
        existing_sequences = set(
            await session.scalars(select(Candidate.sequence).where(Candidate.run_id == run_id))
        )
        selected: list[dict[str, Any]] = []
        seen_in_batch: set[str] = set()
        for expected_rank, record in enumerate(result["records"], start=1):
            if int(record["raw_rank"]) != expected_rank:
                raise ValueError("v32 raw generator ranks are not exact and contiguous")
            sequence = "".join(str(record["sequence"]).split()).upper()
            if not (manifest.minimum_length <= len(sequence) <= manifest.maximum_length):
                continue
            if not sequence or set(sequence) - CANONICAL_AMINO_ACIDS:
                continue
            if sequence in existing_sequences or sequence in seen_in_batch:
                continue
            seen_in_batch.add(sequence)
            selected.append({"raw_rank": expected_rank, "sequence": sequence})
            if len(selected) == manifest.evaluated_valid_unique_per_seed:
                break
        persisted: list[dict[str, Any]] = []
        for seed_rank, item in enumerate(selected, start=1):
            candidate = await repository.add_candidate(
                run_id,
                item["sequence"],
                generation=0,
                proposal_rank=manifest.seeds.index(seed) * 1000 + item["raw_rank"],
                generator_call_id=call.id,
                metadata={
                    "benchmark_id": manifest.benchmark_id,
                    "benchmark_version": manifest.version,
                    "generator_id": manifest.generator_id,
                    "generator_seed": seed,
                    "raw_rank": item["raw_rank"],
                    "seed_valid_unique_rank": seed_rank,
                    "charge_policy": manifest.charge_policy,
                },
                actor="amp-designer-v32-generation",
            )
            descriptor = sequence_developability_metrics(candidate.sequence)
            await repository.record_evaluation(
                candidate.id,
                call.id,
                "maximum_hydrophobic_run",
                float(descriptor["maximum_hydrophobic_run"]),
                "residues",
                {
                    "method_version": descriptor["method_version"],
                    "limitations": descriptor["limitations"],
                    "selection_role": "v32_membrane_descriptor",
                },
                limitations=descriptor["limitations"],
            )
            persisted.append(
                {
                    "id": str(candidate.id),
                    "sequence": candidate.sequence,
                    "sequence_sha256": candidate.sequence_sha256,
                    "seed": seed,
                    "raw_rank": item["raw_rank"],
                }
            )
        await repository.append_event(
            "run",
            run_id,
            "v32.generator_batch_frozen",
            "amp-designer-v32-generation",
            {
                "seed": seed,
                "raw_count": len(result["records"]),
                "valid_unique_count": len(persisted),
                "generator_tool_call_id": str(call.id),
                "candidate_order_sha256": sha256_json(persisted),
                "no_refill": True,
            },
        )
    return {
        "seed": seed,
        "candidate_count": len(persisted),
        "candidates": persisted,
        "generator_tool_call_id": str(call.id),
    }


async def _v32_candidate_payloads(
    session: Any, run_id: uuid.UUID, manifest: MultiobjectivePortfolioManifest
) -> list[dict[str, Any]]:
    candidates = list(
        await session.scalars(
            select(Candidate)
            .where(Candidate.run_id == run_id)
            .order_by(Candidate.proposal_rank, Candidate.id)
        )
    )
    evaluations = list(
        await session.scalars(
            select(Evaluation).where(Evaluation.candidate_id.in_([item.id for item in candidates]))
        )
    )
    numeric: dict[uuid.UUID, dict[str, float]] = {}
    labels: dict[uuid.UUID, dict[str, str]] = {}
    for evaluation in evaluations:
        target = numeric.setdefault(evaluation.candidate_id, {})
        if evaluation.numeric_value is not None:
            if evaluation.metric_name in target:
                raise ValueError(
                    f"ambiguous v32 metric: {evaluation.candidate_id}/{evaluation.metric_name}"
                )
            target[evaluation.metric_name] = float(evaluation.numeric_value)
        if evaluation.text_value is not None:
            text = labels.setdefault(evaluation.candidate_id, {})
            if evaluation.metric_name in text:
                raise ValueError(
                    f"ambiguous v32 label: {evaluation.candidate_id}/{evaluation.metric_name}"
                )
            text[evaluation.metric_name] = evaluation.text_value
    required_labels = {
        manifest.risk_guard["toxicity_label_metric"],
        manifest.risk_guard["hemolysis_label_metric"],
    }
    payloads = []
    for candidate in candidates:
        missing_labels = required_labels - set(labels.get(candidate.id, {}))
        if missing_labels:
            raise ValueError(f"missing v32 labels for {candidate.id}: {sorted(missing_labels)}")
        payloads.append(
            {
                "id": str(candidate.id),
                "seed": candidate.metadata_json["generator_seed"],
                "sequence": candidate.sequence,
                "sequence_sha256": candidate.sequence_sha256,
                "metrics": numeric.get(candidate.id, {}),
                "labels": labels[candidate.id],
            }
        )
    return payloads


@activity.defn(name="persist_v32_portfolio_decision")
async def persist_v32_portfolio_decision(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    manifest = MultiobjectivePortfolioManifest.model_validate(request["manifest"])
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        existing_decision = await session.scalar(
            select(AgentDecision).where(
                AgentDecision.run_id == run_id,
                AgentDecision.decision_type == "v32_multiobjective_portfolio",
            )
        )
        if existing_decision is not None:
            output_edge = await session.scalar(
                select(AgentDecisionToolCallEdge).where(
                    AgentDecisionToolCallEdge.decision_id == existing_decision.id,
                    AgentDecisionToolCallEdge.direction == "output",
                    AgentDecisionToolCallEdge.relation_type == "materializes_portfolio",
                )
            )
            if output_edge is None:
                raise ValueError("persisted v32 decision lacks its output ToolCall edge")
            selection_call = await session.get(ToolCall, output_edge.tool_call_id)
            if selection_call is None or selection_call.output_sha256 is None:
                raise ValueError("persisted v32 decision output ToolCall is incomplete")
            return {
                "decision_id": str(existing_decision.id),
                "selection_tool_call_id": str(selection_call.id),
                "portfolio_sha256": selection_call.output_sha256,
                "portfolio": existing_decision.structured_json,
                "idempotently_recovered": True,
            }
        candidates = await _v32_candidate_payloads(session, run_id, manifest)
        portfolio = replay_v32_portfolio(
            {
                "candidates": [
                    {
                        "id": item["id"],
                        "sequence": item["sequence"],
                        "sequence_sha256": item["sequence_sha256"],
                        "metadata": {"generator_seed": item["seed"]},
                    }
                    for item in candidates
                ],
                "evaluations": [
                    *[
                        {
                            "candidate_id": item["id"],
                            "metric_name": name,
                            "numeric_value": value,
                            "text_value": None,
                        }
                        for item in candidates
                        for name, value in item["metrics"].items()
                    ],
                    *[
                        {
                            "candidate_id": item["id"],
                            "metric_name": name,
                            "numeric_value": None,
                            "text_value": value,
                        }
                        for item in candidates
                        for name, value in item["labels"].items()
                    ],
                ],
            },
            manifest,
        )
        if not portfolio["selection_complete"]:
            raise ValueError("v32 portfolio is incomplete; no refill is allowed")
        environment_sha256 = sha256_json(
            {"policy": portfolio["policy"], "version": PORTFOLIO_ACTIVITY_VERSION}
        )
        selection_call = await repository.record_completed_tool_call(
            run_id,
            "v32-evidence-governed-pareto-portfolio",
            PORTFOLIO_ACTIVITY_VERSION,
            environment_sha256,
            {"candidate_evidence": candidates},
            {
                "manifest_sha256": sha256_json(request["manifest"]),
                "no_weighted_total": True,
                "charge_optimized": False,
            },
            portfolio,
            model_uri="deterministic://v32-evidence-governed-pareto-portfolio",
        )
        evidence_call_ids = set(
            await session.scalars(
                select(Evaluation.tool_call_id).where(
                    Evaluation.candidate_id.in_([uuid.UUID(item["id"]) for item in candidates])
                )
            )
        )
        evidence_call_ids.update(
            await session.scalars(
                select(Candidate.generator_call_id).where(Candidate.run_id == run_id)
            )
        )
        for call_id in sorted((item for item in evidence_call_ids if item), key=str):
            await repository.record_tool_dependency(
                selection_call.id, call_id, "portfolio_uses_evidence"
            )
        output_artifact = await _store_json(portfolio)
        output_row = await _register_artifact(
            session,
            selection_call.id,
            asdict(output_artifact),
            "portfolio_output",
            {"benchmark_id": manifest.benchmark_id, "claim_scope": "computational_only"},
        )
        prompt = (
            "Apply the frozen v32 evidence-governed Pareto portfolio policy to all exact "
            "database observations. Preserve endpoint families and do not use a weighted total."
        )
        response = json.dumps(portfolio, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        decision = await repository.record_agent_decision(
            run_id,
            0,
            "v32_multiobjective_portfolio",
            "deterministic-evidence-governance-agent",
            PORTFOLIO_ACTIVITY_VERSION,
            prompt,
            response,
            portfolio,
            response_artifact_id=output_row.id,
        )
        for call_id in sorted((item for item in evidence_call_ids if item), key=str):
            await repository.record_agent_tool_edge(
                decision.id, call_id, "input", "observes_evidence"
            )
        await repository.record_agent_tool_edge(
            decision.id, selection_call.id, "output", "materializes_portfolio"
        )
        selected_ids = {
            uuid.UUID(item["candidate_id"]) for item in portfolio["lane_results"]
        }
        for candidate in await session.scalars(select(Candidate).where(Candidate.run_id == run_id)):
            await repository.transition_candidate(
                candidate.id,
                (
                    CandidateStatus.SELECTED
                    if candidate.id in selected_ids
                    else CandidateStatus.REJECTED
                ),
                "deterministic-evidence-governance-agent",
                "selected into frozen v32 portfolio"
                if candidate.id in selected_ids
                else "not selected into v32 portfolio; no experimental rejection claim",
            )
    return {
        "decision_id": str(decision.id),
        "selection_tool_call_id": str(selection_call.id),
        "portfolio_sha256": selection_call.output_sha256,
        "portfolio": portfolio,
    }


@activity.defn(name="persist_v32_replay_bundle")
async def persist_v32_replay_bundle(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    manifest = MultiobjectivePortfolioManifest.model_validate(request["manifest"])
    expected = request["portfolio"]
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        existing_call = await session.scalar(
            select(ToolCall).where(
                ToolCall.run_id == run_id,
                ToolCall.tool_name == "v32-database-only-replay-verifier",
            )
        )
        if existing_call is not None:
            link = await session.scalar(
                select(EvidenceArtifact).where(
                    EvidenceArtifact.tool_call_id == existing_call.id,
                    EvidenceArtifact.role == "database_replay_bundle",
                )
            )
            if link is None:
                raise ValueError("persisted replay verifier lacks its replay bundle artifact")
            artifact_row = await session.get(Artifact, link.artifact_id)
            if artifact_row is None:
                raise ValueError("persisted replay bundle artifact row is missing")
            return {
                "exact_replay": True,
                "replay_tool_call_id": str(existing_call.id),
                "replay_bundle_sha256": artifact_row.sha256,
                "idempotently_recovered": True,
            }
        graph = await build_database_evidence_graph(session, run_id)
        replayed = replay_v32_portfolio(graph, manifest)
        if replayed != expected:
            raise ValueError("database-only v32 replay differs from the persisted portfolio")
        bundle = {
            "schema_version": "1.0",
            "evidence_graph": graph,
            "replayed_portfolio": replayed,
            "replayed_portfolio_sha256": sha256_json(replayed),
            "exact_replay": True,
        }
        stored = await _store_json(bundle)
        replay_call = await repository.record_completed_tool_call(
            run_id,
            "v32-database-only-replay-verifier",
            PORTFOLIO_ACTIVITY_VERSION,
            sha256_json({"implementation": "database-only-replay-v1"}),
            {
                "evidence_graph_sha256": graph["graph_sha256"],
                "expected_portfolio_sha256": sha256_json(expected),
            },
            {"filesystem_intermediates_used": False, "exact_replay_required": True},
            {
                "exact_replay": True,
                "replayed_portfolio_sha256": sha256_json(replayed),
                "replay_bundle_sha256": stored.sha256,
            },
            model_uri="deterministic://v32-database-only-replay-verifier",
        )
        await repository.record_tool_dependency(
            replay_call.id,
            uuid.UUID(request["selection_tool_call_id"]),
            "verifies_portfolio_replay",
        )
        await _register_artifact(
            session,
            replay_call.id,
            asdict(stored),
            "database_replay_bundle",
            {"benchmark_id": manifest.benchmark_id, "exact_replay": True},
        )
        await repository.append_event(
            "run",
            run_id,
            "v32.database_replay_verified",
            "v32-database-only-replay-verifier",
            {
                "replay_tool_call_id": str(replay_call.id),
                "replay_bundle_sha256": stored.sha256,
                "portfolio_sha256": sha256_json(replayed),
            },
        )
    return {
        "exact_replay": True,
        "replay_tool_call_id": str(replay_call.id),
        "replay_bundle_sha256": stored.sha256,
    }
