from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import signal
import sys
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from pepagent.db.models import (
    Artifact,
    Candidate,
    Evaluation,
    EvidenceArtifact,
    ExperimentRun,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.developability import (
    HYDROPHOBIC_RESIDUES,
    INSTABILITY_METHOD,
    SEQUENCE_DEVELOPABILITY_VERSION,
    sequence_developability_metrics,
)
from pepagent.domain.enums import CandidateStatus, MetricName, RunStatus
from pepagent.handoff_metrics import HANDOFF_METRIC_VERSION
from pepagent.mutation_context import build_mutation_brief, canonical_sha256, evidence_hashes
from pepagent.provenance.environment import fingerprint_runtime
from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.reporting import (
    BULK_ROSETTA_CSV_COLUMNS,
    build_bulk_rosetta_rows,
    render_bulk_rosetta_csv,
)
from pepagent.selection import (
    cheap_diverse_selection,
    diagnostic_representative_selection,
    diversity_constrained_elites,
    progressive_evaluation_plan,
    qualification_violations,
    research_iteration_directive,
)
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore, StoredObject
from pepagent.structures.interface import (
    audit_protein_peptide_interface,
    classify_structure_support,
    pose_cluster_fraction,
    reconcile_ensemble_structure_support,
)


async def _terminate_subprocess_tree(process: asyncio.subprocess.Process) -> None:
    """Stop a model subprocess and its descendants after activity cancellation."""
    if process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=10)
        return
    except TimeoutError:
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        return
    await process.wait()


async def _run_json_cli(module: str, request: dict[str, Any], work_dir: Path, *extra: str) -> dict:
    await asyncio.to_thread(work_dir.mkdir, parents=True, exist_ok=True)
    request_path = work_dir / "request.json"
    output_path = work_dir / "result.json"
    await asyncio.to_thread(
        request_path.write_text,
        json.dumps(request, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        module,
        "--request",
        str(request_path),
        "--output",
        str(output_path),
        *extra,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )
    output_tail: list[str] = []
    try:
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=30)
            except TimeoutError:
                activity.heartbeat({"module": module, "status": "running"})
                continue
            if not line:
                break
            decoded = line.decode(errors="replace").rstrip()
            output_tail.append(decoded)
            output_tail = output_tail[-200:]
            activity.logger.info(decoded)
            activity.heartbeat({"module": module, "status": "running"})
        return_code = await process.wait()
    except BaseException:
        await _terminate_subprocess_tree(process)
        raise
    if return_code != 0:
        diagnostic = "\n".join(output_tail[-20:])[-8000:]
        raise RuntimeError(f"{module} exited with code {return_code}\n{diagnostic}")
    output = await asyncio.to_thread(output_path.read_text, encoding="utf-8")
    return json.loads(output)


async def _store_json(payload: dict[str, Any]) -> StoredObject:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return await asyncio.to_thread(
        ContentAddressedObjectStore().put_bytes, encoded, "application/json"
    )


async def _store_file(path: Path) -> StoredObject:
    return await asyncio.to_thread(ContentAddressedObjectStore().put_file, path)


async def _store_text(payload: str) -> StoredObject:
    return await asyncio.to_thread(
        ContentAddressedObjectStore().put_bytes,
        payload.encode("utf-8"),
        "text/plain; charset=utf-8",
    )


async def _verify_pepmlm_release(model_path: str, expected_sha256: str) -> None:
    release_dir = Path(model_path)
    if not await asyncio.to_thread(release_dir.is_dir):
        raise RuntimeError(
            "PepMLM worker requires a local immutable model release; remote model IDs are not "
            "accepted because the executed weight bytes cannot be proven"
        )
    weights_path = release_dir / "pytorch_model.bin"
    if not await asyncio.to_thread(weights_path.is_file):
        raise FileNotFoundError(f"PepMLM weights missing: {weights_path}")
    actual_sha256 = await asyncio.to_thread(sha256_file, weights_path)
    if actual_sha256 != expected_sha256:
        raise OSError(
            f"PepMLM weight checksum mismatch: expected {expected_sha256}, got {actual_sha256}"
        )


