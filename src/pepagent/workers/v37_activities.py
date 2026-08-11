from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import uuid
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import select
from temporalio import activity

from pepagent.db.models import AgentDecision, Candidate, Evaluation, ToolCall
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.developability import CANONICAL_AMINO_ACIDS
from pepagent.domain.enums import CandidateStatus
from pepagent.evidence_replay import build_database_evidence_graph
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore
from pepagent.v34_external_adapters import (
    build_knowledge_command,
)
from pepagent.v37_evidence import build_v37_evidence_plan
from pepagent.v37_persistence import (
    validate_v37_database_object_replay as _validate_v37_replay_graph,
)
from pepagent.v37_preregistration import V37Manifest
from pepagent.v37_provider_consumers import (
    build_v37_pepshot_inspect_request,
    consume_v37_pepshot_inspection,
)
from pepagent.v37_selection import select_v37_lanes
from pepagent.workers.activities import _register_artifact, _store_json

V37_ACTIVITY_VERSION = "v37.0.0"


def _select_v37_coordinate_artifact(structure: dict[str, Any]) -> dict[str, Any]:
    artifacts = structure["provenance"]["engine_artifacts"]
    coordinates = [
        item
        for item in artifacts
        if Path(item["path"]).suffix.lower() in {".cif", ".pdb"}
    ]
    preferred = [
        item for item in coordinates if "model_0" in Path(item["path"]).name
    ]
    if not (preferred or coordinates):
        raise ValueError("v37 PepShot inspect requires a persisted coordinate artifact")
    return sorted(preferred or coordinates, key=lambda item: item["path"])[0]


async def _run_process(command: list[str], *, cwd: Path | None = None) -> str:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )
    lines = []
    while True:
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=30)
        except TimeoutError:
            activity.heartbeat({"command": command[0], "status": "running"})
            continue
        if not line:
            break
        lines.append(line.decode(errors="replace").rstrip())
        lines = lines[-80:]
    code = await process.wait()
    output = "\n".join(lines)
    if code:
        raise RuntimeError(f"v37 subprocess failed ({code}): {output[-8000:]}")
    return output


def _generator_command(
    engine: dict[str, Any], runtime: dict[str, Any], request_path: Path, output_path: Path
) -> list[str]:
    base = [
        str(runtime["python_path"]),
        str(runtime["adapter_path"]),
        "--request",
        str(request_path),
        "--output",
        str(output_path),
    ]
    generator = engine["generator_id"]
    if generator == "hydramp":
        return [
            *base,
            "--model-path",
            str(runtime["model_path"]),
            "--decomposer-path",
            str(runtime["decomposer_path"]),
            "--model-archive",
            str(runtime["model_archive_path"]),
        ]
    if generator == "ampgan_v2":
        return [
            *base,
            "--source-dir",
            str(runtime["source_dir"]),
            "--model-dir",
            str(runtime["model_dir"]),
        ]
    if generator == "amp_designer":
        return [
            *base,
            "--config",
            str(runtime["model_config_path"]),
            "--weights",
            str(runtime["model_weights_path"]),
            "--vocab",
            str(runtime["vocab_path"]),
        ]
    raise ValueError(f"unknown v37 generator: {generator}")


