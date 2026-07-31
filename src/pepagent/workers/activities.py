from __future__ import annotations

import asyncio
import importlib.metadata
import json
import sys
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from pepagent.db.models import Artifact, Candidate, EvidenceArtifact, ExperimentRun
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.enums import CandidateStatus, MetricName, RunStatus
from pepagent.provenance.environment import fingerprint_runtime
from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore, StoredObject


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
    )
    output_tail: list[str] = []
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
    if return_code != 0:
        diagnostic = "\n".join(output_tail[-20:])[-8000:]
        raise RuntimeError(
            f"{module} exited with code {return_code}\n{diagnostic}"
        )
    output = await asyncio.to_thread(output_path.read_text, encoding="utf-8")
    return json.loads(output)


async def _store_json(payload: dict[str, Any]) -> StoredObject:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return await asyncio.to_thread(
        ContentAddressedObjectStore().put_bytes, encoded, "application/json"
    )


async def _store_file(path: Path) -> StoredObject:
    return await asyncio.to_thread(ContentAddressedObjectStore().put_file, path)


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
            "role": (
                "molecular_resource_archive" if path.name == "mols.tar" else "weights"
            ),
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
        session.add(
            EvidenceArtifact(tool_call_id=tool_call_id, artifact_id=artifact.id, role=role)
        )
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
        if run.status in {RunStatus.FAILED, RunStatus.SUCCEEDED}:
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