def _boltz_weight_manifest(cache_path: str) -> list[dict[str, Any]]:
    cache_dir = Path(cache_path)
    candidates = sorted(
        path
        for path in cache_dir.rglob("*")
        if path.is_file()
        and (
            (
                path.suffix.lower() in {".ckpt", ".pt", ".pth", ".safetensors"}
                and path.stat().st_size >= 1024 * 1024
            )
            or path.relative_to(cache_dir).as_posix() == "mols.tar"
        )
    )
    if not candidates:
        raise FileNotFoundError(f"no Boltz checkpoint files found below {cache_dir}")
    return [
        {
            "path": str(path.relative_to(cache_dir)),
            "role": ("molecular_resource_archive" if path.name == "mols.tar" else "weights"),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in candidates
    ]


async def _register_artifact(
    session: AsyncSession,
    tool_call_id: uuid.UUID,
    stored_payload: dict[str, Any],
    role: str,
    metadata: dict[str, Any],
) -> Artifact:
    artifact = await session.scalar(
        select(Artifact).where(Artifact.sha256 == stored_payload["sha256"])
    )
    if artifact is None:
        artifact = Artifact(
            sha256=stored_payload["sha256"],
            size_bytes=stored_payload["size_bytes"],
            media_type=stored_payload["media_type"],
            storage_uri=stored_payload["uri"],
            metadata_json=metadata,
        )
        session.add(artifact)
        await session.flush()
    link = await session.get(
        EvidenceArtifact,
        {"tool_call_id": tool_call_id, "artifact_id": artifact.id, "role": role},
    )
    if link is None:
        session.add(EvidenceArtifact(tool_call_id=tool_call_id, artifact_id=artifact.id, role=role))
    return artifact


async def _register_stored_artifact(
    session: AsyncSession, stored_payload: dict[str, Any], metadata: dict[str, Any]
) -> Artifact:
    artifact = await session.scalar(
        select(Artifact).where(Artifact.sha256 == stored_payload["sha256"])
    )
    if artifact is None:
        artifact = Artifact(
            sha256=stored_payload["sha256"],
            size_bytes=stored_payload["size_bytes"],
            media_type=stored_payload["media_type"],
            storage_uri=stored_payload["uri"],
            metadata_json=metadata,
        )
        session.add(artifact)
        await session.flush()
    return artifact


@activity.defn(name="mark_run_started")
async def mark_run_started(request: dict[str, Any]) -> None:
    async with SessionFactory() as session, session.begin():
        await ExperimentRepository(session).mark_run_started(
            uuid.UUID(request["run_id"]), request["workflow_id"], activity.info().workflow_run_id
        )


@activity.defn(name="mark_run_failed")
async def mark_run_failed(request: dict[str, Any]) -> None:
    run_id = uuid.UUID(request["run_id"])
    async with SessionFactory() as session, session.begin():
        run = await session.scalar(
            select(ExperimentRun).where(ExperimentRun.id == run_id).with_for_update()
        )
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        if run.status in {RunStatus.FAILED, RunStatus.SUCCEEDED, RunStatus.CANCELLED}:
            return
        run.status = RunStatus.FAILED
        run.finished_at = datetime.now(UTC)
        await ExperimentRepository(session).append_event(
            "run",
            run_id,
            "run.failed",
            "temporal",
            {
                "error_type": request["error_type"],
                "error": request["error"][:4000],
            },
        )


@activity.defn(name="mark_run_cancelled")
async def mark_run_cancelled(request: dict[str, Any]) -> None:
    """Reconcile a Temporal cancellation into durable run and candidate state."""
    run_id = uuid.UUID(request["run_id"])
    async with SessionFactory() as session, session.begin():
        run = await session.scalar(
            select(ExperimentRun).where(ExperimentRun.id == run_id).with_for_update()
        )
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        if run.status in {RunStatus.CANCELLED, RunStatus.SUCCEEDED, RunStatus.FAILED}:
            return
        queued = list(
            await session.scalars(
                select(Candidate).where(
                    Candidate.run_id == run_id,
                    Candidate.status.in_(
                        [CandidateStatus.STRUCTURE_QUEUED, CandidateStatus.ROSETTA_QUEUED]
                    ),
                )
            )
        )
        for candidate in queued:
            candidate.status = CandidateStatus.CANCELLED
        run.status = RunStatus.CANCELLED
        run.finished_at = datetime.now(UTC)
        await ExperimentRepository(session).append_event(
            "run",
            run_id,
            "run.cancelled",
            "temporal",
            {
                "reason": request.get("reason") or "workflow_cancelled",
                "cancelled_candidate_count": len(queued),
            },
        )


@activity.defn(name="generate_with_pepmlm")
async def generate_with_pepmlm(request: dict[str, Any]) -> list[dict[str, Any]]:
    settings = get_settings()
    spec = request["spec"]
    await _verify_pepmlm_release(settings.pepmlm_model_path, settings.pepmlm_weights_sha256)
    environment_sha256, environment = fingerprint_runtime()
    batches: list[dict[str, Any]] = []
    generation = int(request.get("generation", 0))
    parents = request.get("parents", [])
    payloads: list[tuple[str, dict[str, Any]]] = []
    if parents:
        payloads.append(
            (
                "mutations",
                {
                    "target_sequence": spec["target"]["sequence"],
                    "parent_sequences": [parent["sequence"] for parent in parents],
                    "children_per_parent": spec.get("mutation_children_per_parent", 3),
                    "mutation_count_min": spec.get("mutation_count_min", 1),
                    "mutation_count_max": spec.get("mutation_count_max", 3),
                    "seed": spec["seed"] + generation * 1_000_000,
                    "model": settings.pepmlm_model_path,
                    "revision": settings.pepmlm_model_revision,
                    "top_k": spec.get("pepmlm_mutation_top_k", 5),
                    "temperature": spec.get("pepmlm_temperature", 1.0),
                    "mutation_briefs": [
                        parent["mutation_brief"]
                        for parent in parents
                        if parent.get("mutation_brief") is not None
                    ],
                    "parent_sources": [
                        {
                            "source_run_id": parent.get("source_run_id"),
                            "source_candidate_id": parent["id"],
                            "sequence_sha256": parent.get("sequence_sha256"),
                            "evidence_sha256": parent.get("evidence_sha256", []),
                            "selection_rationale": parent.get("selection_rationale"),
                        }
                        for parent in parents
                    ],
                },
            )
        )
    for length_index, length in enumerate(spec["peptide_lengths"]):
        count = (
            spec["candidates_per_length"]
            if generation == 0
            else spec.get("exploration_candidates_per_length", 2)
        )
        if count == 0:
            continue
        payload = {
            "target_sequence": spec["target"]["sequence"],
            "peptide_length": length,
            "count": count,
            "seed": spec["seed"] + generation * 1_000_000 + (length_index + 1) * 100_000,
            "model": settings.pepmlm_model_path,
            "revision": settings.pepmlm_model_revision,
            "top_k": spec.get("pepmlm_de_novo_top_k", 3),
            "temperature": spec.get("pepmlm_temperature", 1.0),
        }
        payloads.append((f"de-novo-{length}", payload))
    for batch_name, payload in payloads:
        result = await _run_json_cli(
            "pepagent.model_workers.pepmlm_cli",
            payload,
            Path(settings.work_root)
            / request["run_id"]
            / "pepmlm"
            / f"generation-{generation}"
            / batch_name,
        )
        raw_artifact = await _store_json(result)
        environment_artifact = await _store_json(environment)
        recorded_input = {
            **payload,
            "model": spec["pepmlm_model"],
        }
        batches.append(
            {
                "input": recorded_input,
                "parameters": {
                    "generation": generation,
                    "proposal_mode": result["proposal_mode"],
                    "requested_count": result["requested_count"],
                    "top_k": payload["top_k"],
                    "temperature": payload["temperature"],
                },
                "result": result,
                "provenance": {
                    "tool_name": "pepmlm",
                    "tool_version": settings.pepmlm_model_revision,
                    "model_uri": "hf://ChatterjeeLab/PepMLM-650M",
                    "weights_sha256": settings.pepmlm_weights_sha256,
                    "environment_sha256": environment_sha256,
                    "environment": environment,
                    "random_seed": payload["seed"],
                    "attempt": activity.info().attempt,
                    "raw_output_artifact": asdict(raw_artifact),
                    "environment_artifact": asdict(environment_artifact),
                    "execution_model_path": settings.pepmlm_model_path,
                },
            }
        )
    return batches


@activity.defn(name="score_target_specific_pepmlm_proxy")
async def score_target_specific_pepmlm_proxy(request: dict[str, Any]) -> dict[str, Any]:
    """Score a fixed candidate cohort against one primary and an immutable decoy panel."""
    metric_version = request.get("metric_version", "v21_pooled")
    if metric_version not in {"v21_pooled", "v22_stratified"}:
        raise ValueError(f"unsupported metric_version: {metric_version!r}")
    settings = get_settings()
    await _verify_pepmlm_release(settings.pepmlm_model_path, settings.pepmlm_weights_sha256)
    environment_sha256, environment = fingerprint_runtime()
    payload = {
        "peptides": request["peptides"],
        "targets": request["targets"],
        "target_panel_sha256": request["target_panel_sha256"],
        "model": settings.pepmlm_model_path,
        "revision": settings.pepmlm_model_revision,
        "metric_version": metric_version,
    }
    result = await _run_json_cli(
        "pepagent.model_workers.pepmlm_target_proxy_cli",
        payload,
        Path(settings.work_root) / request["run_id"] / "pepmlm-target-proxy",
    )
    if result.get("metric_version") != metric_version:
        raise ValueError("proxy result metric_version does not match request")
    _validate_proxy_result_contract(
        {"parameters": {"metric_version": metric_version}, "result": result}
    )
    raw_artifact = await _store_json(result)
    environment_artifact = await _store_json(environment)
    definition = result.get("definition")
    return {
        "input": {
            **payload,
            "model": request.get("model_name", "ChatterjeeLab/PepMLM-650M"),
        },
        "parameters": {
            "metric": "target_specific_delta_nll",
            "metric_version": metric_version,
            "definition": definition,
            "confidence": "low",
            "rank_only": True,
            "admission_status": "out_of_domain",
            "independence": "not_independent_from_pepmlm_generation_or_ppl",
        },
        "result": result,
        "provenance": {
            "tool_name": "pepmlm-target-specific-delta-nll",
            "tool_version": "v1",
            "model_uri": "hf://ChatterjeeLab/PepMLM-650M",
            "weights_sha256": settings.pepmlm_weights_sha256,
            "environment_sha256": environment_sha256,
            "environment": environment,
            "attempt": activity.info().attempt,
            "raw_output_artifact": asdict(raw_artifact),
            "environment_artifact": asdict(environment_artifact),
            "execution_model_path": settings.pepmlm_model_path,
        },
    }


def _validate_proxy_result_contract(scored: dict[str, Any]) -> str:
    metric_version = scored["parameters"].get("metric_version", "v21_pooled")
    if metric_version not in {"v21_pooled", "v22_stratified"}:
        raise ValueError(f"unsupported metric_version: {metric_version!r}")
    output = scored["result"]
    output_metric_version = output.get("metric_version")
    if output_metric_version is not None and output_metric_version != metric_version:
        raise ValueError("persisted proxy metric_version does not match tool parameters")
    if metric_version == "v22_stratified" and output_metric_version is None:
        raise ValueError("v22 proxy output is missing metric_version")
    for result in output["results"]:
        if metric_version != "v22_stratified":
            continue
        required = {
            "metric_version",
            "control_type_nll_medians",
            "stratified_control_target_nll",
            "pooled_v21_compatible_secondary",
            "control_type_sensitivity",
        }
        missing = required.difference(result)
        if missing:
            raise ValueError(
                "v22 proxy result missing required fields: "
                + ", ".join(sorted(missing))
            )
        if result["metric_version"] != metric_version:
            raise ValueError("v22 candidate result metric_version mismatch")
        if not result["pooled_v21_compatible_secondary"].get("secondary"):
            raise ValueError("v22 pooled metric must be marked secondary")
        if not result["control_type_sensitivity"].get("diagnostic_only"):
            raise ValueError("v22 type sensitivity must be diagnostic-only")
    return metric_version


@activity.defn(name="persist_target_specific_pepmlm_proxy")
async def persist_target_specific_pepmlm_proxy(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    scored = request["scored"]
    provenance = scored["provenance"]
    metric_version = _validate_proxy_result_contract(scored)
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            provenance["tool_name"],
            provenance["tool_version"],
            provenance["environment_sha256"],
            scored["input"],
            scored["parameters"],
            scored["result"],
            weights_sha256=provenance["weights_sha256"],
            model_uri=provenance["model_uri"],
            attempt=provenance["attempt"],
        )
        await _register_artifact(
            session,
            call.id,
            provenance["raw_output_artifact"],
            "raw_output",
            {
                "tool": provenance["tool_name"],
                "target_panel_sha256": scored["result"]["target_panel_sha256"],
            },
        )
        await _register_artifact(
            session,
            call.id,
            provenance["environment_artifact"],
            "environment_manifest",
            {"tool": provenance["tool_name"], "kind": "runtime_environment"},
        )
        candidate_by_sha = {
            candidate.sequence_sha256: candidate
            for candidate in await session.scalars(
                select(Candidate).where(Candidate.run_id == run_id)
            )
        }
        persisted: list[dict[str, Any]] = []
        for result in scored["result"]["results"]:
            candidate = candidate_by_sha.get(result["sequence_sha256"])
            if candidate is None:
                raise KeyError(
                    "proxy result does not resolve to a candidate in the same run: "
                    f"{result['sequence_sha256']}"
                )
            await repository.record_evaluation(
                candidate.id,
                call.id,
                MetricName.TARGET_SPECIFIC_DELTA_NLL,
                float(result["target_specific_delta_nll"]),
                "nats_per_residue_difference",
                result,
                out_of_domain=True,
                limitations=[
                    "low-confidence sequence-conditioning proxy; rank-only",
                    "not a binding probability or affinity estimate",
                    "not independent from PepMLM generation or conditional PPL",
                    "cannot override conflicting or negative structural evidence",
                ],
            )
            await repository.transition_candidate(
                candidate.id,
                CandidateStatus.PPL_SCORED,
                provenance["tool_name"],
                "out-of-domain target-specific sequence proxy persisted",
            )
            persisted.append(
                {
                    "candidate_id": str(candidate.id),
                    "sequence_sha256": candidate.sequence_sha256,
                    "target_specific_delta_nll": result["target_specific_delta_nll"],
                    "metric_version": metric_version,
                    **(
                        {
                            "stratified_control_target_nll": result[
                                "stratified_control_target_nll"
                            ],
                            "control_type_nll_medians": result[
                                "control_type_nll_medians"
                            ],
                            "pooled_v21_compatible_secondary": result[
                                "pooled_v21_compatible_secondary"
                            ],
                            "control_type_sensitivity": result[
                                "control_type_sensitivity"
                            ],
                        }
                        if metric_version == "v22_stratified"
                        else {}
                    ),
                }
            )
    return {"tool_call_id": str(call.id), "results": persisted}


@activity.defn(name="persist_and_select_candidates")
async def persist_and_select_candidates(request: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    run_id = uuid.UUID(request["run_id"])
    generation = int(request.get("generation", 0))
    parents_by_sequence = {parent["sequence"]: parent for parent in request.get("parents", [])}
    decision_id = request.get("decision_id")
    persisted: list[dict[str, Any]] = []
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        for batch in request["generated"]:
            provenance = batch["provenance"]
            call = await repository.record_completed_tool_call(
                run_id,
                provenance["tool_name"],
                provenance["tool_version"],
                provenance["environment_sha256"],
                batch["input"],
                batch["parameters"],
                batch["result"],
                weights_sha256=provenance["weights_sha256"],
                model_uri=provenance["model_uri"],
                random_seed=provenance["random_seed"],
                attempt=provenance["attempt"],
            )
            if decision_id:
                await repository.record_agent_tool_edge(
                    uuid.UUID(decision_id), call.id, "output", "proposes"
                )
            await _register_artifact(
                session,
                call.id,
                provenance["raw_output_artifact"],
                "raw_output",
                {
                    "tool": "pepmlm",
                    "environment_sha256": provenance["environment_sha256"],
                },
            )
            await _register_artifact(
                session,
                call.id,
                provenance["environment_artifact"],
                "environment_manifest",
                {"tool": "pepmlm", "kind": "runtime_environment"},
            )
            for item in batch["result"]["candidates"]:
                parent = parents_by_sequence.get(item.get("parent_sequence"))
                candidate = await repository.add_candidate(
                    run_id,
                    item["sequence"],
                    generation=generation,
                    proposal_rank=0,
                    generator_call_id=call.id,
                    parent_id=uuid.UUID(parent["id"]) if parent else None,
                    metadata={
                        "per_residue_log_probabilities": item["per_residue_log_probabilities"],
                        "seed": item["seed"],
                        "proposal_mode": item.get("proposal_mode", "de_novo"),
                        "mutation_positions": item.get("mutation_positions", []),
                          "parent_sequence_sha256": (
                              parent.get("sequence_sha256") if parent else None
                          ),
                          "parent_source_run_id": (
                              parent.get("source_run_id") if parent else None
                          ),
                          "parent_evidence_sha256": (
                              parent.get("evidence_sha256", []) if parent else []
                          ),
                          "parent_selection_rationale": (
                              parent.get("selection_rationale") if parent else None
                          ),
                    },
                )
                raw_metric = {
                    "seed": item["seed"],
                    "per_residue_log_probabilities": item["per_residue_log_probabilities"],
                }
                await repository.record_evaluation(
                    candidate.id,
                    call.id,
                    MetricName.CONDITIONAL_NLL,
                    item["conditional_nll"],
                    "nats_per_residue",
                    raw_metric,
                )
                developability = sequence_developability_metrics(item["sequence"])
                developability_call = await repository.record_completed_tool_call(
                    run_id,
                    "sequence-developability-audit",
                    SEQUENCE_DEVELOPABILITY_VERSION,
                    provenance["environment_sha256"],
                    {"sequence": item["sequence"]},
                    {
                        "hydrophobic_residues": "".join(sorted(HYDROPHOBIC_RESIDUES)),
                        "method": "sequence-developability-and-instability-screen",
                        "instability_method": INSTABILITY_METHOD,
                    },
                    developability,
                    model_uri="deterministic://sequence-developability-audit",
                )
                if decision_id:
                    await repository.record_agent_tool_edge(
                        uuid.UUID(decision_id),
                        developability_call.id,
                        "output",
                        "evaluates",
                    )
                for metric_name in (
                    MetricName.INSTABILITY_INDEX,
                    MetricName.HYDROPHOBIC_FRACTION,
                    MetricName.MAXIMUM_HYDROPHOBIC_RUN,
                    MetricName.MAXIMUM_IDENTICAL_RESIDUE_RUN,
                    MetricName.MOLECULAR_WEIGHT_DA,
                    MetricName.NET_CHARGE_PH7_4,
                    MetricName.ISOELECTRIC_POINT,
                    MetricName.GRAVY,
                    MetricName.HYDROPHOBIC_MOMENT_EISENBERG,
                    MetricName.CATIONIC_RESIDUE_FRACTION,
                ):
                    units = {
                        MetricName.HYDROPHOBIC_FRACTION: "fraction",
                        MetricName.CATIONIC_RESIDUE_FRACTION: "fraction",
                        MetricName.MAXIMUM_HYDROPHOBIC_RUN: "residues",
                        MetricName.MAXIMUM_IDENTICAL_RESIDUE_RUN: "residues",
                        MetricName.MOLECULAR_WEIGHT_DA: "Da",
                        MetricName.NET_CHARGE_PH7_4: "elementary_charge",
                        MetricName.ISOELECTRIC_POINT: "pH",
                    }
                    await repository.record_evaluation(
                        candidate.id,
                        developability_call.id,
                        metric_name,
                        float(developability[metric_name]),
                        units.get(metric_name, "dimensionless"),
                        developability,
                        limitations=developability["limitations"],
                    )
                await repository.record_evaluation(
                    candidate.id,
                    call.id,
                    MetricName.CONDITIONAL_PPL,
                    item["conditional_ppl"],
                    "dimensionless",
                    raw_metric,
                )
                await repository.transition_candidate(
                    candidate.id,
                    CandidateStatus.PPL_SCORED,
                    "pepmlm",
                    "conditional likelihood evidence persisted",
                )
                persisted.append(
                    {
                        "id": str(candidate.id),
                        **item,
                        "metrics": {
                            "conditional_ppl": float(item["conditional_ppl"]),
                            "conditional_nll": float(item["conditional_nll"]),
                            "instability_index": float(developability["instability_index"]),
                            "hydrophobic_fraction": float(
                                developability["hydrophobic_fraction"]
                            ),
                            "maximum_hydrophobic_run": float(
                                developability["maximum_hydrophobic_run"]
                            ),
                            "maximum_identical_residue_run": float(
                                developability["maximum_identical_residue_run"]
                            ),
                            "molecular_weight_da": float(
                                developability["molecular_weight_da"]
                            ),
                            "net_charge_ph7_4": float(
                                developability["net_charge_ph7_4"]
                            ),
                            "isoelectric_point": float(
                                developability["isoelectric_point"]
                            ),
                            "gravy": float(developability["gravy"]),
                            "hydrophobic_moment_eisenberg": float(
                                developability["hydrophobic_moment_eisenberg"]
                            ),
                            "cationic_residue_fraction": float(
                                developability["cationic_residue_fraction"]
                            ),
                        },
                    }
                )

        persisted.sort(key=lambda item: (item["conditional_ppl"], item["sequence"]))
        for rank, item in enumerate(persisted, start=1):
            candidate = await session.get(Candidate, uuid.UUID(item["id"]))
            if candidate is None:
                raise KeyError(f"candidate not found: {item['id']}")
            candidate.proposal_rank = rank
        spec = request["spec"]
        if spec.get("evaluation_ladder_mode") == "lightweight_first":
            # Optional sequence metrics are persisted after this activity. Expensive
            # representatives must therefore be chosen by the Research Director later.
            selected = []
        elif spec.get("structure_protocol") == "diagnostic_fast":
            final_generation = generation == int(spec["generations"]) - 1
            if final_generation:
                selected = cheap_diverse_selection(
                    persisted,
                    int(spec.get("final_structure_candidate_count", 8)),
                    float(spec.get("maximum_sequence_similarity", 0.75)),
                    spec.get("metric_policy"),
                )
            else:
                selected = diagnostic_representative_selection(
                    persisted,
                    int(spec.get("search_structure_comprehensive_count", 2)),
                    int(spec.get("search_structure_diversity_count", 2)),
                    float(spec.get("maximum_sequence_similarity", 0.75)),
                    spec.get("metric_policy"),
                )
        else:
            selected = cheap_diverse_selection(
                persisted,
                int(spec["structure_top_k"]),
                float(spec.get("maximum_sequence_similarity", 0.75)),
                spec.get("metric_policy"),
            )
        for item in selected:
            await repository.transition_candidate(
                uuid.UUID(item["id"]),
                CandidateStatus.STRUCTURE_QUEUED,
                "metric-role-policy-v1",
                "passed proposal qualifications and ranked within the diversity constraint",
            )
    metric_candidates = list({item["id"]: item for item in persisted}.values())
    metric_candidates.sort(key=lambda item: (item["conditional_ppl"], item["sequence"]))
    return {"structure_candidates": selected, "all_candidates": metric_candidates}


@activity.defn(name="evaluate_optional_sequence_metric")
async def evaluate_optional_sequence_metric(request: dict[str, Any]) -> dict[str, Any]:
    """Run one optional metric plugin in an isolated metric-worker subprocess."""
    settings = get_settings()
    plugin_name = request["plugin"]["name"]
    work_dir = (
        Path(settings.work_root)
        / request["run_id"]
        / "optional-metrics"
        / f"generation-{request['generation']}"
        / plugin_name
    )
    result = await _run_json_cli(
        "pepagent.model_workers.sequence_metrics_cli",
        request,
        work_dir,
        "--work-dir",
        str(work_dir / "adapter"),
        "--registry",
        settings.metric_adapter_registry_path,
    )
    environment_sha256, environment = fingerprint_runtime()
    raw_artifact = await _store_json(result)
    environment_artifact = await _store_json(environment)
    return {
        "result": result,
        "provenance": {
            "tool_name": f"handoff-metric-{plugin_name}",
            "tool_version": result.get("adapter_version") or HANDOFF_METRIC_VERSION,
            "model_uri": result.get("model_uri") or f"metric://{plugin_name}",
            "weights_sha256": result.get("weights_sha256"),
            "environment_sha256": environment_sha256,
            "environment": environment,
            "attempt": activity.info().attempt,
            "raw_output_artifact": asdict(raw_artifact),
            "environment_artifact": asdict(environment_artifact),
        },
    }


@activity.defn(name="persist_optional_sequence_metric")
async def persist_optional_sequence_metric(request: dict[str, Any]) -> dict[str, Any]:
    """Persist normalized metric observations and immutable raw adapter evidence."""
    run_id = uuid.UUID(request["run_id"])
    result = request["metric_result"]["result"]
    provenance = request["metric_result"]["provenance"]
    plugin = result["plugin"]
    limitations = [
        f"handoff reliability: {result['contract']['reliability']}",
        f"configured trust: {plugin['trust']}",
        *result.get("limitations", []),
    ]
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            provenance["tool_name"],
            provenance["tool_version"],
            provenance["environment_sha256"],
            {
                "generation": request["generation"],
                "candidate_ids": [item["id"] for item in request["candidates"]],
                **(
                    {"v37_logical_id": request["v37_logical_id"]}
                    if request.get("v37_logical_id")
                    else {}
                ),
            },
            {
                "plugin": plugin,
                "contract_reliability": result["contract"]["reliability"],
                "registry_sha256": result.get("registry_sha256"),
            },
            result,
            weights_sha256=provenance.get("weights_sha256"),
            model_uri=provenance["model_uri"],
            attempt=provenance["attempt"],
        )
        await _register_artifact(
            session,
            call.id,
            provenance["raw_output_artifact"],
            "raw_output",
            {"kind": "optional_metric_bundle", "plugin": plugin["name"]},
        )
        await _register_artifact(
            session,
            call.id,
            provenance["environment_artifact"],
            "environment_manifest",
            {"kind": "runtime_environment", "plugin": plugin["name"]},
        )
        candidates_by_id = {
            str(candidate.id): candidate
            for candidate in await session.scalars(
                select(Candidate).where(Candidate.run_id == run_id)
            )
        }
        requested_candidate_ids = {item["id"] for item in request["candidates"]}
        if requested_candidate_ids - set(candidates_by_id):
            raise KeyError("optional metric request references unknown candidates")
        generator_call_ids = {
            candidates_by_id[candidate_id].generator_call_id
            for candidate_id in requested_candidate_ids
            if candidates_by_id[candidate_id].generator_call_id is not None
        }
        if not generator_call_ids:
            raise ValueError(
                "optional metric evidence must depend on persisted candidate generation"
            )
        for generator_call_id in sorted(generator_call_ids, key=str):
            await repository.record_tool_dependency(
                call.id,
                generator_call_id,
                "evaluates_generated_candidate",
            )
        recorded = 0
        if result["status"] == "complete":
            for record in result["records"]:
                candidate = candidates_by_id.get(record["candidate_id"])
                if candidate is None:
                    raise KeyError(f"metric candidate not found: {record['candidate_id']}")
                if candidate.sequence != record["sequence"]:
                    raise ValueError(
                        f"metric sequence mismatch for candidate {record['candidate_id']}"
                    )
                record_succeeded = record.get("status") in {"complete", "ok", "success"}
                for observation in record["observations"]:
                    await repository.record_evaluation(
                        candidate.id,
                        call.id,
                        observation["metric_name"],
                        observation["numeric_value"],
                        observation["unit"],
                        {
                            "plugin": plugin,
                            "contract": result["contract"],
                            "adapter_version": result.get("adapter_version"),
                            "raw_row": record["raw"],
                        },
                        text_value=observation["text_value"],
                        out_of_domain=not record_succeeded,
                        limitations=limitations,
                    )
                    recorded += 1
                if not record_succeeded:
                    await repository.record_evaluation(
                        candidate.id,
                        call.id,
                        f"{plugin['name']}_status",
                        None,
                        None,
                        {
                            "plugin": plugin,
                            "status": record.get("status"),
                            "raw_row": record["raw"],
                            "sequence_remains_eligible": True,
                        },
                        text_value="unavailable",
                        out_of_domain=True,
                        limitations=[
                            *limitations,
                            "Candidate-level metric failure preserved sequence eligibility.",
                        ],
                    )
                    recorded += 1
        else:
            for item in request["candidates"]:
                candidate = candidates_by_id[item["id"]]
                await repository.record_evaluation(
                    candidate.id,
                    call.id,
                    f"{plugin['name']}_status",
                    None,
                    None,
                    {
                        "plugin": plugin,
                        "status": result["status"],
                        "reason": result.get("reason"),
                        "sequence_remains_eligible": True,
                    },
                    text_value="unavailable",
                    out_of_domain=True,
                    limitations=[
                        *limitations,
                        "Metric unavailable; candidate eligibility was preserved.",
                    ],
                )
                recorded += 1
        await repository.append_event(
            "run",
            run_id,
            "optional_metric.completed",
            provenance["tool_name"],
            {
                "plugin": plugin["name"],
                "generation": request["generation"],
                "status": result["status"],
                "evaluation_count": recorded,
                "tool_call_id": str(call.id),
            },
        )
    return {
        "plugin": plugin["name"],
        "status": result["status"],
        "evaluation_count": recorded,
        "tool_call_id": str(call.id),
    }


@activity.defn(name="persist_optional_metric_failure")
async def persist_optional_metric_failure(request: dict[str, Any]) -> dict[str, Any]:
    """Record a metric runtime failure without changing candidate eligibility."""
    run_id = uuid.UUID(request["run_id"])
    plugin = request["plugin"]
    environment_sha256, _ = fingerprint_runtime()
    raw = {
        "status": "unavailable",
        "plugin": plugin,
        "generation": request["generation"],
        "error_type": request["error_type"],
        "error": request["error"][:8000],
        "sequence_remains_eligible": True,
    }
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            f"handoff-metric-{plugin['name']}-failure-recorder",
            HANDOFF_METRIC_VERSION,
            environment_sha256,
            {
                "generation": request["generation"],
                "candidate_ids": [item["id"] for item in request["candidates"]],
            },
            {"failure_policy": "record_unavailable"},
            raw,
            model_uri=f"deterministic://handoff-metric-{plugin['name']}-failure-recorder",
        )
        for item in request["candidates"]:
            await repository.record_evaluation(
                uuid.UUID(item["id"]),
                call.id,
                f"{plugin['name']}_status",
                None,
                None,
                raw,
                text_value="unavailable",
                out_of_domain=True,
                limitations=[
                    "Optional metric runtime failed; candidate eligibility was preserved.",
                    f"configured trust: {plugin['trust']}",
                ],
            )
        await repository.append_event(
            "run",
            run_id,
            "optional_metric.unavailable",
            f"handoff-metric-{plugin['name']}-failure-recorder",
            {
                "plugin": plugin["name"],
                "generation": request["generation"],
                "candidate_count": len(request["candidates"]),
                "tool_call_id": str(call.id),
            },
        )
    return {"plugin": plugin["name"], "status": "unavailable"}


