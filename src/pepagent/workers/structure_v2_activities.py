from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from temporalio import activity

from pepagent.provenance.environment import fingerprint_runtime
from pepagent.provenance.hashing import sha256_json
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore, StoredObject
from pepagent.structure_evidence_repair import (
    DEFAULT_LOCK_TIMEOUT_MS,
    DEFAULT_STATEMENT_TIMEOUT_MS,
    DEFAULT_TRANSACTION_TIMEOUT_SECONDS,
    STRUCTURE_SCORE_REFERENCE_SCHEMA,
    _configure_transaction,
    _repair_session_factory,
    load_structure_score_reference,
    persist_structure_evidence,
    validate_structure_evidence_request,
)
from pepagent.structure_v2_binding import bind_structure_v2_target_request
from pepagent.structures.interface import (
    classify_structure_support,
    reconcile_ensemble_structure_support,
)
from pepagent.workers.activities import (
    _bind_rosetta_decoy_hashes,
    _convert_structure_to_pdb,
    _run_json_cli,
    _select_boltz_structure_artifact,
    _store_file,
    _store_json,
    _structure_work_dir,
)

STRUCTURE_SCORE_SUMMARY_SCHEMA = "ampgent.structure-score-summary.2"
STRUCTURE_V2_SHARED_ROOT_ENV = "PEPAGENT_STRUCTURE_V2_SHARED_ROSETTA_ROOT"
STRUCTURE_V2_DG_THRESHOLD_REU = -50.0


def canonical_structure_result_bytes(result: Mapping[str, Any]) -> bytes:
    return json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@activity.defn(name="preflight_structure_v2_target_request_v2")
async def preflight_structure_v2_target_request_v2(
    request: dict[str, Any],
) -> dict[str, Any]:
    """Re-read the complete candidate qualification and legacy exclusion from PG."""

    activity.heartbeat(
        {
            "stage": "pg_eligibility_preflight_started",
            "run_id": request.get("run_id"),
            "target_key": request.get("target_key"),
        }
    )
    engine, factory = _repair_session_factory()
    try:
        async with asyncio.timeout(60.0):
            bound = await bind_structure_v2_target_request(
                request,
                session_factory=factory,
            )
    finally:
        await engine.dispose()
    if bound != request:
        raise ValueError("structure v2 request differs from its current PG eligibility binding")
    binding = bound["pg_eligibility_binding"]
    activity.heartbeat(
        {
            "stage": "pg_eligibility_preflight_succeeded",
            "run_id": binding["run_id"],
            "target_key": binding["target_key"],
            "candidate_count": binding["candidate_count"],
            "binding_sha256": binding["binding_sha256"],
        }
    )
    return binding


def build_structure_score_reference(
    *,
    run_id: str,
    result: dict[str, Any],
    artifact: StoredObject,
) -> dict[str, Any]:
    request = {"run_id": run_id, "rosetta_result": result}
    binding = validate_structure_evidence_request(request)
    if artifact.media_type != "application/json":
        raise ValueError("structure score result CAS artifact must be JSON")
    if artifact.sha256 != binding.result_sha256:
        raise ValueError("structure score result CAS identity differs from its payload")
    rosetta = result["rosetta"]
    provenance = result["provenance"]
    structure_support = _structure_support_label(result)
    return {
        "schema_version": STRUCTURE_SCORE_REFERENCE_SCHEMA,
        "run_id": str(binding.run_id),
        "candidate_id": str(binding.candidate_id),
        "candidate_sequence_sha256": binding.candidate_sequence_sha256,
        "seed": binding.seed,
        "parent_tool_call_id": str(binding.parent_tool_call_id),
        "interface_audit_tool_call_id": (
            str(binding.interface_audit_tool_call_id)
            if binding.interface_audit_tool_call_id is not None
            else None
        ),
        "tool_name": binding.tool_name,
        "tool_version": binding.tool_version,
        "tool_output_sha256": binding.output_sha256,
        "result_sha256": binding.result_sha256,
        "artifact": asdict(artifact),
        "summary": {
            "schema_version": STRUCTURE_SCORE_SUMMARY_SCHEMA,
            "candidate_id": str(binding.candidate_id),
            "seed": binding.seed,
            "primary_dG_separated_reu": binding.primary_dg_separated_reu,
            "minimum_dG_separated_reu": float(rosetta["dG_separated_reu"]["minimum"]),
            "median_peptide_bb_rmsd_angstrom": float(rosetta["peptide_bb_rmsd_angstrom"]["median"]),
            "decoy_count": binding.decoy_count,
            "artifact_observation_count": 2 + len(provenance["engine_artifacts"]),
            "structure_support": structure_support,
            "dG_le_minus_50": (binding.primary_dg_separated_reu <= STRUCTURE_V2_DG_THRESHOLD_REU),
        },
    }