@activity.defn(name="generate_v37_batch")
async def generate_v37_batch(request: dict[str, Any]) -> dict[str, Any]:
    engine = request["engine"]
    runtime = request["runtime"]
    generator = engine["generator_id"]
    seed = int(request["seed"])
    settings = get_settings()
    work = Path(settings.work_root) / request["run_id"] / "v37" / generator / str(seed)
    await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
    request_path = work / "request.json"
    output_path = work / "raw-output.json"
    payload: dict[str, Any] = {
        "generator_id": generator,
        "seed": seed,
        "raw_proposal_budget": 1000,
    }
    if generator == "amp_designer":
        payload.update(
            batch_size=100,
            batches=10,
            top_k=10,
            top_p=1.0,
            temperature=None,
            decode_steps=34,
            device=runtime["device"],
        )
    await asyncio.to_thread(
        request_path.write_text,
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    command = _generator_command(engine, runtime, request_path, output_path)
    stdout = await _run_process(command, cwd=Path(runtime.get("cwd", work)))
    result = json.loads(await asyncio.to_thread(output_path.read_text, encoding="utf-8"))
    if result.get("generator_id") != generator or result.get("seed") != seed:
        raise ValueError("v37 generator output identity mismatch")
    if len(result.get("records", [])) != 1000:
        raise ValueError("v37 generator output must contain exactly 1000 records")
    return {
        "result": result,
        "runtime_identity": runtime["identity"],
        "environment_sha256": runtime["environment_sha256"],
        "weights_sha256": runtime["weights_sha256"],
        "stdout_tail": stdout[-8000:],
        "attempt": activity.info().attempt,
    }


@activity.defn(name="persist_v37_generation_batch")
async def persist_v37_generation_batch(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    manifest = request["manifest"]
    generated = request["generated"]
    result = generated["result"]
    generator = result["generator_id"]
    seed = int(result["seed"])
    raw_records = result["records"]
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            f"v37-generate-{generator}",
            str(result["adapter_version"]),
            generated["environment_sha256"],
            {"v37_logical_id": f"v37:generate:{generator}:{seed}", "seed": seed},
            {"raw_proposal_budget": 1000, "no_refill": True},
            result,
            weights_sha256=(
                generated["weights_sha256"]
                if isinstance(generated["weights_sha256"], str)
                else sha256_json(generated["weights_sha256"])
            ),
            random_seed=seed,
            attempt=int(generated["attempt"]),
        )
        existing = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == run_id, Candidate.generator_call_id == call.id)
                .order_by(Candidate.proposal_rank)
            )
        )
        if existing:
            return {
                "candidates": [
                    {
                        "id": str(item.id),
                        "sequence": item.sequence,
                        "sequence_sha256": item.sequence_sha256,
                        "generator_id": generator,
                        "seed": seed,
                    }
                    for item in existing
                ],
                "idempotently_recovered": True,
            }
        artifact = await _store_json(
            {
                "records": raw_records,
                "runtime_identity": generated["runtime_identity"],
                "stdout_tail": generated["stdout_tail"],
            }
        )
        await _register_artifact(
            session,
            call.id,
            asdict(artifact),
            "v37_raw_generator_output",
            {"generator_id": generator, "seed": seed},
        )
        existing_sequences = set(
            await session.scalars(select(Candidate.sequence).where(Candidate.run_id == run_id))
        )
        seen = set()
        retained = []
        occurrence_witness = []
        cell_index = next(
            index
            for index, engine in enumerate(manifest["generators"]["engines"])
            if engine["generator_id"] == generator
        )
        seed_index = next(
            index
            for index, engine in enumerate(manifest["generators"]["engines"])
            if engine["generator_id"] == generator
            for index, value in enumerate(engine["seeds"])
            if int(value) == seed
        )
        cell_ordinal = cell_index * 3 + seed_index
        for expected_rank, row in enumerate(raw_records, start=1):
            if int(row["raw_rank"]) != expected_rank:
                raise ValueError("v37 raw ranks must be contiguous")
            sequence = "".join(str(row["sequence"]).split()).upper()
            disposition = "retained"
            if not sequence or set(sequence) - CANONICAL_AMINO_ACIDS:
                disposition = "invalid_symbol_or_empty"
            elif not 10 <= len(sequence) <= 25:
                disposition = "out_of_length"
            elif sequence in seen or sequence in existing_sequences:
                disposition = "duplicate"
            elif len(retained) >= 100:
                disposition = "valid_after_fixed_first_100"
            if disposition == "retained":
                seen.add(sequence)
                candidate = await repository.add_candidate(
                    run_id,
                    sequence,
                    generation=0,
                    proposal_rank=cell_ordinal * 1000 + expected_rank,
                    generator_call_id=call.id,
                    metadata={
                        "benchmark_id": manifest["benchmark_id"],
                        "generator_id": generator,
                        "generator_seed": seed,
                        "raw_rank": expected_rank,
                    },
                    actor="v37-generation",
                )
                retained.append(candidate)
            else:
                candidate = None
            occurrence_witness.append(
                {"raw_rank": expected_rank, "sequence": sequence, "disposition": disposition}
            )
            await repository.record_candidate_occurrence(
                run_id=run_id,
                tool_call_id=call.id,
                parent_candidate_id=None,
                occurrence_rank=expected_rank,
                occurrence_kind="de_novo",
                opaque_arm_label="rapid_champion",
                sequence=sequence,
                candidate_id=candidate.id if candidate is not None else None,
                metadata={
                    "disposition": disposition,
                    "generator_id": generator,
                    "generator_seed": seed,
                },
            )
        witness_artifact = await _store_json({"occurrences": occurrence_witness})
        await _register_artifact(
            session,
            call.id,
            asdict(witness_artifact),
            "v37_proposal_occurrence_manifest",
            {"generator_id": generator, "seed": seed},
        )
        await repository.append_event(
            "run",
            run_id,
            "v37.generation_batch_frozen",
            "v37-generation",
            {
                "generator_id": generator,
                "seed": seed,
                "generator_tool_call_id": str(call.id),
                "occurrence_witness_sha256": sha256_json(occurrence_witness),
                "retained_count": len(retained),
                "shortfall": 100 - len(retained),
                "no_refill": True,
            },
        )
    return {
        "candidates": [
            {
                "id": str(item.id),
                "sequence": item.sequence,
                "sequence_sha256": item.sequence_sha256,
                "generator_id": generator,
                "seed": seed,
            }
            for item in retained
        ]
    }