@activity.defn(name="predict_boltz2_complex")
async def predict_boltz2_complex(request: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    candidate = request["candidate"]
    spec = request["spec"]
    seed = int(request.get("seed", spec["seed"]))
    work_dir = (
        Path(settings.work_root) / request["run_id"] / "boltz2" / candidate["id"] / f"seed-{seed}"
    )
    payload = {
        "target_sequence": spec["target"]["sequence"],
        "peptide_sequence": candidate["sequence"],
        "pocket_residues": spec["target"].get("pocket_residues", []),
        "pocket_max_distance": spec.get("pocket_max_distance_angstrom", 8.0),
        "force_pocket": spec.get("boltz_force_pocket", False),
        "diffusion_samples": spec["diffusion_samples"],
        "recycling_steps": spec.get("boltz_recycling_steps", 3),
        "sampling_steps": spec.get("boltz_sampling_steps", 200),
        "use_potentials": spec.get("boltz_use_potentials", True),
        "no_kernels": spec.get("boltz_no_kernels", True),
        "use_msa_server": spec.get("use_msa_server", True),
        "seed": seed,
    }
    result = await _run_json_cli(
        "pepagent.model_workers.boltz2_cli",
        payload,
        work_dir,
        "--work-dir",
        str(work_dir / "engine"),
        "--cache-dir",
        settings.boltz2_cache_path,
    )
    weight_manifest = await asyncio.to_thread(_boltz_weight_manifest, settings.boltz2_cache_path)
    weights_sha256 = sha256_json(weight_manifest)
    weight_manifest_artifact = await _store_json({"files": weight_manifest})
    raw_artifact = await _store_json(result)
    engine_artifacts: list[dict[str, Any]] = []
    for relative_path in result["artifacts"]:
        path = work_dir / "engine" / relative_path
        if path.is_file():
            stored = await _store_file(path)
            engine_artifacts.append({"path": relative_path, **asdict(stored)})
    environment_sha256, environment = fingerprint_runtime()
    environment_artifact = await _store_json(environment)
    return {
        "candidate": candidate,
        "input": payload,
        "parameters": {
            "diffusion_samples": spec["diffusion_samples"],
            "recycling_steps": payload["recycling_steps"],
            "sampling_steps": payload["sampling_steps"],
            "use_potentials": payload["use_potentials"],
            "no_kernels": payload["no_kernels"],
            "pocket_residues": payload["pocket_residues"],
            "use_msa_server": payload["use_msa_server"],
            "seed": payload["seed"],
        },
        "boltz2": result,
        "provenance": {
            "tool_name": "boltz2",
            "tool_version": settings.boltz2_revision,
            "model_uri": "git+https://github.com/jwohlwend/boltz",
            "weights_sha256": weights_sha256,
            "environment_sha256": environment_sha256,
            "environment": environment,
            "attempt": activity.info().attempt,
            "raw_output_artifact": asdict(raw_artifact),
            "environment_artifact": asdict(environment_artifact),
            "weight_manifest_artifact": asdict(weight_manifest_artifact),
            "engine_artifacts": engine_artifacts,
        },
    }


@activity.defn(name="persist_structure_unavailable")
async def persist_structure_unavailable(request: dict[str, Any]) -> dict[str, Any]:
    """Preserve a structural diagnostic failure without rejecting the peptide sequence."""
    run_id = uuid.UUID(request["run_id"])
    candidate = request["candidate"]
    raw = {
        "structure_available": False,
        "structure_support": {"label": "unavailable", "reasons": [request["reason"]]},
        "error_type": request.get("error_type"),
        "error": request.get("error"),
    }
    environment_sha256, _ = fingerprint_runtime()
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            "structure-unavailable-recorder",
            "1.0.0",
            environment_sha256,
            {"candidate_id": candidate["id"]},
            {"policy": "diagnostic-fast-no-sequence-rejection"},
            raw,
            model_uri="deterministic://structure-unavailable-recorder",
        )
        await repository.record_evaluation(
            uuid.UUID(candidate["id"]),
            call.id,
            MetricName.STRUCTURE_AVAILABLE,
            0.0,
            "dimensionless",
            raw,
        )
        await repository.record_evaluation(
            uuid.UUID(candidate["id"]),
            call.id,
            MetricName.STRUCTURE_SUPPORT,
            None,
            None,
            raw,
            text_value="unavailable",
            limitations=["structural calculation unavailable; sequence remains eligible"],
        )
        await repository.transition_candidate(
            uuid.UUID(candidate["id"]),
            CandidateStatus.STRUCTURE_SCORED,
            "diagnostic-fast",
            "structure unavailable; preserved as a non-eliminating diagnostic",
        )
    return {"candidate": candidate, "audit": raw, "tool_call_id": str(call.id)}