def _structure_support_label(result: Mapping[str, Any]) -> str | None:
    interface_audit = result.get("interface_audit")
    if not isinstance(interface_audit, Mapping):
        return None
    sample_audits = interface_audit.get("sample_audits")
    if not isinstance(sample_audits, list) or not sample_audits:
        raise ValueError("structure score result lacks interface-audit samples")
    representative_index = int(interface_audit["representative_index"])
    representative = sample_audits[representative_index]
    thresholds = result["parameters"]["support_thresholds"]
    support = classify_structure_support(
        structure_available=bool(interface_audit.get("structure_available", True)),
        pair_iptm=representative.get("pair_iptm"),
        pocket_contact_count=representative.get("pocket_contact_count"),
        clash_count=representative.get("cross_chain_clash_count"),
        severe_clash_count=thresholds["severe_structure_clash_count"],
        minimum_pair_iptm=thresholds["interface_min_pair_iptm_median"],
        minimum_pocket_contacts=thresholds["interface_min_pocket_contacts"],
        rosetta_dg=result["rosetta"]["primary_dG_separated_reu"],
    )
    reconciled = reconcile_ensemble_structure_support(
        support,
        interface_audit.get("gate_checks", {}),
    )
    return str(reconciled["label"])


@activity.defn(name="score_rosetta_complex_v2")
async def score_rosetta_complex_v2(request: dict[str, Any]) -> dict[str, Any]:
    """Run Rosetta, put the full result in CAS, and return only a bound pointer."""

    recovered = await _recover_score_reference(request)
    if recovered is not None:
        activity.heartbeat(
            {
                "stage": "score_reference_reused",
                "candidate_id": recovered["candidate_id"],
                "result_sha256": recovered["result_sha256"],
                "score_reference": recovered,
            }
        )
        return recovered
    result = await _score_rosetta_payload_v2(request)
    binding = validate_structure_evidence_request(
        {"run_id": request["run_id"], "rosetta_result": result}
    )
    activity.heartbeat(
        {
            "stage": "score_result_validated",
            "candidate_id": str(binding.candidate_id),
            "decoy_count": binding.decoy_count,
        }
    )
    encoded = canonical_structure_result_bytes(result)
    store = await asyncio.to_thread(ContentAddressedObjectStore)
    artifact = await asyncio.to_thread(
        store.put_bytes,
        encoded,
        "application/json",
    )
    reference = build_structure_score_reference(
        run_id=request["run_id"],
        result=result,
        artifact=artifact,
    )
    activity.heartbeat(
        {
            "stage": "score_result_stored",
            "candidate_id": reference["candidate_id"],
            "result_sha256": reference["result_sha256"],
            "size_bytes": reference["artifact"]["size_bytes"],
            "score_reference": reference,
        }
    )
    return reference


def structure_v2_shared_rosetta_root() -> Path:
    value = os.environ.get(STRUCTURE_V2_SHARED_ROOT_ENV, "").strip()
    if not value:
        raise RuntimeError(f"{STRUCTURE_V2_SHARED_ROOT_ENV} must name one same-host shared root")
    root = Path(value)
    if not root.is_absolute():
        raise ValueError(f"{STRUCTURE_V2_SHARED_ROOT_ENV} must be an absolute path")
    root.mkdir(parents=True, exist_ok=True)
    return root


async def _recover_score_reference(
    request: Mapping[str, Any],
) -> dict[str, Any] | None:
    references: list[Mapping[str, Any]] = []
    explicit = request.get("score_reference")
    if isinstance(explicit, Mapping):
        references.append(explicit)
    try:
        heartbeat_details = activity.info().heartbeat_details
    except RuntimeError:
        heartbeat_details = []
    for detail in reversed(heartbeat_details):
        if not isinstance(detail, Mapping):
            continue
        reference = detail.get("score_reference")
        if isinstance(reference, Mapping):
            references.append(reference)
    for reference in references:
        resolved = await load_structure_score_reference(reference)
        candidate = request["structure"]["candidate"]
        binding = validate_structure_evidence_request(
            resolved,
            expected_run_id=_uuid(request.get("run_id"), "run_id"),
            expected_candidate_id=_uuid(candidate.get("id"), "candidate.id"),
            expected_seed=int(request["seed"]),
        )
        if binding.result_sha256 != reference.get("result_sha256"):
            raise ValueError("recovered Rosetta score reference identity drifted")
        return dict(reference)
    return None