async def _candidate_payloads(session: Any, run_id: uuid.UUID) -> list[dict[str, Any]]:
    candidates = list(
        await session.scalars(
            select(Candidate).where(Candidate.run_id == run_id).order_by(Candidate.proposal_rank)
        )
    )
    evaluations = list(
        await session.scalars(
            select(Evaluation).where(Evaluation.candidate_id.in_([item.id for item in candidates]))
        )
    )
    numeric: dict[uuid.UUID, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    labels: dict[uuid.UUID, dict[str, str]] = defaultdict(dict)
    for item in evaluations:
        if item.numeric_value is not None:
            numeric[item.candidate_id][item.metric_name].append(float(item.numeric_value))
        if item.text_value is not None:
            labels[item.candidate_id][item.metric_name] = item.text_value
    payloads = []
    for item in candidates:
        metrics = {
            name: float(statistics.median(values))
            for name, values in numeric[item.id].items()
        }
        aliases = {
            "median_pair_iptm": ("boltz2_pair_iptm_median", statistics.median),
            "median_pocket_coverage": ("pocket_coverage_fraction", statistics.median),
            "maximum_geometric_clash_count": ("interface_clash_count", max),
            "peptide_backbone_displacement_range": (
                "rosetta_peptide_bb_rmsd_angstrom",
                lambda values: max(values) - min(values),
            ),
            "median_representative_rosetta_interface_delta_g": (
                "rosetta_dg_separated_reu",
                statistics.median,
            ),
        }
        for alias, (source, reducer) in aliases.items():
            values = numeric[item.id].get(source)
            if values:
                metrics[alias] = float(reducer(values))
        payloads.append(
            {
            "id": str(item.id),
            "sequence": item.sequence,
            "sequence_sha256": item.sequence_sha256,
            "generator_id": item.metadata_json["generator_id"],
            "seed": item.metadata_json["generator_seed"],
            "source_ordinal": item.metadata_json["raw_rank"],
            "metrics": metrics,
            "labels": labels[item.id],
            }
        )
    return payloads


async def _validate_stage1_observations(
    session: Any,
    *,
    run_id: uuid.UUID,
    candidate_ids: list[uuid.UUID],
    manifest: dict[str, Any],
) -> None:
    required = list(
        manifest["stage_1_sequence_evaluation"]["required_metric_names"]
    )
    rows = list(
        await session.scalars(
            select(Evaluation).where(
                Evaluation.candidate_id.in_(candidate_ids),
                Evaluation.metric_name.in_(required),
            )
        )
    )
    by_key: dict[tuple[uuid.UUID, str], list[Evaluation]] = defaultdict(list)
    for row in rows:
        by_key[(row.candidate_id, row.metric_name)].append(row)
    label_values = {
        "toxinpred3_label": {"Toxin", "Non-Toxin"},
        "macrel_hemolysis_label": {"high", "low"},
    }
    for candidate_id in candidate_ids:
        for metric_name in required:
            observed = by_key[(candidate_id, metric_name)]
            if len(observed) != 1:
                raise ValueError(
                    "v37 stage-1 requires exactly one observation per "
                    f"candidate and metric: {candidate_id}/{metric_name}"
                )
            row = observed[0]
            if row.status != "succeeded" or row.out_of_domain:
                raise ValueError("v37 stage-1 contains failed or out-of-domain evidence")
            if metric_name in label_values:
                if row.text_value not in label_values[metric_name]:
                    raise ValueError("v37 stage-1 categorical label is outside its enum")
            elif row.numeric_value is None or not math.isfinite(row.numeric_value):
                raise ValueError("v37 stage-1 numeric evidence is missing or non-finite")
    calls = list(
        await session.scalars(select(ToolCall).where(ToolCall.run_id == run_id))
    )
    metric_calls = {
        str(call.input_json.get("v37_logical_id")): call
        for call in calls
        if str(call.input_json.get("v37_logical_id", "")).startswith("v37:metric:")
    }
    expected_logical_ids = {
        f"v37:metric:{item['name']}"
        for item in manifest["stage_1_sequence_evaluation"]["metric_plugins"]
    }
    if set(metric_calls) != expected_logical_ids:
        raise ValueError("v37 stage-1 metric ToolCall set differs from five-plugin contract")
    expected_owner_by_metric = {
        metric_name: f"v37:metric:{plugin['name']}"
        for plugin in manifest["stage_1_sequence_evaluation"]["metric_plugins"]
        for metric_name in plugin["observation_names"]
    }
    for (candidate_id, metric_name), observed in by_key.items():
        expected_logical_id = expected_owner_by_metric[metric_name]
        call = metric_calls[expected_logical_id]
        expected_tool_call_id = str(getattr(call, "id", expected_logical_id))
        if str(observed[0].tool_call_id) != expected_tool_call_id:
            raise ValueError(
                "v37 stage-1 plugin ToolCall ownership mismatch: "
                f"{candidate_id}/{metric_name}"
            )


def _stage1_lanes(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    shortlist = manifest["stage_1_sequence_evaluation"]["shortlist"]
    return [
        {
            "name": name,
            "quota": quota,
            "objective_families": shortlist["lane_objective_families"][name],
        }
        for name, quota in shortlist["lane_quotas"].items()
    ]


@activity.defn(name="persist_v37_stage1_shortlist")
async def persist_v37_stage1_shortlist(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    manifest = request["manifest"]
    async with SessionFactory() as session, session.begin():
        candidates = await _candidate_payloads(session, run_id)
        await _validate_stage1_observations(
            session,
            run_id=run_id,
            candidate_ids=[uuid.UUID(item["id"]) for item in candidates],
            manifest=manifest,
        )
        result = select_v37_lanes(
            candidates,
            lanes=_stage1_lanes(manifest),
            family_objectives=manifest["stage_1_sequence_evaluation"]["endpoint_families"],
            maximum_similarity=0.80,
            maximum_per_generator=6,
            maximum_per_generator_seed=2,
        )
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            "v37-stage1-shortlist",
            V37_ACTIVITY_VERSION,
            sha256_json({"policy": "v37-stage1-pareto-maximin"}),
            {"v37_logical_id": "v37:stage1-shortlist"},
            {"manifest_sha256": sha256_json(manifest), "weighted_total": False},
            result,
            model_uri="deterministic://v37-stage1-pareto-maximin",
        )
        decision = await repository.record_agent_decision(
            run_id,
            0,
            "v37_stage1_shortlist",
            "deterministic-evidence-governance-agent",
            V37_ACTIVITY_VERSION,
            "Apply frozen lane-local Pareto and diversity rules without scalarization.",
            json.dumps(result, sort_keys=True),
            result,
        )
        await repository.record_agent_tool_edge(
            decision.id, call.id, "output", "materializes_stage1_shortlist"
        )
        by_id = {item["id"]: item for item in candidates}
        shortlisted = [by_id[item] for item in result["selected_ids"]]
        for item in shortlisted:
            await repository.transition_candidate(
                uuid.UUID(item["id"]),
                CandidateStatus.STRUCTURE_QUEUED,
                "v37-stage1-shortlist",
                "selected by frozen v37 stage-1 portfolio",
            )
    return {"candidates": shortlisted, "decision_id": str(decision.id)}


@activity.defn(name="run_and_persist_v37_knowledge")
async def run_and_persist_v37_knowledge(request: dict[str, Any]) -> dict[str, Any]:
    runtime = request["runtime"]
    settings = get_settings()
    work = Path(settings.work_root) / request["run_id"] / "v37" / "knowledge"
    await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
    output_path = work / "context-pack.json"
    command = build_knowledge_command(
        python_executable=Path(runtime["python_path"]),
        kbctl_path=Path(runtime["kbctl_path"]),
        target_key="AceA",
        query=str(request["query"]),
        application="v37_rapid_champion_generation",
        output_path=output_path,
        config_path=Path(runtime["config_path"]) if runtime.get("config_path") else None,
    )
    await _run_process(command, cwd=Path(runtime["cwd"]))
    payload = json.loads(await asyncio.to_thread(output_path.read_text, encoding="utf-8"))
    run_id = uuid.UUID(request["run_id"])
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            "v37-knowledge",
            str(runtime["release_revision"]),
            str(runtime["runtime_manifest_sha256"]),
            {"v37_logical_id": "v37:knowledge", "query": request["query"]},
            {"positive_support_is_not_score": True},
            payload,
        )
        artifact = await _store_json(payload)
        await _register_artifact(
            session, call.id, asdict(artifact), "v37_knowledge_context_pack", {}
        )
    return {"tool_call_id": str(call.id), "context_pack": payload}


@activity.defn(name="run_and_persist_v37_pepshot")
async def run_and_persist_v37_pepshot(request: dict[str, Any]) -> dict[str, Any]:
    runtime = request["runtime"]
    settings = get_settings()
    provider_contract = request["provider_contract"]
    if (
        provider_contract.get("required_route") != "deterministic_inspect"
        or provider_contract.get("fallback_allowed") is not False
        or runtime.get("release_id") != provider_contract.get("release_id")
        or runtime.get("release_manifest_sha256")
        != provider_contract.get("release_manifest_sha256")
        or runtime.get("runtime_manifest_sha256")
        != provider_contract.get("runtime_manifest_sha256")
        or runtime.get("contract_sha256") != provider_contract.get("contract_sha256")
    ):
        raise ValueError("v37 PepShot runtime differs from the frozen provider contract")
    attempt = activity.info().attempt
    root = Path(settings.work_root) / request["run_id"] / "v37" / "pepshot"
    contract_dir = root / f"contract-attempt-{attempt}"
    await asyncio.to_thread(contract_dir.mkdir, parents=True, exist_ok=True)
    contract_path = contract_dir / "inspect-contract.json"
    await _run_process(
        [
            str(runtime["executable"]),
            "contract",
            "--task",
            "inspect",
            "--out",
            str(contract_path),
        ],
        cwd=Path(runtime["cwd"]),
    )
    inspect_contract = json.loads(
        await asyncio.to_thread(contract_path.read_text, encoding="utf-8")
    )
    if (
        inspect_contract.get("task") != "inspect"
        or inspect_contract.get("fallback_allowed") is not False
        or inspect_contract.get("route", {}).get("task") != "inspect"
    ):
        raise ValueError("v37 PepShot inspect contract is not fallback-free")
    inspections = []
    detailed_outputs: list[dict[str, Any]] = []
    for candidate in request["candidates"]:
        candidate_id = candidate["id"]
        structures = request["structures_by_candidate"].get(candidate_id)
        if not isinstance(structures, list):
            raise ValueError("v37 PepShot structure mapping is incomplete")
        provider_poses = []
        coordinates_by_seed: dict[int, dict[str, Any]] = {}
        for structure in structures:
            seed = int(structure["input"]["seed"])
            coordinate = _select_v37_coordinate_artifact(structure)
            suffix = Path(coordinate["path"]).suffix.lower()
            coordinates_by_seed[seed] = coordinate
            provider_poses.append(
                {
                    "run_id": request["run_id"],
                    "candidate_id": candidate_id,
                    "pose_id": structure["tool_call_id"],
                    "boltz_seed": seed,
                    "pair_iptm": structure["boltz2"]["pair_iptm"],
                    "coordinate_path": f"poses/{seed}{suffix}",
                    "coordinate_sha256": coordinate["sha256"],
                }
            )
        spec = build_v37_pepshot_inspect_request(
            candidate={
                "run_id": request["run_id"],
                "candidate_id": candidate_id,
                "sequence": candidate["sequence"],
                "sequence_sha256": candidate["sequence_sha256"],
            },
            poses=provider_poses,
            receptor_chains=["A"],
            peptide_chains=["B"],
            pocket_residues=[
                {"chain": "A", "number": int(number)}
                for number in request["experiment_spec"]["target"].get(
                    "pocket_residues", []
                )
            ],
        )
        selected_seed = int(spec["seed"])
        coordinate = coordinates_by_seed[selected_seed]
        coordinate_bytes = await asyncio.to_thread(
            ContentAddressedObjectStore().get_bytes, coordinate["uri"]
        )
        if sha256_bytes(coordinate_bytes) != coordinate["sha256"]:
            raise ValueError("v37 PepShot coordinate object hash drifted")
        work = root / candidate_id / f"attempt-{attempt}"
        await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
        spec_path = work / "spec.json"
        coordinate_path = work / Path(spec["structure_path"])
        await asyncio.to_thread(coordinate_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(coordinate_path.write_bytes, coordinate_bytes)
        await asyncio.to_thread(
            spec_path.write_text,
            json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        inspection_path = work / "inspection.json"
        receipt = await _run_process(
            [
                str(runtime["executable"]),
                "inspect",
                "--spec",
                str(spec_path),
                "--out",
                str(inspection_path),
            ],
            cwd=Path(runtime["cwd"]),
        )
        inspection = json.loads(
            await asyncio.to_thread(inspection_path.read_text, encoding="utf-8")
        )
        provider_result = consume_v37_pepshot_inspection(
            request=spec,
            inspection=inspection,
            contract_receipt=inspect_contract,
            provider_release_receipt={
                "provider_contract_verified": True,
                "release_id": runtime["release_id"],
                "release_manifest_sha256": runtime["release_manifest_sha256"],
                "runtime_manifest_sha256": runtime["runtime_manifest_sha256"],
            },
        )
        audit = inspection.get("audit", {})
        findings = audit.get("spatial_findings")
        if not isinstance(findings, list) or audit.get("spatial_finding_count") != len(
            findings
        ):
            raise ValueError("v37 PepShot inspection finding manifest is inconsistent")
        plausibility = audit.get("interface_plausibility", {})
        summary = {
            **asdict(provider_result),
            "spatial_finding_count": len(findings),
            "blocking_finding_types": list(
                plausibility.get("blocking_finding_types", [])
            ),
        }
        inspections.append(summary)
        detailed_outputs.append(
            {
                "candidate_id": candidate_id,
                "spec": spec,
                "inspection": inspection,
                "stdout_receipt": receipt,
                "summary": summary,
            }
        )
    output = {"inspections": inspections}
    run_id = uuid.UUID(request["run_id"])
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        contract_call = await repository.record_completed_tool_call(
            run_id,
            "pepshot-contract",
            str(runtime["release_id"]),
            str(runtime["runtime_manifest_sha256"]),
            {"task": "inspect"},
            {"fallback_allowed": False},
            inspect_contract,
            model_uri="provider://pepshot/contract/inspect",
            attempt=attempt,
        )
        contract_artifact = await _store_json(inspect_contract)
        await _register_artifact(
            session,
            contract_call.id,
            asdict(contract_artifact),
            "pepshot_inspect_contract",
            {"release_id": runtime["release_id"]},
        )
        detail_calls = []
        for detail in detailed_outputs:
            summary = detail["summary"]
            detail_call = await repository.record_completed_tool_call(
                run_id,
                "pepshot-inspect",
                str(runtime["release_id"]),
                str(runtime["runtime_manifest_sha256"]),
                {
                    "candidate_id": detail["candidate_id"],
                    "request_sha256": sha256_json(detail["spec"]),
                    "coordinate_sha256": summary["source_sha256"],
                },
                {
                    "route": "inspect",
                    "fallback_allowed": False,
                    "provider_contract_sha256": runtime["contract_sha256"],
                },
                detail["inspection"],
                model_uri="provider://pepshot/inspect",
                random_seed=int(summary["boltz_seed"]),
                attempt=attempt,
            )
            await repository.record_tool_dependency(
                detail_call.id, contract_call.id, "uses_verified_inspect_contract"
            )
            request_artifact = await _store_json(detail["spec"])
            inspection_artifact = await _store_json(detail["inspection"])
            receipt_artifact = await _store_json(
                {
                    "stdout": detail["stdout_receipt"],
                    "inspection_sha256": summary["inspection_sha256"],
                    "disposition": summary["disposition"],
                }
            )
            await _register_artifact(
                session,
                detail_call.id,
                asdict(request_artifact),
                "pepshot_inspect_request",
                {"candidate_id": detail["candidate_id"]},
            )
            await _register_artifact(
                session,
                detail_call.id,
                asdict(inspection_artifact),
                "pepshot_inspection",
                {"candidate_id": detail["candidate_id"]},
            )
            await _register_artifact(
                session,
                detail_call.id,
                asdict(receipt_artifact),
                "pepshot_inspect_receipt",
                {"candidate_id": detail["candidate_id"]},
            )
            detail_calls.append(detail_call)
        call = await repository.record_completed_tool_call(
            run_id,
            "v37-pepshot",
            str(runtime["release_id"]),
            str(runtime["runtime_manifest_sha256"]),
            {"v37_logical_id": "v37:pepshot"},
            {
                "route": "deterministic_inspect",
                "fallback_allowed": False,
                "provider_contract_sha256": runtime["contract_sha256"],
            },
            output,
            model_uri="provider://pepshot/inspect-aggregate",
            attempt=attempt,
        )
        for detail_call in detail_calls:
            await repository.record_tool_dependency(
                call.id, detail_call.id, "aggregates_candidate_inspection"
            )
        artifact = await _store_json(output)
        await _register_artifact(
            session,
            call.id,
            asdict(artifact),
            "v37_pepshot_inspection_manifest",
            {"candidate_count": len(inspections)},
        )
    return {"tool_call_id": str(call.id), **output}


@activity.defn(name="persist_v37_structure_stage_summaries")
async def persist_v37_structure_stage_summaries(
    request: dict[str, Any],
) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    manifest = request["manifest"]
    structure_contract = manifest["stage_2_structure_confirmation"]
    poses_per_candidate = int(structure_contract["poses_per_candidate"])
    rosetta_decoys_per_pose = int(structure_contract["rosetta_decoys_per_pose"])
    candidate_ids = [str(value) for value in request["candidate_ids"]]
    structures_by_candidate = request["structures_by_candidate"]
    for candidate_id in candidate_ids:
        poses = structures_by_candidate.get(candidate_id)
        if not isinstance(poses, list) or len(poses) != poses_per_candidate:
            raise ValueError(f"v37 pose coverage mismatch for {candidate_id}")
    if set(structures_by_candidate) != set(candidate_ids):
        raise ValueError("v37 pose coverage contains an unknown or missing candidate")
    rosetta_results = request["rosetta_results"]
    if len(rosetta_results) != len(candidate_ids) * poses_per_candidate:
        raise ValueError("v37 decoy coverage has the wrong pose count")
    for result in rosetta_results:
        decoys = result.get("rosetta", {}).get("decoys")
        if not isinstance(decoys, list) or len(decoys) != rosetta_decoys_per_pose:
            raise ValueError("v37 decoy coverage mismatch for a frozen pose")
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        calls = list(await session.scalars(select(ToolCall).where(ToolCall.run_id == run_id)))
        structure_sources = [
            item
            for item in calls
            if "boltz" in item.tool_name.lower()
            or "interface" in item.tool_name.lower()
        ]
        rosetta_sources = [item for item in calls if "rosetta" in item.tool_name.lower()]
        if not structure_sources or not rosetta_sources:
            raise ValueError("v37 structure summaries require persisted Boltz and Rosetta calls")
        structure = await repository.record_completed_tool_call(
            run_id,
            "v37-structure",
            V37_ACTIVITY_VERSION,
            sha256_json({"summary": "v37-structure"}),
            {"v37_logical_id": "v37:structure"},
            {"all_poses_required": True},
            {"source_tool_call_ids": sorted(str(item.id) for item in structure_sources)},
        )
        rosetta = await repository.record_completed_tool_call(
            run_id,
            "v37-rosetta",
            V37_ACTIVITY_VERSION,
            sha256_json({"summary": "v37-rosetta"}),
            {"v37_logical_id": "v37:rosetta"},
            {"all_decoys_required": True, "same_protocol_relative_only": True},
            {"source_tool_call_ids": sorted(str(item.id) for item in rosetta_sources)},
        )
        for item in structure_sources:
            await repository.record_tool_dependency(
                structure.id, item.id, "summarizes_structure_evidence"
            )
        for item in rosetta_sources:
            await repository.record_tool_dependency(
                rosetta.id, item.id, "summarizes_rosetta_evidence"
            )
        await repository.record_tool_dependency(
            rosetta.id, structure.id, "scores_frozen_structure_stage"
        )
    return {"structure_call_id": str(structure.id), "rosetta_call_id": str(rosetta.id)}


@activity.defn(name="persist_v37_final_portfolio_and_replay")
async def persist_v37_final_portfolio_and_replay(
    request: dict[str, Any],
) -> dict[str, Any]:
    run_id = uuid.UUID(request["run_id"])
    manifest = request["manifest"]
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        existing = await session.scalar(
            select(AgentDecision).where(
                AgentDecision.run_id == run_id,
                AgentDecision.decision_type == "v37_final_portfolio",
            )
        )
        if existing is not None:
            recovered = True
            await validate_v37_database_object_replay(
                session=session,
                run_id=run_id,
                manifest=manifest,
            )
        else:
            recovered = False
        candidates = await _candidate_payloads(session, run_id)
        eligible_ids = set(request["structurally_eligible_candidate_ids"])
        eligible = [item for item in candidates if item["id"] in eligible_ids]
        lanes = [
            {
                "name": lane["name"],
                "quota": lane["quota"],
                "objective_families": lane["Pareto_objective_families"],
                "required_soft_labels": lane.get("required_soft_labels", {}),
            }
            for lane in manifest["final_portfolio"]["lanes"]
        ]
        families = dict(manifest["stage_1_sequence_evaluation"]["endpoint_families"])
        families["structure"] = manifest["stage_2_structure_confirmation"][
            "Pareto_objectives"
        ]
        recomputed_portfolio = select_v37_lanes(
            eligible,
            lanes=lanes,
            family_objectives=families,
            maximum_similarity=0.80,
            maximum_per_generator=2,
            maximum_per_generator_seed=1,
        )
        if recovered:
            portfolio = existing.structured_json
            if portfolio != recomputed_portfolio:
                raise ValueError("v37 recovered final portfolio differs from database evidence")
        else:
            portfolio = recomputed_portfolio
        call = await repository.record_completed_tool_call(
            run_id,
            "v37-final-portfolio",
            V37_ACTIVITY_VERSION,
            sha256_json({"policy": "v37-final-pareto-maximin"}),
            {"v37_logical_id": "v37:final-portfolio"},
            {"manifest_sha256": sha256_json(manifest), "weighted_total": False},
            portfolio,
        )
        decision = existing or await repository.record_agent_decision(
                run_id,
                0,
                "v37_final_portfolio",
                "deterministic-evidence-governance-agent",
                V37_ACTIVITY_VERSION,
                "Apply the frozen final v37 lane portfolio without scalarization.",
                json.dumps(portfolio, sort_keys=True),
                portfolio,
            )
        await repository.record_agent_tool_edge(
            decision.id, call.id, "output", "materializes_final_portfolio"
        )
        replay_call = await repository.record_completed_tool_call(
            run_id,
            "v37-replay",
            V37_ACTIVITY_VERSION,
            sha256_json({"replay": "database-only-v37"}),
            {"v37_logical_id": "v37:replay"},
            {"database_only": True},
            {"validation_pending": True},
        )
        graph = await build_database_evidence_graph(session, run_id)
        validation, graph = await validate_v37_database_object_replay(
            session=session,
            run_id=run_id,
            manifest=manifest,
            graph=graph,
        )
        replay_payload = {
            "graph_sha256": sha256_json(graph),
            "validation": validation,
            "portfolio_sha256": call.output_sha256,
        }
        artifact = await _store_json(replay_payload)
        await _register_artifact(
            session, replay_call.id, asdict(artifact), "v37_database_replay_bundle", {}
        )
    return {
        "portfolio": portfolio,
        "portfolio_sha256": call.output_sha256,
        "replay_sha256": replay_call.output_sha256,
        "exact_database_replay": bool(validation["exact_replay"]),
        "idempotently_recovered": recovered,
    }


async def validate_v37_database_object_replay(
    *,
    session: Any,
    run_id: uuid.UUID,
    manifest: dict[str, Any],
    graph: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reconstruct and validate the closure from persisted bytes only."""
    if graph is None:
        graph = await build_database_evidence_graph(session, run_id)
    object_store = ContentAddressedObjectStore()
    artifact_payloads: dict[str, dict[str, Any]] = {}
    for artifact_row in graph.get("artifacts", []):
        try:
            raw = await asyncio.to_thread(
                object_store.get_bytes, artifact_row["storage_uri"]
            )
            payload = json.loads(raw)
        except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            artifact_payloads[str(artifact_row["sha256"])] = payload
    plan = build_v37_evidence_plan(V37Manifest.model_validate(manifest))
    validation = _validate_v37_replay_graph(
        manifest=manifest,
        plan=plan,
        graph=graph,
        artifact_payloads_by_sha256=artifact_payloads,
    )
    return validation, graph