@activity.defn(name="persist_boltz2_evidence")
async def persist_boltz2_evidence(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    result = request["structure"]
    provenance = result["provenance"]
    candidate_id = uuid.UUID(result["candidate"]["id"])
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            provenance["tool_name"],
            provenance["tool_version"],
            provenance["environment_sha256"],
            result["input"],
            result["parameters"],
            result["boltz2"],
            weights_sha256=provenance["weights_sha256"],
            model_uri=provenance["model_uri"],
            random_seed=result["input"]["seed"],
            attempt=provenance["attempt"],
        )
        await _register_artifact(
            session,
            call.id,
            provenance["raw_output_artifact"],
            "raw_output",
            {"tool": "boltz2", "candidate_id": str(candidate_id)},
        )
        await _register_artifact(
            session,
            call.id,
            provenance["environment_artifact"],
            "environment_manifest",
            {"tool": "boltz2", "kind": "runtime_environment"},
        )
        await _register_artifact(
            session,
            call.id,
            provenance["weight_manifest_artifact"],
            "weight_manifest",
            {"tool": "boltz2", "weights_sha256": provenance["weights_sha256"]},
        )
        for index, artifact_payload in enumerate(provenance["engine_artifacts"]):
            await _register_artifact(
                session,
                call.id,
                artifact_payload,
                f"engine_output_{index}",
                {
                    "tool": "boltz2",
                    "candidate_id": str(candidate_id),
                    "relative_path": artifact_payload["path"],
                },
            )
        metrics = {
            MetricName.BOLTZ2_CONFIDENCE: result["boltz2"].get("confidence_score"),
            MetricName.BOLTZ2_IPTM: result["boltz2"].get("iptm"),
            MetricName.BOLTZ2_PAIR_IPTM: result["boltz2"].get("pair_iptm"),
            MetricName.BOLTZ2_COMPLEX_IPLDDT: result["boltz2"].get("complex_iplddt"),
        }
        for metric_name, value in metrics.items():
            if value is not None:
                await repository.record_evaluation(
                    candidate_id,
                    call.id,
                    metric_name,
                    float(value),
                    "dimensionless",
                    result["boltz2"].get("raw_confidence", {}),
                )
        await repository.transition_candidate(
            candidate_id,
            CandidateStatus.STRUCTURE_SCORED,
            "boltz2",
            "complex-confidence evidence persisted",
        )
        result["tool_call_id"] = str(call.id)
    return result