async def _score_rosetta_payload_v2(request: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    structure = request["structure"]
    candidate = structure["candidate"]
    spec = request["spec"]
    validation_case = request.get("validation_case")
    lane = "rosetta-validation" if validation_case else "rosetta"
    work_dir = _structure_work_dir(
        root=str(structure_v2_shared_rosetta_root()),
        run_id=request["run_id"],
        lane=lane,
        candidate_id=candidate["id"],
        seed=int(request["seed"]),
        work_scope=request.get("work_scope"),
    )
    coordinate_artifact = _select_boltz_structure_artifact(structure)
    store = await asyncio.to_thread(ContentAddressedObjectStore)
    coordinate_bytes = await asyncio.to_thread(
        store.get_bytes,
        coordinate_artifact["uri"],
    )
    coordinate_scope = str(coordinate_artifact["sha256"])
    source_path = work_dir / (
        f"boltz-input-{coordinate_scope[:16]}{Path(coordinate_artifact['path']).suffix.lower()}"
    )
    await asyncio.to_thread(work_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(source_path.write_bytes, coordinate_bytes)
    input_pdb = work_dir / f"boltz-input-{coordinate_scope[:16]}.pdb"
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
        request_name=f"request-{sha256_json(payload)}.json",
    )
    _bind_rosetta_decoy_hashes(result)
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
                "severe_structure_clash_count": int(spec.get("severe_structure_clash_count", 25)),
                "interface_min_pair_iptm_median": float(
                    spec.get("interface_min_pair_iptm_median", 0.2)
                ),
                "interface_min_pocket_contacts": int(spec.get("interface_min_pocket_contacts", 1)),
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


def _bounded_transaction_timeout(request: Mapping[str, Any]) -> float:
    value = float(
        request.get(
            "transaction_timeout_seconds",
            DEFAULT_TRANSACTION_TIMEOUT_SECONDS,
        )
    )
    if not 1.0 <= value <= 300.0:
        raise ValueError("structure persist transaction timeout must be within 1..300s")
    return value


@activity.defn(name="persist_rosetta_evidence_v2")
async def persist_rosetta_evidence_v2(request: dict[str, Any]) -> dict[str, Any]:
    """Resolve a score pointer, atomically persist evidence, and return a thin receipt."""

    reference = request.get("score_reference")
    if not isinstance(reference, Mapping):
        raise ValueError("v2 Rosetta persistence requires one structure score reference")
    activity.heartbeat(
        {
            "stage": "score_reference_loading",
            "candidate_id": reference.get("candidate_id"),
            "result_sha256": reference.get("result_sha256"),
        }
    )
    resolved = await load_structure_score_reference(reference)
    binding = validate_structure_evidence_request(
        resolved,
        expected_run_id=_uuid(reference.get("run_id"), "score_reference.run_id"),
        expected_candidate_id=_uuid(
            reference.get("candidate_id"),
            "score_reference.candidate_id",
        ),
        expected_seed=int(reference.get("seed")),
    )
    if str(binding.run_id) != str(request.get("run_id")):
        raise ValueError("v2 persist request run differs from its score reference")
    activity.heartbeat(
        {
            "stage": "score_payload_validated",
            "candidate_id": str(binding.candidate_id),
            "decoy_count": binding.decoy_count,
        }
    )

    timeout_seconds = _bounded_transaction_timeout(request)
    engine, factory = _repair_session_factory()
    try:
        async with asyncio.timeout(timeout_seconds):
            async with factory() as session, session.begin():
                await _configure_transaction(
                    session,
                    statement_timeout_ms=min(
                        DEFAULT_STATEMENT_TIMEOUT_MS,
                        max(1, int(timeout_seconds * 1000) - 500),
                    ),
                    lock_timeout_ms=DEFAULT_LOCK_TIMEOUT_MS,
                    read_only=False,
                )
                activity.heartbeat(
                    {
                        "stage": "scientific_transaction_started",
                        "candidate_id": str(binding.candidate_id),
                    }
                )
                receipt = await persist_structure_evidence(
                    session,
                    request=resolved,
                    binding=binding,
                )
    finally:
        await engine.dispose()
    thin_receipt = {
        **asdict(receipt),
        "score_reference_sha256": sha256_json(dict(reference)),
        "primary_dG_separated_reu": reference["summary"]["primary_dG_separated_reu"],
        "structure_support": reference["summary"]["structure_support"],
        "dG_le_minus_50": reference["summary"]["dG_le_minus_50"],
    }
    activity.heartbeat(
        {
            "stage": "scientific_transaction_committed",
            "candidate_id": thin_receipt["candidate_id"],
            "tool_call_id": thin_receipt["tool_call_id"],
        }
    )
    return thin_receipt


def _uuid(value: object, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"{field} must be a UUID") from error


__all__ = [
    "STRUCTURE_SCORE_SUMMARY_SCHEMA",
    "STRUCTURE_V2_DG_THRESHOLD_REU",
    "STRUCTURE_V2_SHARED_ROOT_ENV",
    "build_structure_score_reference",
    "canonical_structure_result_bytes",
    "persist_rosetta_evidence_v2",
    "preflight_structure_v2_target_request_v2",
    "score_rosetta_complex_v2",
]