@activity.defn(name="generate_with_pepmlm")
async def generate_with_pepmlm(request: dict[str, Any]) -> list[dict[str, Any]]:
    settings = get_settings()
    spec = request["spec"]
    await _verify_pepmlm_release(
        settings.pepmlm_model_path, settings.pepmlm_weights_sha256
    )
    environment_sha256, environment = fingerprint_runtime()
    batches: list[dict[str, Any]] = []
    for length_index, length in enumerate(spec["peptide_lengths"]):
        payload = {
            "target_sequence": spec["target"]["sequence"],
            "peptide_length": length,
            "count": spec["candidates_per_length"],
            "seed": spec["seed"] + length_index * 100_000,
            "model": settings.pepmlm_model_path,
            "revision": settings.pepmlm_model_revision,
            "top_k": 3,
            "temperature": 1.0,
        }
        result = await _run_json_cli(
            "pepagent.model_workers.pepmlm_cli",
            payload,
            Path(settings.work_root) / request["run_id"] / "pepmlm" / str(length),
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
                    "peptide_length": length,
                    "count": spec["candidates_per_length"],
                    "top_k": 3,
                    "temperature": 1.0,
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


@activity.defn(name="persist_and_select_candidates")
async def persist_and_select_candidates(request: dict[str, Any]) -> list[dict[str, Any]]:
    run_id = uuid.UUID(request["run_id"])
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
                candidate = await repository.add_candidate(
                    run_id,
                    item["sequence"],
                    generation=0,
                    proposal_rank=0,
                    generator_call_id=call.id,
                    metadata={
                        "per_residue_log_probabilities": item[
                            "per_residue_log_probabilities"
                        ],
                        "seed": item["seed"],
                    },
                )
                raw_metric = {
                    "seed": item["seed"],
                    "per_residue_log_probabilities": item[
                        "per_residue_log_probabilities"
                    ],
                }
                await repository.record_evaluation(
                    candidate.id,
                    call.id,
                    MetricName.CONDITIONAL_NLL,
                    item["conditional_nll"],
                    "nats_per_residue",
                    raw_metric,
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
                persisted.append({"id": str(candidate.id), **item})

        persisted.sort(key=lambda item: item["conditional_ppl"])
        for rank, item in enumerate(persisted, start=1):
            candidate = await session.get(Candidate, uuid.UUID(item["id"]))
            if candidate is None:
                raise KeyError(f"candidate not found: {item['id']}")
            candidate.proposal_rank = rank
        selected = persisted[: int(request["spec"]["structure_top_k"])]
        for item in selected:
            await repository.transition_candidate(
                uuid.UUID(item["id"]),
                CandidateStatus.STRUCTURE_QUEUED,
                "selection-policy",
                "selected by ascending conditional PPL",
            )
    return selected


@activity.defn(name="predict_boltz2_complex")
async def predict_boltz2_complex(request: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    candidate = request["candidate"]
    spec = request["spec"]
    work_dir = Path(settings.work_root) / request["run_id"] / "boltz2" / candidate["id"]
    payload = {
        "target_sequence": spec["target"]["sequence"],
        "peptide_sequence": candidate["sequence"],
        "pocket_residues": spec["target"].get("pocket_residues", []),
        "diffusion_samples": spec["diffusion_samples"],
        "recycling_steps": spec.get("boltz_recycling_steps", 3),
        "sampling_steps": spec.get("boltz_sampling_steps", 200),
        "use_potentials": spec.get("boltz_use_potentials", True),
        "no_kernels": spec.get("boltz_no_kernels", True),
        "use_msa_server": spec.get("use_msa_server", True),
        "seed": spec["seed"],
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
    weight_manifest = await asyncio.to_thread(
        _boltz_weight_manifest, settings.boltz2_cache_path
    )
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


@activity.defn(name="score_rosetta_complex")
async def score_rosetta_complex(request: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    structure = request["structure"]
    candidate = structure["candidate"]
    spec = request["spec"]
    validation_case = request.get("validation_case")
    lane = "rosetta-validation" if validation_case else "rosetta"
    work_dir = Path(settings.work_root) / request["run_id"] / lane / candidate["id"]
    coordinate_artifact = _select_boltz_structure_artifact(structure)
    coordinate_bytes = await asyncio.to_thread(
        ContentAddressedObjectStore().get_bytes, coordinate_artifact["uri"]
    )
    source_path = work_dir / f"boltz-input{Path(coordinate_artifact['path']).suffix.lower()}"
    await asyncio.to_thread(work_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(source_path.write_bytes, coordinate_bytes)
    input_pdb = work_dir / "boltz-input.pdb"
    await asyncio.to_thread(_convert_structure_to_pdb, source_path, input_pdb)

    receptor_chains = (
        list(validation_case["receptor_chains"]) if validation_case else ["A"]
    )
    peptide_chain = validation_case["peptide_chain"] if validation_case else "B"
    payload = {
        "receptor_chains": receptor_chains,
        "peptide_chain": peptide_chain,
        "nstruct": int(spec.get("rosetta_nstruct", 200)),
        "seed": int(spec["seed"]),
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
        "input": payload,
        "parameters": {
            "score_function": payload["score_function"],
            "nstruct": payload["nstruct"],
            "prepack": True,
            "pack_input": False,
            "pack_separated": True,
            "primary_aggregation": result["primary_aggregation"],
            "validation_case": validation_case,
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
            MetricName.ROSETTA_INTERFACE_SCORE: rosetta["best_decoy"].get(
                "interface_score"
            ),
            MetricName.ROSETTA_REWEIGHTED_SCORE: rosetta["best_decoy"].get(
                "reweighted_sc"
            ),
            MetricName.ROSETTA_INTERFACE_HBONDS: rosetta["best_decoy"].get(
                "interface_hbonds"
            ),
            MetricName.ROSETTA_BURIED_SURFACE_AREA: rosetta["best_decoy"].get(
                "dSASA_int"
            ),
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
                    else (
                        "count"
                        if metric_name == MetricName.ROSETTA_INTERFACE_HBONDS
                        else "REU"
                    )
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
        await repository.transition_candidate(
            candidate_id,
            CandidateStatus.ROSETTA_SCORED,
            "rosetta",
            "FlexPepDock refinement and InterfaceAnalyzer dG evidence persisted",
        )
        result["tool_call_id"] = str(call.id)
    return result


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
                "affinity_count": 0,
                "affinity_lane": "not_admitted",
            },
        )
    return {"run_id": str(run_id), "status": "succeeded"}