@activity.defn(name="select_rosetta_inputs")
async def select_rosetta_inputs(request: dict[str, Any]) -> list[dict[str, Any]]:
    if "ensembles" in request:
        ensembles = request["ensembles"]
        top_k = int(request["top_k"])
        diagnostic_shadow = request.get("mode") == "diagnostic_shadow"
        admitted = (
            [
                item
                for item in ensembles
                if item["audit"].get("structure_available")
                and item["audit"].get("structure_support", {}).get("label") != "unavailable"
            ]
            if diagnostic_shadow
            else [item for item in ensembles if item["audit"]["gate_pass"]]
        )
        admitted.sort(
            key=lambda item: (
                item["audit"].get("pocket_contact_consistency") or 0.0,
                item["audit"]["pair_iptm_median"],
            ),
            reverse=True,
        )
        chosen = admitted[:top_k]
        selected_ids = {item["candidate"]["id"] for item in chosen}
        exploratory_slots = int(request.get("exploratory_slots", 0))
        if exploratory_slots and len(chosen) < top_k + exploratory_slots:
            exploratory = sorted(
                (item for item in ensembles if item["candidate"]["id"] not in selected_ids),
                key=lambda item: (
                    item["audit"]["pocket_contact_consistency"],
                    item["audit"]["pair_iptm_median"],
                ),
                reverse=True,
            )[:exploratory_slots]
            for item in exploratory:
                item["rosetta_selection_mode"] = "exploratory_gate_failure"
            chosen.extend(exploratory)
        async with SessionFactory() as session, session.begin():
            repository = ExperimentRepository(session)
            for item in chosen:
                mode = (
                    "diagnostic_shadow_tiebreak"
                    if diagnostic_shadow
                    else item.get("rosetta_selection_mode", "admitted_structure_gate")
                )
                await repository.transition_candidate(
                    uuid.UUID(item["candidate"]["id"]),
                    CandidateStatus.ROSETTA_QUEUED,
                    "selection-policy",
                    f"selected for FlexPepDock: {mode}",
                )
        return [
            {
                **item["representative"],
                "interface_audit": item["audit"],
                "interface_audit_tool_call_id": item["tool_call_id"],
                "rosetta_selection_mode": (
                    "diagnostic_shadow_tiebreak"
                    if diagnostic_shadow
                    else item.get("rosetta_selection_mode", "admitted_structure_gate")
                ),
            }
            for item in chosen
        ]
    threshold = float(request["pair_iptm_min"])
    top_k = int(request["top_k"])
    eligible = [
        structure
        for structure in request["structures"]
        if structure["boltz2"].get("pair_iptm") is not None
        and float(structure["boltz2"]["pair_iptm"]) >= threshold
    ]
    selected = sorted(
        eligible,
        key=lambda item: float(item["boltz2"]["pair_iptm"]),
        reverse=True,
    )[:top_k]
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        for structure in selected:
            await repository.transition_candidate(
                uuid.UUID(structure["candidate"]["id"]),
                CandidateStatus.ROSETTA_QUEUED,
                "selection-policy",
                f"pair-ipTM >= {threshold} and within Rosetta top-{top_k}",
            )
    return selected


def _select_boltz_structure_artifact(structure: dict[str, Any]) -> dict[str, Any]:
    artifacts = structure["provenance"]["engine_artifacts"]
    candidates = [
        artifact
        for artifact in artifacts
        if Path(artifact["path"]).suffix.lower() in {".cif", ".pdb"}
        and "model_0" in Path(artifact["path"]).name
    ]
    if not candidates:
        candidates = [
            artifact
            for artifact in artifacts
            if Path(artifact["path"]).suffix.lower() in {".cif", ".pdb"}
        ]
    if not candidates:
        raise FileNotFoundError("Boltz evidence contains no complex coordinate artifact")
    return sorted(candidates, key=lambda item: item["path"])[0]


def _convert_structure_to_pdb(source: Path, destination: Path) -> None:
    if source.suffix.lower() == ".pdb":
        destination.write_bytes(source.read_bytes())
        return
    import gemmi

    structure = gemmi.read_structure(str(source))
    structure.write_pdb(str(destination))


@activity.defn(name="audit_structure_ensemble")
async def audit_structure_ensemble(request: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    spec = request["spec"]
    structures = request["structures"]
    candidate = structures[0]["candidate"]
    work_dir = Path(settings.work_root) / request["run_id"] / "interface-audit" / candidate["id"]
    await asyncio.to_thread(work_dir.mkdir, parents=True, exist_ok=True)
    pose_paths: list[Path] = []
    sample_audits: list[dict[str, Any]] = []
    for index, structure in enumerate(structures):
        coordinate_artifact = _select_boltz_structure_artifact(structure)
        coordinate_bytes = await asyncio.to_thread(
            ContentAddressedObjectStore().get_bytes, coordinate_artifact["uri"]
        )
        suffix = Path(coordinate_artifact["path"]).suffix.lower()
        source = work_dir / f"sample-{index}{suffix}"
        destination = work_dir / f"sample-{index}.pdb"
        await asyncio.to_thread(source.write_bytes, coordinate_bytes)
        await asyncio.to_thread(_convert_structure_to_pdb, source, destination)
        pose_paths.append(destination)
        coordinate_audit = await asyncio.to_thread(
            audit_protein_peptide_interface,
            destination,
            spec["target"].get("pocket_residues", []),
            float(spec.get("interface_contact_distance_angstrom", 5.0)),
            float(spec.get("interface_clash_distance_angstrom", 1.5)),
        )
        sample_audits.append(
            {
                "seed": structure["input"]["seed"],
                "tool_call_id": structure["tool_call_id"],
                "coordinate_artifact": coordinate_artifact,
                "pair_iptm": structure["boltz2"].get("pair_iptm"),
                **coordinate_audit,
            }
        )
    pose_consistency = (
        await asyncio.to_thread(
            pose_cluster_fraction,
            pose_paths,
            float(spec.get("interface_pose_cluster_rmsd_angstrom", 4.0)),
        )
        if len(pose_paths) > 1
        else None
    )
    required_contacts = int(spec.get("interface_min_pocket_contacts", 1))
    contact_consistency = (
        sum(audit["pocket_contact_count"] >= required_contacts for audit in sample_audits)
        / len(sample_audits)
        if len(sample_audits) > 1
        else None
    )
    pair_values = [
        float(audit["pair_iptm"]) for audit in sample_audits if audit["pair_iptm"] is not None
    ]
    pair_median = median(pair_values) if pair_values else 0.0
    gate_checks = {
        "pocket_contact_consistency": contact_consistency is not None
        and contact_consistency >= float(spec.get("interface_min_seed_consistency", 0.5)),
        "pair_iptm_median": pair_median >= float(spec.get("interface_min_pair_iptm_median", 0.2)),
        "pose_cluster_fraction": pose_consistency is not None
        and pose_consistency["largest_cluster_fraction"]
        >= float(spec.get("interface_min_pose_cluster_fraction", 0.5)),
        "no_cross_chain_clash": all(
            audit["cross_chain_clash_count"] == 0 for audit in sample_audits
        ),
    }
    representative_index = max(
        range(len(sample_audits)),
        key=lambda index: (
            sample_audits[index]["pocket_contact_count"],
            sample_audits[index]["pair_iptm"] or 0.0,
            -sample_audits[index]["cross_chain_clash_count"],
        ),
    )
    representative = sample_audits[representative_index]
    support = classify_structure_support(
        structure_available=True,
        pair_iptm=representative["pair_iptm"],
        pocket_contact_count=representative["pocket_contact_count"],
        clash_count=representative["cross_chain_clash_count"],
        severe_clash_count=int(spec.get("severe_structure_clash_count", 25)),
        minimum_pair_iptm=float(spec.get("interface_min_pair_iptm_median", 0.2)),
        minimum_pocket_contacts=required_contacts,
    )
    diagnostic_fast = spec.get("structure_protocol") == "diagnostic_fast"
    support = reconcile_ensemble_structure_support(support, gate_checks)
    result = {
        "schema_version": "1.0",
        "candidate_id": candidate["id"],
        "generation": request["generation"],
        "sample_count": len(sample_audits),
        "sample_audits": sample_audits,
        "pocket_contact_consistency": contact_consistency,
        "pair_iptm_median": pair_median,
        "pose_consistency": pose_consistency,
        "structure_available": True,
        "structure_support": support,
        "gate_checks": gate_checks,
        "gate_pass": None if diagnostic_fast else all(gate_checks.values()),
        "gate_policy": "diagnostic_only" if diagnostic_fast else "legacy_hard_gate",
        "representative_index": representative_index,
    }
    raw_artifact = await _store_json(result)
    environment_sha256, environment = fingerprint_runtime()
    environment_artifact = await _store_json(environment)
    return {
        "candidate": candidate,
        "structures": structures,
        "representative": structures[representative_index],
        "audit": result,
        "input": {
            "candidate_id": candidate["id"],
            "structure_tool_call_ids": [item["tool_call_id"] for item in structures],
            "pocket_residues": spec["target"].get("pocket_residues", []),
        },
        "parameters": {
            key: spec.get(key)
            for key in (
                "interface_contact_distance_angstrom",
                "interface_clash_distance_angstrom",
                "interface_min_pocket_contacts",
                "interface_min_seed_consistency",
                "interface_min_pair_iptm_median",
                "interface_pose_cluster_rmsd_angstrom",
                "interface_min_pose_cluster_fraction",
            )
        },
        "provenance": {
            "tool_name": "coordinate-interface-audit",
            "tool_version": "1.0.0",
            "environment_sha256": environment_sha256,
            "attempt": activity.info().attempt,
            "parent_tool_call_ids": [item["tool_call_id"] for item in structures],
            "raw_output_artifact": asdict(raw_artifact),
            "environment_artifact": asdict(environment_artifact),
        },
    }


@activity.defn(name="persist_interface_audit")
async def persist_interface_audit(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    result = request["audit_result"]
    provenance = result["provenance"]
    candidate_id = uuid.UUID(result["candidate"]["id"])
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            provenance["tool_name"],
            provenance["tool_version"],
            provenance["environment_sha256"],
            result["input"],
            result["parameters"],
            result["audit"],
            attempt=provenance["attempt"],
        )
        for parent in provenance["parent_tool_call_ids"]:
            await repository.record_tool_dependency(call.id, uuid.UUID(parent), "audits")
        await _register_artifact(
            session,
            call.id,
            provenance["raw_output_artifact"],
            "raw_output",
            {"tool": "coordinate-interface-audit", "candidate_id": str(candidate_id)},
        )
        await _register_artifact(
            session,
            call.id,
            provenance["environment_artifact"],
            "environment_manifest",
            {"tool": "coordinate-interface-audit"},
        )
        audit = result["audit"]
        representative = audit["sample_audits"][audit["representative_index"]]
        metrics = {
            MetricName.BOLTZ2_PAIR_IPTM_MEDIAN: audit["pair_iptm_median"],
            MetricName.STRUCTURE_AVAILABLE: int(audit["structure_available"]),
            MetricName.POCKET_CONTACT_COUNT: representative["pocket_contact_count"],
            MetricName.POCKET_CONTACT_CONSISTENCY: audit["pocket_contact_consistency"],
            MetricName.POCKET_COVERAGE_FRACTION: representative["pocket_coverage_fraction"],
            MetricName.OFF_POCKET_CONTACT_FRACTION: representative["off_pocket_contact_fraction"],
            MetricName.INTERFACE_MIN_DISTANCE_ANGSTROM: representative[
                "minimum_interface_distance_angstrom"
            ],
            MetricName.INTERFACE_CLASH_COUNT: representative["cross_chain_clash_count"],
            MetricName.POSE_CLUSTER_FRACTION: (
                audit["pose_consistency"]["largest_cluster_fraction"]
                if audit["pose_consistency"] is not None
                else None
            ),
            MetricName.INTERFACE_GATE_PASS: (
                int(audit["gate_pass"]) if audit["gate_pass"] is not None else None
            ),
        }
        for metric_name, value in metrics.items():
            if value is None:
                continue
            unit = (
                "angstrom"
                if metric_name == MetricName.INTERFACE_MIN_DISTANCE_ANGSTROM
                else "count"
                if metric_name
                in {MetricName.POCKET_CONTACT_COUNT, MetricName.INTERFACE_CLASH_COUNT}
                else "dimensionless"
            )
            await repository.record_evaluation(
                candidate_id, call.id, metric_name, float(value), unit, audit
            )
        await repository.record_evaluation(
            candidate_id,
            call.id,
            MetricName.STRUCTURE_SUPPORT,
            None,
            None,
            audit,
            text_value=audit["structure_support"]["label"],
            limitations=(
                ["single Boltz pose; pose and contact consistency are not estimable"]
                if audit["sample_count"] == 1
                else []
            ),
        )
        result["tool_call_id"] = str(call.id)
    return result


@activity.defn(name="score_rosetta_complex")
async def score_rosetta_complex(request: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    structure = request["structure"]
    candidate = structure["candidate"]
    spec = request["spec"]
    validation_case = request.get("validation_case")
    lane = "rosetta-validation" if validation_case else "rosetta"
    work_dir = (
        Path(settings.work_root)
        / request["run_id"]
        / lane
        / candidate["id"]
        / str(request["seed"])
    )
    coordinate_artifact = _select_boltz_structure_artifact(structure)
    coordinate_bytes = await asyncio.to_thread(
        ContentAddressedObjectStore().get_bytes, coordinate_artifact["uri"]
    )
    source_path = work_dir / f"boltz-input{Path(coordinate_artifact['path']).suffix.lower()}"
    await asyncio.to_thread(work_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(source_path.write_bytes, coordinate_bytes)
    input_pdb = work_dir / "boltz-input.pdb"
    await asyncio.to_thread(_convert_structure_to_pdb, source_path, input_pdb)

    receptor_chains = list(validation_case["receptor_chains"]) if validation_case else ["A"]
    peptide_chain = validation_case["peptide_chain"] if validation_case else "B"
    payload = {
        "receptor_chains": receptor_chains,
        "peptide_chain": peptide_chain,
        "nstruct": int(spec.get("rosetta_nstruct", 200)),
        "parallel_decoys": int(spec.get("rosetta_parallel_decoys", 8 if validation_case else 1)),
        "seed": int(request.get("seed", spec["seed"])),
        "score_function": spec.get("rosetta_score_function", "ref2015"),
        "source_structure_sha256": coordinate_artifact["sha256"],
        "source_tool_call_id": structure["tool_call_id"],
    }
    if validation_case:
        payload["native_structure"] = str(input_pdb)
        payload["validation_case"] = validation_case
    engine_dir = work_dir / "engine"
    result = await _run_json_cli(
        "pepagent.model_workers.rosetta_cli",
        payload,
        work_dir,
        "--work-dir",
        str(engine_dir),
        "--input-structure",
        str(input_pdb),
    )
    if spec.get("structure_protocol") == "diagnostic_fast":
        result.setdefault("limitations", []).append(
            "Predicted-pose decoys are a same-protocol local-energy diagnostic, not "
            "decision-grade affinity or experimental uncertainty estimation."
        )
    raw_artifact = await _store_json(result)
    engine_artifacts: list[dict[str, Any]] = []
    for relative_path in result["artifacts"]:
        path = engine_dir / relative_path
        if path.is_file():
            stored = await _store_file(path)
            engine_artifacts.append({"path": relative_path, **asdict(stored)})
    environment_sha256, environment = fingerprint_runtime()
    environment_artifact = await _store_json(environment)
    return {
        "candidate": candidate,
        "interface_audit": structure.get("interface_audit"),
        "input": payload,
        "parameters": {
            "score_function": payload["score_function"],
            "nstruct": payload["nstruct"],
            "parallel_decoys": payload["parallel_decoys"],
            "prepack": True,
            "pack_input": False,
            "pack_separated": False,
            "primary_aggregation": result["primary_aggregation"],
            "validation_case": validation_case,
            "evidence_grade": (
                "shadow_diagnostic"
                if spec.get("structure_protocol") == "diagnostic_fast"
                else "formal_relative_reranking"
            ),
            "support_thresholds": {
                "severe_structure_clash_count": int(
                    spec.get("severe_structure_clash_count", 25)
                ),
                "interface_min_pair_iptm_median": float(
                    spec.get("interface_min_pair_iptm_median", 0.2)
                ),
                "interface_min_pocket_contacts": int(
                    spec.get("interface_min_pocket_contacts", 1)
                ),
            },
        },
        "rosetta": result,
        "provenance": {
            "tool_name": "pyrosetta-flexpepdock-interface-analyzer",
            "tool_version": importlib.metadata.version("pyrosetta"),
            "model_uri": (
                "https://west.rosettacommons.org/pyrosetta/quarterly/release/"
                "pyrosetta-2026.29%2Breleasequarterly.80a0635615-"
                "cp311-cp311-linux_x86_64.whl"
            ),
            "weights_sha256": settings.pyrosetta_wheel_sha256,
            "environment_sha256": environment_sha256,
            "environment": environment,
            "attempt": activity.info().attempt,
            "parent_tool_call_id": structure["tool_call_id"],
            "interface_audit_tool_call_id": structure.get("interface_audit_tool_call_id"),
            "source_coordinate_artifact": coordinate_artifact,
            "raw_output_artifact": asdict(raw_artifact),
            "environment_artifact": asdict(environment_artifact),
            "engine_artifacts": engine_artifacts,
        },
    }


@activity.defn(name="persist_rosetta_evidence")
async def persist_rosetta_evidence(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    result = request["rosetta_result"]
    provenance = result["provenance"]
    candidate_id = uuid.UUID(result["candidate"]["id"])
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            provenance["tool_name"],
            provenance["tool_version"],
            provenance["environment_sha256"],
            result["input"],
            result["parameters"],
            result["rosetta"],
            weights_sha256=provenance["weights_sha256"],
            model_uri=provenance["model_uri"],
            random_seed=result["input"]["seed"],
            attempt=provenance["attempt"],
        )
        await repository.record_tool_dependency(
            call.id,
            uuid.UUID(provenance["parent_tool_call_id"]),
            "refines",
        )
        if provenance.get("interface_audit_tool_call_id"):
            await repository.record_tool_dependency(
                call.id,
                uuid.UUID(provenance["interface_audit_tool_call_id"]),
                "authorized_by_interface_audit",
            )
        await _register_artifact(
            session,
            call.id,
            provenance["raw_output_artifact"],
            "raw_output",
            {"tool": "rosetta", "candidate_id": str(candidate_id)},
        )
        await _register_artifact(
            session,
            call.id,
            provenance["environment_artifact"],
            "environment_manifest",
            {"tool": "rosetta", "kind": "runtime_environment"},
        )
        for index, artifact_payload in enumerate(provenance["engine_artifacts"]):
            await _register_artifact(
                session,
                call.id,
                artifact_payload,
                f"engine_output_{index}",
                {
                    "tool": "rosetta",
                    "candidate_id": str(candidate_id),
                    "relative_path": artifact_payload["path"],
                },
            )

        rosetta = result["rosetta"]
        metrics = {
            MetricName.ROSETTA_DG_SEPARATED_REU: rosetta["primary_dG_separated_reu"],
            MetricName.ROSETTA_DG_MINIMUM_REU: rosetta["dG_separated_reu"]["minimum"],
            MetricName.ROSETTA_PEPTIDE_BB_RMSD_ANGSTROM: (
                rosetta["peptide_bb_rmsd_angstrom"]["median"]
            ),
            MetricName.ROSETTA_INTERFACE_SCORE: rosetta["best_decoy"].get("interface_score"),
            MetricName.ROSETTA_REWEIGHTED_SCORE: rosetta["best_decoy"].get("reweighted_sc"),
            MetricName.ROSETTA_INTERFACE_HBONDS: rosetta["best_decoy"].get("interface_hbonds"),
            MetricName.ROSETTA_BURIED_SURFACE_AREA: rosetta["best_decoy"].get("dSASA_int"),
        }
        for metric_name, value in metrics.items():
            if value is None:
                continue
            unit = (
                "angstrom"
                if metric_name == MetricName.ROSETTA_PEPTIDE_BB_RMSD_ANGSTROM
                else (
                    "angstrom^2"
                    if metric_name == MetricName.ROSETTA_BURIED_SURFACE_AREA
                    else ("count" if metric_name == MetricName.ROSETTA_INTERFACE_HBONDS else "REU")
                )
            )
            await repository.record_evaluation(
                candidate_id,
                call.id,
                metric_name,
                float(value),
                unit,
                rosetta,
                limitations=rosetta["limitations"],
            )
        if result.get("interface_audit") is not None:
            interface_audit = result["interface_audit"]
            thresholds = result["parameters"]["support_thresholds"]
            representative = interface_audit["sample_audits"][
                interface_audit["representative_index"]
            ]
            support = classify_structure_support(
                structure_available=bool(interface_audit.get("structure_available", True)),
                pair_iptm=representative.get("pair_iptm"),
                pocket_contact_count=representative.get("pocket_contact_count"),
                clash_count=representative.get("cross_chain_clash_count"),
                severe_clash_count=thresholds["severe_structure_clash_count"],
                minimum_pair_iptm=thresholds["interface_min_pair_iptm_median"],
                minimum_pocket_contacts=thresholds["interface_min_pocket_contacts"],
                rosetta_dg=rosetta["primary_dG_separated_reu"],
            )
            support = reconcile_ensemble_structure_support(
                support,
                interface_audit.get("gate_checks", {}),
            )
            await repository.record_evaluation(
                candidate_id,
                call.id,
                MetricName.STRUCTURE_SUPPORT,
                None,
                None,
                {"structure_support": support, "interface_audit": interface_audit},
                text_value=support["label"],
                limitations=rosetta["limitations"],
            )
        await repository.transition_candidate(
            candidate_id,
            CandidateStatus.ROSETTA_SCORED,
            "rosetta",
            "FlexPepDock refinement and InterfaceAnalyzer dG evidence persisted",
        )
        result["tool_call_id"] = str(call.id)
    return result


@activity.defn(name="select_next_generation")
async def select_next_generation(request: dict[str, Any]) -> dict[str, Any]:
    """Run the versioned diversity-constrained elitist Research Director policy."""
    run_id = uuid.UUID(request["run_id"])
    generation = int(request["generation"])
    spec = request["spec"]
    final_generation = bool(request.get("final_generation", False))
    selection_stage = "final" if final_generation else "research"
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == run_id, Candidate.generation == generation)
                .order_by(Candidate.proposal_rank, Candidate.id)
            )
        )
        candidate_ids = [candidate.id for candidate in candidates]
        evaluations = list(
            await session.scalars(
                select(Evaluation).where(Evaluation.candidate_id.in_(candidate_ids))
            )
        )
        metrics_by_candidate: dict[uuid.UUID, dict[str, float]] = {
            candidate.id: {} for candidate in candidates
        }
        evidence_calls: set[uuid.UUID] = set()
        metric_evidence: dict[uuid.UUID, dict[str, list[dict[str, Any]]]] = {
            candidate.id: {} for candidate in candidates
        }
        tool_call_ids = {evaluation.tool_call_id for evaluation in evaluations}
        tool_calls = {
            tool_call.id: tool_call
            for tool_call in await session.scalars(
                select(ToolCall).where(ToolCall.id.in_(tool_call_ids))
            )
        }
        for evaluation in evaluations:
            if evaluation.numeric_value is not None:
                metrics_by_candidate[evaluation.candidate_id][evaluation.metric_name] = float(
                    evaluation.numeric_value
                )
            evidence_calls.add(evaluation.tool_call_id)
            tool_call = tool_calls[evaluation.tool_call_id]
            if not tool_call.output_sha256:
                raise RuntimeError(
                    "Agent evidence must have an explicit output SHA-256: "
                    f"tool_call={tool_call.id} metric={evaluation.metric_name}"
                )
            atomic_evidence = {
                "candidate_id": str(evaluation.candidate_id),
                "metric_name": evaluation.metric_name,
                "numeric_value": evaluation.numeric_value,
                "text_value": evaluation.text_value,
                "unit": evaluation.unit,
                "status": evaluation.status,
                "out_of_domain": evaluation.out_of_domain,
                "limitations": evaluation.limitations_json,
                "source_sha256": tool_call.output_sha256,
                "tool_call_id": str(tool_call.id),
            }
            metric_evidence[evaluation.candidate_id].setdefault(
                evaluation.metric_name, []
            ).append(
                {
                    "source_type": "tool_call_output",
                    "source_sha256": tool_call.output_sha256,
                    "evidence_sha256": canonical_sha256(atomic_evidence),
                    "tool_call_id": str(tool_call.id),
                    "tool_name": tool_call.tool_name,
                    "tool_version": tool_call.tool_version,
                    "weights_sha256": tool_call.weights_sha256,
                }
            )
        observed = [
            {
                "id": str(candidate.id),
                "sequence": candidate.sequence,
                "sequence_sha256": candidate.sequence_sha256,
                "generation": candidate.generation,
                "status": candidate.status,
                "metrics": metrics_by_candidate[candidate.id],
            }
            for candidate in candidates
        ]
        elites = diversity_constrained_elites(
            observed,
            int(spec.get("elite_parent_count", 3)),
            float(spec.get("maximum_sequence_similarity", 0.75)),
            spec.get("metric_policy"),
            selection_stage,
        )
        if not elites and not final_generation:
            raise RuntimeError("Research Director could not select any next-generation parent")
        qualified_elite_count = sum(
            not qualification_violations(item, spec.get("metric_policy"), selection_stage)
            for item in elites
        )
        evaluation_plan = progressive_evaluation_plan(
            spec,
            generation,
            final_generation,
            qualified_elite_count,
        )
        iteration_directive = research_iteration_directive(
            spec,
            final_generation=final_generation,
            selected_count=len(elites),
            qualified_elite_count=qualified_elite_count,
        )
        selected_ids = {uuid.UUID(item["id"]) for item in elites}
        for item in elites:
            candidate_id = uuid.UUID(item["id"])
            item["mutation_brief"] = build_mutation_brief(
                item,
                metric_evidence[candidate_id],
                spec.get("mutation_knowledge_cards"),
            )
        for candidate in candidates:
            if candidate.id in selected_ids:
                await repository.transition_candidate(
                    candidate.id,
                    CandidateStatus.SELECTED,
                    "research-director-policy-v2",
                    "retained as a diversity-constrained elite parent",
                )
            elif candidate.status not in {
                CandidateStatus.ROSETTA_QUEUED,
                CandidateStatus.FAILED,
            }:
                await repository.transition_candidate(
                    candidate.id,
                    CandidateStatus.REJECTED,
                    "research-director-policy-v2",
                    "not retained in the generation elite archive",
                )
        qualification_by_candidate = {
            item["id"]: qualification_violations(
                item, spec.get("metric_policy"), selection_stage
            )
            for item in observed
        }
        policy_snapshot = {
            "policy": "metric-role-policy-v1",
            "stage": selection_stage,
            "maximum_sequence_similarity": spec.get("maximum_sequence_similarity", 0.75),
            "elite_parent_count": spec.get("elite_parent_count", 3),
            "metric_policy": spec.get("metric_policy", []),
        }
        candidate_assessments = [
            {
                "candidate_id": item["id"],
                "sequence_sha256": item["sequence_sha256"],
                "outcome": "selected" if uuid.UUID(item["id"]) in selected_ids else "rejected",
                "qualification_violations": qualification_by_candidate[item["id"]],
                "metrics": item["metrics"],
                "metric_evidence": metric_evidence[uuid.UUID(item["id"])],
            }
            for item in observed
        ]
        prompt_payload = {
            **policy_snapshot,
            "policy_sha256": canonical_sha256(policy_snapshot),
            "generation": generation,
            "legacy_staged_order": [
                "interface_gate_pass",
                "favorable_rosetta_dg_after_gate",
                "pocket_contact_consistency",
                "boltz2_pair_iptm_median",
                "conditional_ppl",
            ],
            "qualification_violations": qualification_by_candidate,
            "candidates": observed,
            "candidate_assessments": candidate_assessments,
            "mutation_briefs": [item["mutation_brief"] for item in elites],
            "evaluation_plan": evaluation_plan,
            "iteration_directive": iteration_directive,
        }
        prompt_text = json.dumps(prompt_payload, ensure_ascii=False, indent=2, sort_keys=True)
        structured = {
            "schema_version": "1.0",
            "decision_type": (
                "select_final_qualified_candidates"
                if final_generation
                else "select_next_generation_parents"
            ),
            "generation": generation,
            "selected_parent_ids": [item["id"] for item in elites],
            "rejected_candidate_ids": [
                str(candidate.id) for candidate in candidates if candidate.id not in selected_ids
            ],
            "selection_policy": "metric-role-policy-v1",
            "next_action": iteration_directive["next_action"],
            "mutation_briefs": [item["mutation_brief"] for item in elites],
            "policy_sha256": canonical_sha256(policy_snapshot),
            "candidate_assessments": candidate_assessments,
            "evaluation_plan": evaluation_plan,
            "iteration_directive": iteration_directive,
        }
        response_lines = [
            f"Generation {generation}: selected {len(elites)} "
            f"{'qualified final candidates' if final_generation else 'parent candidates'} "
            "under the diversity constraint.",
            "Qualification rules were applied before objectives; failed hard constraints "
            "could not be compensated by a stronger objective.",
            "EVALUATION_LADDER "
            + json.dumps(evaluation_plan, ensure_ascii=False, sort_keys=True),
            "ITERATION_DIRECTIVE "
            + json.dumps(iteration_directive, ensure_ascii=False, sort_keys=True),
        ]
        response_lines.extend(
            f"SELECT {item['id']} {item['sequence']} metrics="
            f"{json.dumps(item['metrics'], ensure_ascii=False, sort_keys=True)} "
            f"mutation_brief_sha256={item['mutation_brief']['brief_sha256']} "
            f"evidence_sha256s={','.join(evidence_hashes(item['mutation_brief']))}"
            for item in elites
        )
        response_lines.extend(
            f"AUDIT {item['candidate_id']} outcome={item['outcome']} "
            f"sequence_sha256={item['sequence_sha256']} "
            f"qualification_violations="
            f"{json.dumps(item['qualification_violations'], ensure_ascii=False, sort_keys=True)} "
            "evidence_sha256s="
            + ",".join(
                sorted(
                    {
                        ref["evidence_sha256"]
                        for refs in item["metric_evidence"].values()
                        for ref in refs
                    }
                )
            )
            for item in candidate_assessments
        )
        response_text = "\n".join(response_lines)
        prompt_stored = await _store_text(prompt_text)
        response_stored = await _store_text(response_text)
        prompt_artifact = await _register_stored_artifact(
            session,
            asdict(prompt_stored),
            {"kind": "agent_prompt_original", "generation": generation},
        )
        response_artifact = await _register_stored_artifact(
            session,
            asdict(response_stored),
            {"kind": "agent_response_original", "generation": generation},
        )
        decision = await repository.record_agent_decision(
            run_id,
            generation,
            (
                "select_final_qualified_candidates"
                if final_generation
                else "select_next_generation_parents"
            ),
            "research-director-policy",
            "metric-role-policy-v1",
            prompt_text,
            response_text,
            structured,
            prompt_artifact_id=prompt_artifact.id,
            response_artifact_id=response_artifact.id,
        )
        for tool_call_id in sorted(evidence_calls, key=str):
            await repository.record_agent_tool_edge(decision.id, tool_call_id, "input", "observes")
        await repository.append_event(
            "run",
            run_id,
            "generation.completed",
            "research-director-policy-v1",
            {
                "generation": generation,
                "candidate_count": len(candidates),
                "selected_parent_ids": [item["id"] for item in elites],
                "decision_id": str(decision.id),
            },
        )
        return {
            "decision_id": str(decision.id),
            "parents": elites,
            "generation": generation,
            "evaluation_plan": evaluation_plan,
        }


@activity.defn(name="select_bulk_evaluation_candidates")
async def select_bulk_evaluation_candidates(request: dict[str, Any]) -> dict[str, Any]:
    """Select a qualification-first, diversity-constrained post-search cohort."""
    run_id = uuid.UUID(request["run_id"])
    spec = request["spec"]
    candidate_limit = int(spec.get("bulk_rosetta_candidate_limit", 250))
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == run_id)
                .order_by(Candidate.generation, Candidate.proposal_rank, Candidate.id)
            )
        )
        candidate_ids = [candidate.id for candidate in candidates]
        evaluations = list(
            await session.scalars(
                select(Evaluation)
                .where(Evaluation.candidate_id.in_(candidate_ids))
                .order_by(Evaluation.created_at, Evaluation.id)
            )
        )
        metrics_by_candidate: dict[uuid.UUID, dict[str, float]] = {
            candidate.id: {} for candidate in candidates
        }
        evidence_calls: set[uuid.UUID] = set()
        for evaluation in evaluations:
            if evaluation.numeric_value is not None:
                metrics_by_candidate[evaluation.candidate_id][evaluation.metric_name] = float(
                    evaluation.numeric_value
                )
            evidence_calls.add(evaluation.tool_call_id)
        observed = [
            {
                "id": str(candidate.id),
                "sequence": candidate.sequence,
                "sequence_sha256": candidate.sequence_sha256,
                "generation": candidate.generation,
                "metrics": metrics_by_candidate[candidate.id],
                "conditional_ppl": metrics_by_candidate[candidate.id].get(
                    MetricName.CONDITIONAL_PPL, float("inf")
                ),
            }
            for candidate in candidates
        ]
        selected = cheap_diverse_selection(
            observed,
            candidate_limit,
            float(spec.get("maximum_sequence_similarity", 0.75)),
            spec.get("metric_policy"),
        )
        for rank, item in enumerate(selected, start=1):
            candidate = await session.get(Candidate, uuid.UUID(item["id"]))
            if candidate is None:
                raise KeyError(f"candidate not found: {item['id']}")
            candidate.metadata_json = {
                **candidate.metadata_json,
                "bulk_rosetta": {
                    "selected": True,
                    "rank": rank,
                    "candidate_limit": candidate_limit,
                    "protocol": "single-seed-boltz-plus-eight-decoy-rosetta-v1",
                },
            }
            await repository.transition_candidate(
                candidate.id,
                CandidateStatus.STRUCTURE_QUEUED,
                "bulk-rosetta-selection-v1",
                "selected after non-compensatory qualifications and diversity filtering",
            )
        prompt_payload = {
            "policy": "bulk-rosetta-selection-v1",
            "candidate_limit": candidate_limit,
            "maximum_sequence_similarity": spec.get("maximum_sequence_similarity", 0.75),
            "metric_policy": spec.get("metric_policy", []),
            "candidate_count": len(observed),
            "selected_candidate_ids": [item["id"] for item in selected],
        }
        prompt_text = json.dumps(prompt_payload, ensure_ascii=False, indent=2, sort_keys=True)
        response_text = (
            f"Selected {len(selected)} naturally produced qualified candidates from "
            f"{len(observed)} observed candidates (safety cap {candidate_limit}); no quota filling."
        )
        prompt_stored = await _store_text(prompt_text)
        response_stored = await _store_text(response_text)
        prompt_artifact = await _register_stored_artifact(
            session, asdict(prompt_stored), {"kind": "bulk_rosetta_selection_prompt"}
        )
        response_artifact = await _register_stored_artifact(
            session, asdict(response_stored), {"kind": "bulk_rosetta_selection_response"}
        )
        decision = await repository.record_agent_decision(
            run_id,
            int(spec["generations"]),
            "select_bulk_rosetta_cohort",
            "bulk-rosetta-selection-policy",
            "bulk-rosetta-selection-v1",
            prompt_text,
            response_text,
            {
                "candidate_limit": candidate_limit,
                "selected_count": len(selected),
                "selected_candidate_ids": [item["id"] for item in selected],
            },
            prompt_artifact_id=prompt_artifact.id,
            response_artifact_id=response_artifact.id,
        )
        for tool_call_id in sorted(evidence_calls, key=str):
            await repository.record_agent_tool_edge(
                decision.id, tool_call_id, "input", "qualifies_bulk_cohort"
            )
        await repository.append_event(
            "run",
            run_id,
            "bulk_rosetta.cohort_selected",
            "bulk-rosetta-selection-v1",
            {
                "candidate_limit": candidate_limit,
                "selected_count": len(selected),
                "decision_id": str(decision.id),
            },
        )
    return {
        "candidate_limit": candidate_limit,
        "selected_count": len(selected),
        "decision_id": str(decision.id),
        "candidates": selected,
    }


@activity.defn(name="persist_bulk_evaluation_failure")
async def persist_bulk_evaluation_failure(request: dict[str, Any]) -> dict[str, Any]:
    """Persist a failed bulk attempt without turning it into a sequence rejection."""
    run_id = uuid.UUID(request["run_id"])
    candidate_id = uuid.UUID(request["candidate"]["id"])
    raw = {
        "status": "failed",
        "stage": request["stage"],
        "error_type": request.get("error_type"),
        "error": request.get("error"),
        "sequence_remains_eligible": True,
    }
    environment_sha256, _ = fingerprint_runtime()
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            "bulk-rosetta-failure-recorder",
            "1.0.0",
            environment_sha256,
            {"candidate_id": str(candidate_id), "stage": request["stage"]},
            {"policy": "preserve-failure-without-sequence-rejection"},
            raw,
            model_uri="deterministic://bulk-rosetta-failure-recorder",
        )
        await repository.record_evaluation(
            candidate_id,
            call.id,
            "bulk_rosetta_status",
            None,
            None,
            raw,
            text_value="failed",
            limitations=["bulk structural evaluation failed; sequence was not rejected"],
        )
    return {"candidate_id": str(candidate_id), **raw, "tool_call_id": str(call.id)}


@activity.defn(name="export_bulk_rosetta_csv")
async def export_bulk_rosetta_csv(request: dict[str, Any]) -> dict[str, Any]:
    """Export the selected cohort and its immutable metric evidence as CSV."""
    run_id = uuid.UUID(request["run_id"])
    selected = request["candidates"]
    candidate_ids = [uuid.UUID(item["id"]) for item in selected]
    result_status = {item["candidate_id"]: item["status"] for item in request["results"]}
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.id.in_(candidate_ids))
                .order_by(Candidate.generation, Candidate.proposal_rank, Candidate.id)
            )
        )
        evaluations = list(
            await session.scalars(
                select(Evaluation)
                .where(Evaluation.candidate_id.in_(candidate_ids))
                .order_by(Evaluation.created_at, Evaluation.id)
            )
        )
        rows = build_bulk_rosetta_rows(candidates, evaluations, result_status)
        payload = render_bulk_rosetta_csv(rows)
        stored = await asyncio.to_thread(
            ContentAddressedObjectStore().put_bytes, payload, "text/csv; charset=utf-8"
        )
        completed_count = sum(row["rosetta_dg_separated_reu"] is not None for row in rows)
        output = {
            "row_count": len(rows),
            "completed_dg_count": completed_count,
            "artifact": asdict(stored),
            "columns": BULK_ROSETTA_CSV_COLUMNS,
        }
        environment_sha256, _ = fingerprint_runtime()
        call = await repository.record_completed_tool_call(
            run_id,
            "bulk-rosetta-csv-export",
            "1.0.0",
            environment_sha256,
            {"candidate_ids": [str(candidate_id) for candidate_id in candidate_ids]},
            {
                "format": "csv",
                "sort": "rosetta_dg_separated_reu_ascending_missing_last",
                "pack_separated": False,
            },
            output,
            model_uri="deterministic://bulk-rosetta-csv-export",
        )
        await _register_artifact(
            session,
            call.id,
            asdict(stored),
            "candidate_results_csv",
            {"row_count": len(rows), "completed_dg_count": completed_count},
        )
        await repository.append_event(
            "run",
            run_id,
            "bulk_rosetta.csv_exported",
            "bulk-rosetta-csv-export",
            {
                "row_count": len(rows),
                "completed_dg_count": completed_count,
                "artifact_sha256": stored.sha256,
                "tool_call_id": str(call.id),
            },
        )
    return {**output, "tool_call_id": str(call.id)}


@activity.defn(name="finalize_run")
async def finalize_run(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    async with SessionFactory() as session, session.begin():
        run = await session.scalar(
            select(ExperimentRun).where(ExperimentRun.id == run_id).with_for_update()
        )
        if run is None:
            raise KeyError(f"run not found: {run_id}")
        if run.status == RunStatus.SUCCEEDED:
            return {"run_id": str(run_id), "status": "succeeded"}
        run.status = RunStatus.SUCCEEDED
        run.finished_at = datetime.now(UTC)
        await ExperimentRepository(session).append_event(
            "run",
            run_id,
            "run.succeeded",
            "temporal",
            {
                "structure_count": len(request["structures"]),
                "rosetta_count": len(request.get("rosetta_results", [])),
                "generation_count": request.get("generation_count", 1),
                "agent_decision_count": request.get("agent_decision_count", 0),
                "bulk_rosetta_count": request.get("bulk_rosetta_count", 0),
                "bulk_rosetta_candidate_limit": request.get(
                    "bulk_rosetta_candidate_limit", 0
                ),
                "bulk_csv_report_threshold": request.get("bulk_csv_report_threshold", 200),
                "bulk_csv": request.get("bulk_csv"),
                "affinity_count": 0,
                "affinity_lane": "not_admitted",
            },
        )
    return {"run_id": str(run_id), "status": "succeeded"}
