from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import ForeignKeyConstraint, String, Text, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.pool import NullPool
from temporalio.client import Client

from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    persist_operational_call,
)
from pepagent.db.models import (
    Artifact,
    Candidate,
    Evaluation,
    EvidenceArtifact,
    ExperimentRun,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.domain.enums import CandidateStatus, EvaluationStatus, MetricName
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore
from pepagent.structures.interface import (
    classify_structure_support,
    reconcile_ensemble_structure_support,
)

try:
    from pepagent.db.models import EvidenceArtifactLocation
except ImportError:
    from pepagent.db.base import Base

    class EvidenceArtifactLocation(Base):
        """Compatibility mapping for a DB migrated ahead of an inert worker release."""

        __tablename__ = "evidence_artifact_locations"
        __table_args__ = (
            ForeignKeyConstraint(
                ["tool_call_id", "artifact_id", "role"],
                [
                    "evidence_artifacts.tool_call_id",
                    "evidence_artifacts.artifact_id",
                    "evidence_artifacts.role",
                ],
                name="fk_evidence_artifact_location_edge",
            ),
        )

        tool_call_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
        artifact_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
        role: Mapped[str] = mapped_column(String(64), primary_key=True)
        location_witness_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
        requested_storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
        location_metadata_json: Mapped[dict[str, Any]] = mapped_column(
            JSONB,
            default=dict,
            nullable=False,
        )

STRUCTURE_SCORE_REFERENCE_SCHEMA = "ampgent.structure-score-reference.2"
STRUCTURE_REPAIR_RECEIPT_SCHEMA = "ampgent.structure-evidence-repair-receipt.1"
DEFAULT_TRANSACTION_TIMEOUT_SECONDS = 30.0
DEFAULT_STATEMENT_TIMEOUT_MS = 20_000
DEFAULT_LOCK_TIMEOUT_MS = 5_000

PersistArtifacts = Callable[
    [AsyncSession, uuid.UUID, Mapping[str, Any], "StructureEvidenceBinding"],
    Awaitable[int],
]


@dataclass(frozen=True)
class StructureEvidenceBinding:
    run_id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_sequence_sha256: str
    seed: int
    parent_tool_call_id: uuid.UUID
    interface_audit_tool_call_id: uuid.UUID | None
    tool_name: str
    tool_version: str
    environment_sha256: str
    weights_sha256: str | None
    model_uri: str | None
    input_sha256: str
    output_sha256: str
    result_sha256: str
    decoy_count: int
    primary_dg_separated_reu: float


@dataclass(frozen=True)
class StructureEvidenceReceipt:
    schema_version: Literal["ampgent.structure-evidence-repair-receipt.1"]
    run_id: str
    candidate_id: str
    tool_call_id: str
    idempotency_key: str
    reused_tool_call: bool
    evaluation_count: int
    artifact_edge_count: int
    dependency_count: int
    candidate_status: str
    result_sha256: str


@dataclass(frozen=True)
class ArtifactObservation:
    stored_payload: Mapping[str, Any]
    role: str
    metadata: dict[str, Any]


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a SHA-256 hex string")
    try:
        bytes.fromhex(value)
    except ValueError as error:
        raise ValueError(f"{field} must be a SHA-256 hex string") from error
    return value.lower()


def _require_uuid(value: object, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"{field} must be a UUID") from error


def _candidate_lock_id(candidate_id: uuid.UUID) -> int:
    digest = bytes.fromhex(
        sha256_json(
            {
                "lock_domain": "pepagent.structure_evidence_candidate.v1",
                "candidate_id": str(candidate_id),
            }
        )
    )
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _expected_tool_call_identity(
    run_id: uuid.UUID, result: Mapping[str, Any]
) -> dict[str, Any]:
    provenance = result["provenance"]
    input_payload = result["input"]
    parameters = result["parameters"]
    input_sha256 = sha256_json(input_payload)
    identity = {
        "run_id": run_id,
        "tool_name": provenance["tool_name"],
        "tool_version": provenance["tool_version"],
        "model_uri": provenance.get("model_uri"),
        "weights_sha256": provenance.get("weights_sha256"),
        "environment_sha256": provenance["environment_sha256"],
        "input_sha256": input_sha256,
        "input_json": input_payload,
        "parameters_json": parameters,
        "random_seed": int(input_payload["seed"]),
    }
    identity["idempotency_key"] = sha256_json(
        {
            "run_id": str(run_id),
            "tool_name": identity["tool_name"],
            "tool_version": identity["tool_version"],
            "environment_sha256": identity["environment_sha256"],
            "weights_sha256": identity["weights_sha256"],
            "input_sha256": input_sha256,
            "parameters": parameters,
            "random_seed": identity["random_seed"],
        }
    )
    return identity


def validate_structure_evidence_request(
    request: Mapping[str, Any],
    *,
    expected_run_id: uuid.UUID | None = None,
    expected_candidate_id: uuid.UUID | None = None,
    expected_seed: int | None = None,
) -> StructureEvidenceBinding:
    """Validate the scientific identity without adding environment-hash ceremony."""

    if not isinstance(request, Mapping):
        raise ValueError("structure evidence repair request must be an object")
    run_id = _require_uuid(request.get("run_id"), "run_id")
    if expected_run_id is not None and run_id != expected_run_id:
        raise ValueError("structure evidence repair run identity drifted")
    result = request.get("rosetta_result")
    if not isinstance(result, Mapping):
        raise ValueError("structure evidence repair request lacks a Rosetta result")
    candidate = result.get("candidate")
    input_payload = result.get("input")
    parameters = result.get("parameters")
    rosetta = result.get("rosetta")
    provenance = result.get("provenance")
    if not all(
        isinstance(item, Mapping)
        for item in (candidate, input_payload, parameters, rosetta, provenance)
    ):
        raise ValueError("Rosetta result lacks candidate/input/parameters/result/provenance")

    candidate_id = _require_uuid(candidate.get("id"), "candidate.id")
    if expected_candidate_id is not None and candidate_id != expected_candidate_id:
        raise ValueError("structure evidence repair candidate identity drifted")
    candidate_sequence_sha256 = _require_sha256(
        candidate.get("sequence_sha256"), "candidate.sequence_sha256"
    )
    seed = int(input_payload.get("seed"))
    if expected_seed is not None and seed != expected_seed:
        raise ValueError("structure evidence repair seed drifted")
    if int(rosetta.get("seed")) != seed:
        raise ValueError("Rosetta input and output seeds differ")

    parent_tool_call_id = _require_uuid(
        provenance.get("parent_tool_call_id"), "provenance.parent_tool_call_id"
    )
    interface_audit = provenance.get("interface_audit_tool_call_id")
    interface_audit_tool_call_id = (
        _require_uuid(interface_audit, "provenance.interface_audit_tool_call_id")
        if interface_audit
        else None
    )
    tool_name = str(provenance.get("tool_name") or "")
    tool_version = str(provenance.get("tool_version") or "")
    if not tool_name or not tool_version:
        raise ValueError("Rosetta provenance lacks tool identity")
    environment_sha256 = _require_sha256(
        provenance.get("environment_sha256"), "provenance.environment_sha256"
    )
    weights = provenance.get("weights_sha256")
    weights_sha256 = _require_sha256(weights, "provenance.weights_sha256") if weights else None
    raw_output = provenance.get("raw_output_artifact")
    environment_artifact = provenance.get("environment_artifact")
    engine_artifacts = provenance.get("engine_artifacts")
    if not isinstance(raw_output, Mapping) or not isinstance(environment_artifact, Mapping):
        raise ValueError("Rosetta provenance lacks persisted raw/environment artifact pointers")
    if not isinstance(engine_artifacts, list):
        raise ValueError("Rosetta provenance engine_artifacts must be a list")
    for field, artifact in (
        ("raw_output_artifact", raw_output),
        ("environment_artifact", environment_artifact),
    ):
        _validate_stored_object(artifact, f"provenance.{field}")
    for index, artifact in enumerate(engine_artifacts):
        if not isinstance(artifact, Mapping) or not artifact.get("path"):
            raise ValueError(f"provenance.engine_artifacts[{index}] is invalid")
        _validate_stored_object(artifact, f"provenance.engine_artifacts[{index}]")

    decoys = rosetta.get("decoys")
    if not isinstance(decoys, list) or not decoys:
        raise ValueError("Rosetta result lacks decoys")
    primary_dg = float(rosetta["primary_dG_separated_reu"])
    return StructureEvidenceBinding(
        run_id=run_id,
        candidate_id=candidate_id,
        candidate_sequence_sha256=candidate_sequence_sha256,
        seed=seed,
        parent_tool_call_id=parent_tool_call_id,
        interface_audit_tool_call_id=interface_audit_tool_call_id,
        tool_name=tool_name,
        tool_version=tool_version,
        environment_sha256=environment_sha256,
        weights_sha256=weights_sha256,
        model_uri=(str(provenance["model_uri"]) if provenance.get("model_uri") else None),
        input_sha256=sha256_json(dict(input_payload)),
        output_sha256=sha256_json(dict(rosetta)),
        result_sha256=sha256_json(dict(result)),
        decoy_count=len(decoys),
        primary_dg_separated_reu=primary_dg,
    )


def _validate_stored_object(artifact: Mapping[str, Any], field: str) -> None:
    expected = {"sha256", "size_bytes", "uri", "media_type"}
    if not expected.issubset(artifact):
        raise ValueError(f"{field} lacks a complete stored-object identity")
    _require_sha256(artifact.get("sha256"), f"{field}.sha256")
    if int(artifact.get("size_bytes", -1)) < 0:
        raise ValueError(f"{field}.size_bytes is invalid")
    if not str(artifact.get("uri") or "").startswith("s3://"):
        raise ValueError(f"{field}.uri is invalid")
    if not str(artifact.get("media_type") or ""):
        raise ValueError(f"{field}.media_type is invalid")


def _structure_artifact_observations(
    result: Mapping[str, Any],
    binding: StructureEvidenceBinding,
) -> list[ArtifactObservation]:
    provenance = result["provenance"]
    observations = [
        ArtifactObservation(
            stored_payload=provenance["raw_output_artifact"],
            role="raw_output",
            metadata={"tool": "rosetta", "candidate_id": str(binding.candidate_id)},
        ),
        ArtifactObservation(
            stored_payload=provenance["environment_artifact"],
            role="environment_manifest",
            metadata={"tool": "rosetta", "kind": "runtime_environment"},
        ),
    ]
    observations.extend(
        ArtifactObservation(
            stored_payload=artifact_payload,
            role=f"engine_output_{index}",
            metadata={
                "tool": "rosetta",
                "candidate_id": str(binding.candidate_id),
                "relative_path": artifact_payload["path"],
            },
        )
        for index, artifact_payload in enumerate(provenance["engine_artifacts"])
    )
    return observations


def _content_identity(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = metadata.get("content_identity")
    if value is not None and not isinstance(value, Mapping):
        raise ValueError("Artifact content_identity metadata must be an object")
    return value


def _validate_artifact_identity(
    artifact: Artifact,
    stored_payload: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> None:
    expected = {
        "sha256": stored_payload["sha256"],
        "size_bytes": int(stored_payload["size_bytes"]),
        "media_type": stored_payload["media_type"],
        "content_identity": _content_identity(metadata),
    }
    actual_metadata = (
        artifact.metadata_json if isinstance(artifact.metadata_json, Mapping) else {}
    )
    actual = {
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
        "media_type": artifact.media_type,
        "content_identity": _content_identity(actual_metadata),
    }
    mismatches = {
        key: {"expected": expected[key], "actual": actual[key]}
        for key in expected
        if expected[key] != actual[key]
    }
    if mismatches:
        raise ValueError(
            "content-addressed Artifact identity drifted for "
            f"{stored_payload['sha256']}: {json.dumps(mismatches, sort_keys=True)}"
        )


def _validate_duplicate_artifact_observation(
    first: ArtifactObservation,
    duplicate: ArtifactObservation,
) -> None:
    first_payload = first.stored_payload
    duplicate_payload = duplicate.stored_payload
    expected = {
        "size_bytes": int(first_payload["size_bytes"]),
        "media_type": first_payload["media_type"],
        "content_identity": _content_identity(first.metadata),
    }
    actual = {
        "size_bytes": int(duplicate_payload["size_bytes"]),
        "media_type": duplicate_payload["media_type"],
        "content_identity": _content_identity(duplicate.metadata),
    }
    if expected != actual:
        raise ValueError(
            "duplicate artifact SHA carries conflicting immutable identity: "
            f"{first_payload['sha256']}"
        )


async def persist_structure_artifacts_bulk(
    session: AsyncSession,
    tool_call_id: uuid.UUID,
    result: Mapping[str, Any],
    binding: StructureEvidenceBinding,
) -> int:
    """Persist all Rosetta artifacts with bounded, set-oriented database I/O."""

    observations = _structure_artifact_observations(result, binding)
    first_by_sha: dict[str, ArtifactObservation] = {}
    for observation in observations:
        digest = str(observation.stored_payload["sha256"])
        first = first_by_sha.setdefault(digest, observation)
        if first is not observation:
            _validate_duplicate_artifact_observation(first, observation)

    artifact_rows = [
        {
            "id": uuid.uuid4(),
            "sha256": digest,
            "size_bytes": int(observation.stored_payload["size_bytes"]),
            "media_type": observation.stored_payload["media_type"],
            "storage_uri": observation.stored_payload["uri"],
            "metadata_json": observation.metadata,
        }
        for digest, observation in first_by_sha.items()
    ]
    if artifact_rows:
        await session.execute(
            pg_insert(Artifact).values(artifact_rows).on_conflict_do_nothing()
        )
    artifacts = list(
        await session.scalars(
            select(Artifact).where(Artifact.sha256.in_(first_by_sha))
        )
    )
    artifacts_by_sha = {artifact.sha256: artifact for artifact in artifacts}
    missing_artifacts = sorted(set(first_by_sha) - set(artifacts_by_sha))
    if missing_artifacts:
        raise RuntimeError(
            "bulk artifact insert conflicted without a matching content identity: "
            f"{missing_artifacts[:5]}"
        )
    for digest, observation in first_by_sha.items():
        _validate_artifact_identity(
            artifacts_by_sha[digest],
            observation.stored_payload,
            observation.metadata,
        )

    edge_rows: list[dict[str, Any]] = []
    location_rows: list[dict[str, Any]] = []
    expected_locations: dict[
        tuple[uuid.UUID, str, str], tuple[str, dict[str, Any]]
    ] = {}
    for observation in observations:
        artifact = artifacts_by_sha[str(observation.stored_payload["sha256"])]
        edge_rows.append(
            {
                "tool_call_id": tool_call_id,
                "artifact_id": artifact.id,
                "role": observation.role,
            }
        )
        location_metadata = {
            key: value
            for key, value in observation.metadata.items()
            if key != "content_identity"
        }
        witness_payload = {
            "tool_call_id": str(tool_call_id),
            "artifact_id": str(artifact.id),
            "role": observation.role,
            "requested_storage_uri": observation.stored_payload["uri"],
            "location_metadata": location_metadata,
        }
        witness_sha256 = sha256_json(witness_payload)
        key = (artifact.id, observation.role, witness_sha256)
        expected_locations[key] = (
            str(observation.stored_payload["uri"]),
            location_metadata,
        )
        location_rows.append(
            {
                "tool_call_id": tool_call_id,
                "artifact_id": artifact.id,
                "role": observation.role,
                "location_witness_sha256": witness_sha256,
                "requested_storage_uri": observation.stored_payload["uri"],
                "location_metadata_json": location_metadata,
            }
        )

    if edge_rows:
        await session.execute(
            pg_insert(EvidenceArtifact)
            .values(edge_rows)
            .on_conflict_do_nothing(
                index_elements=[
                    EvidenceArtifact.tool_call_id,
                    EvidenceArtifact.artifact_id,
                    EvidenceArtifact.role,
                ]
            )
        )
    if location_rows:
        await session.execute(
            pg_insert(EvidenceArtifactLocation)
            .values(location_rows)
            .on_conflict_do_nothing(
                index_elements=[
                    EvidenceArtifactLocation.tool_call_id,
                    EvidenceArtifactLocation.artifact_id,
                    EvidenceArtifactLocation.role,
                    EvidenceArtifactLocation.location_witness_sha256,
                ]
            )
        )
    locations = list(
        await session.scalars(
            select(EvidenceArtifactLocation).where(
                EvidenceArtifactLocation.tool_call_id == tool_call_id,
                EvidenceArtifactLocation.role.in_(
                    observation.role for observation in observations
                ),
            )
        )
    )
    observed_locations = {
        (row.artifact_id, row.role, row.location_witness_sha256): row for row in locations
    }
    missing_locations = sorted(
        set(expected_locations) - set(observed_locations),
        key=lambda item: (str(item[0]), item[1], item[2]),
    )
    if missing_locations:
        raise RuntimeError(
            "bulk artifact location witnesses were not durably observable: "
            f"{missing_locations[:5]}"
        )
    for key, (expected_uri, expected_metadata) in expected_locations.items():
        location = observed_locations[key]
        if (
            location.requested_storage_uri != expected_uri
            or location.location_metadata_json != expected_metadata
        ):
            raise ValueError(f"artifact location witness identity drifted: {key}")
    return len(expected_locations)


async def load_structure_score_reference(
    reference: Mapping[str, Any],
    *,
    object_store: ContentAddressedObjectStore | None = None,
) -> dict[str, Any]:
    """Resolve a v2 CAS pointer and prove it names the bound score result."""

    if reference.get("schema_version") != STRUCTURE_SCORE_REFERENCE_SCHEMA:
        raise ValueError("structure score reference schema is invalid")
    artifact = reference.get("artifact")
    if not isinstance(artifact, Mapping):
        raise ValueError("structure score reference lacks an artifact")
    _validate_stored_object(artifact, "artifact")
    if artifact["media_type"] != "application/json":
        raise ValueError("structure score reference artifact is not JSON")
    store = object_store or ContentAddressedObjectStore()
    raw = await asyncio.to_thread(store.get_bytes, str(artifact["uri"]))
    if len(raw) != int(artifact["size_bytes"]) or sha256_bytes(raw) != artifact["sha256"]:
        raise ValueError("structure score reference artifact identity drifted")
    try:
        result = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("structure score reference artifact is not canonical JSON") from error
    if not isinstance(result, dict) or sha256_json(result) != reference.get("result_sha256"):
        raise ValueError("structure score reference payload identity drifted")
    candidate = result.get("candidate", {})
    input_payload = result.get("input", {})
    provenance = result.get("provenance", {})
    rosetta = result.get("rosetta", {})
    expected = {
        "run_id": str(reference.get("run_id")),
        "candidate_id": str(candidate.get("id")),
        "candidate_sequence_sha256": str(candidate.get("sequence_sha256")),
        "seed": int(input_payload.get("seed")),
        "parent_tool_call_id": str(provenance.get("parent_tool_call_id")),
        "interface_audit_tool_call_id": (
            str(provenance.get("interface_audit_tool_call_id"))
            if provenance.get("interface_audit_tool_call_id") is not None
            else None
        ),
        "tool_name": str(provenance.get("tool_name")),
        "tool_version": str(provenance.get("tool_version")),
        "tool_output_sha256": sha256_json(rosetta),
    }
    observed = {
        "run_id": str(reference.get("run_id")),
        "candidate_id": str(reference.get("candidate_id")),
        "candidate_sequence_sha256": str(
            reference.get("candidate_sequence_sha256")
        ),
        "seed": int(reference.get("seed")),
        "parent_tool_call_id": str(reference.get("parent_tool_call_id")),
        "interface_audit_tool_call_id": (
            str(reference.get("interface_audit_tool_call_id"))
            if reference.get("interface_audit_tool_call_id") is not None
            else None
        ),
        "tool_name": str(reference.get("tool_name")),
        "tool_version": str(reference.get("tool_version")),
        "tool_output_sha256": str(reference.get("tool_output_sha256")),
    }
    if expected != observed:
        raise ValueError("structure score reference binding differs from its payload")
    summary = reference.get("summary")
    if not isinstance(summary, Mapping):
        raise ValueError("structure score reference lacks its thin summary")
    support_label: str | None = None
    interface_audit = result.get("interface_audit")
    if isinstance(interface_audit, Mapping):
        sample_audits = interface_audit.get("sample_audits")
        if not isinstance(sample_audits, list) or not sample_audits:
            raise ValueError("structure score payload lacks interface-audit samples")
        representative = sample_audits[int(interface_audit["representative_index"])]
        thresholds = result["parameters"]["support_thresholds"]
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
        support_label = str(
            reconcile_ensemble_structure_support(
                support,
                interface_audit.get("gate_checks", {}),
            )["label"]
        )
    expected_summary = {
        "schema_version": "ampgent.structure-score-summary.2",
        "candidate_id": expected["candidate_id"],
        "seed": expected["seed"],
        "primary_dG_separated_reu": float(rosetta["primary_dG_separated_reu"]),
        "minimum_dG_separated_reu": float(rosetta["dG_separated_reu"]["minimum"]),
        "median_peptide_bb_rmsd_angstrom": float(
            rosetta["peptide_bb_rmsd_angstrom"]["median"]
        ),
        "decoy_count": len(rosetta["decoys"]),
        "artifact_observation_count": 2 + len(provenance["engine_artifacts"]),
        "structure_support": support_label,
        "dG_le_minus_50": float(rosetta["primary_dG_separated_reu"]) <= -50.0,
    }
    if dict(summary) != expected_summary:
        raise ValueError("structure score reference summary differs from its payload")
    return {"run_id": observed["run_id"], "rosetta_result": result}


async def load_temporal_activity_request(
    client: Client,
    *,
    workflow_id: str,
    temporal_run_id: str,
    activity_id: str,
    expected_activity_type: str = "persist_rosetta_evidence",
) -> dict[str, Any]:
    """Decode exactly one immutable ActivityTaskScheduled input from history."""

    handle = client.get_workflow_handle(workflow_id, run_id=temporal_run_id)
    description = await handle.describe()
    if description.run_id != temporal_run_id:
        raise ValueError("Temporal run identity drifted while loading repair input")
    matches: list[dict[str, Any]] = []
    async for event in handle.fetch_history_events():
        attributes = event.activity_task_scheduled_event_attributes
        if attributes.activity_id != activity_id:
            continue
        if attributes.activity_type.name != expected_activity_type:
            raise ValueError("Temporal activity type differs from the repair contract")
        decoded = await client.data_converter.decode(attributes.input.payloads)
        if len(decoded) != 1 or not isinstance(decoded[0], dict):
            raise ValueError("Temporal scheduled activity input is not one JSON object")
        matches.append(decoded[0])
    if len(matches) != 1:
        raise ValueError(
            f"expected one Temporal scheduled activity {activity_id}, found {len(matches)}"
        )
    request = matches[0]
    pointer = request.get("rosetta_result_pointer") or request.get("structure_score_reference")
    if pointer is not None:
        if not isinstance(pointer, Mapping):
            raise ValueError("Temporal structure score pointer is invalid")
        return await load_structure_score_reference(pointer)
    return request


async def _validate_database_binding(
    session: AsyncSession,
    *,
    binding: StructureEvidenceBinding,
    result: Mapping[str, Any],
) -> tuple[ExperimentRun, Candidate, ToolCall | None]:
    run = await session.get(ExperimentRun, binding.run_id)
    if run is None:
        raise KeyError(f"scientific run not found: {binding.run_id}")
    candidate = await session.get(Candidate, binding.candidate_id)
    if candidate is None or candidate.run_id != binding.run_id:
        raise ValueError("repair candidate is missing or belongs to another run")
    if candidate.sequence_sha256 != binding.candidate_sequence_sha256:
        raise ValueError("repair candidate sequence identity drifted")
    parent = await session.get(ToolCall, binding.parent_tool_call_id)
    if parent is None or parent.run_id != binding.run_id:
        raise ValueError("Rosetta parent ToolCall is missing or cross-run")
    if binding.interface_audit_tool_call_id is not None:
        audit = await session.get(ToolCall, binding.interface_audit_tool_call_id)
        if audit is None or audit.run_id != binding.run_id:
            raise ValueError("Rosetta interface-audit ToolCall is missing or cross-run")
    identity = _expected_tool_call_identity(binding.run_id, result)
    existing = await session.scalar(
        select(ToolCall).where(ToolCall.idempotency_key == identity["idempotency_key"])
    )
    if existing is not None:
        _validate_existing_tool_call(existing, identity, binding.output_sha256)
    return run, candidate, existing


def _validate_existing_tool_call(
    call: ToolCall, expected: Mapping[str, Any], output_sha256: str
) -> None:
    mismatches = {
        key: {"expected": value, "actual": getattr(call, key)}
        for key, value in expected.items()
        if getattr(call, key) != value
    }
    if call.status != EvaluationStatus.SUCCEEDED:
        mismatches["status"] = {
            "expected": EvaluationStatus.SUCCEEDED,
            "actual": call.status,
        }
    if call.output_sha256 != output_sha256:
        mismatches["output_sha256"] = {
            "expected": output_sha256,
            "actual": call.output_sha256,
        }
    if mismatches:
        raise ValueError(f"existing Rosetta ToolCall identity drifted: {mismatches}")


def _rosetta_metrics(result: Mapping[str, Any]) -> dict[MetricName, Any]:
    rosetta = result["rosetta"]
    return {
        MetricName.ROSETTA_DG_SEPARATED_REU: rosetta["primary_dG_separated_reu"],
        MetricName.ROSETTA_DG_MINIMUM_REU: rosetta["dG_separated_reu"]["minimum"],
        MetricName.ROSETTA_PEPTIDE_BB_RMSD_ANGSTROM: rosetta[
            "peptide_bb_rmsd_angstrom"
        ]["median"],
        MetricName.ROSETTA_INTERFACE_SCORE: rosetta["best_decoy"].get("interface_score"),
        MetricName.ROSETTA_REWEIGHTED_SCORE: rosetta["best_decoy"].get("reweighted_sc"),
        MetricName.ROSETTA_INTERFACE_HBONDS: rosetta["best_decoy"].get(
            "interface_hbonds"
        ),
        MetricName.ROSETTA_BURIED_SURFACE_AREA: rosetta["best_decoy"].get("dSASA_int"),
    }


def _metric_unit(metric_name: MetricName) -> str:
    if metric_name == MetricName.ROSETTA_PEPTIDE_BB_RMSD_ANGSTROM:
        return "angstrom"
    if metric_name == MetricName.ROSETTA_BURIED_SURFACE_AREA:
        return "angstrom^2"
    if metric_name == MetricName.ROSETTA_INTERFACE_HBONDS:
        return "count"
    return "REU"


async def persist_structure_evidence(
    session: AsyncSession,
    *,
    request: dict[str, Any],
    binding: StructureEvidenceBinding,
    persist_artifacts: PersistArtifacts | None = None,
) -> StructureEvidenceReceipt:
    """Persist the legacy scientific result exactly once under a candidate lock."""

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _candidate_lock_id(binding.candidate_id)},
    )
    result = request["rosetta_result"]
    provenance = result["provenance"]
    _, _, existing = await _validate_database_binding(
        session,
        binding=binding,
        result=result,
    )
    repository = ExperimentRepository(session)
    call = await repository.record_completed_tool_call(
        binding.run_id,
        provenance["tool_name"],
        provenance["tool_version"],
        provenance["environment_sha256"],
        result["input"],
        result["parameters"],
        result["rosetta"],
        weights_sha256=provenance.get("weights_sha256"),
        model_uri=provenance.get("model_uri"),
        random_seed=binding.seed,
        attempt=int(provenance["attempt"]),
    )
    expected_call = _expected_tool_call_identity(binding.run_id, result)
    _validate_existing_tool_call(call, expected_call, binding.output_sha256)
    await repository.record_tool_dependency(
        call.id,
        binding.parent_tool_call_id,
        "refines",
    )
    if binding.interface_audit_tool_call_id is not None:
        await repository.record_tool_dependency(
            call.id,
            binding.interface_audit_tool_call_id,
            "authorized_by_interface_audit",
        )

    artifact_writer = persist_artifacts or persist_structure_artifacts_bulk
    await artifact_writer(session, call.id, result, binding)

    rosetta = result["rosetta"]
    for metric_name, value in _rosetta_metrics(result).items():
        if value is None:
            continue
        await repository.record_evaluation(
            binding.candidate_id,
            call.id,
            metric_name,
            float(value),
            _metric_unit(metric_name),
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
            binding.candidate_id,
            call.id,
            MetricName.STRUCTURE_SUPPORT,
            None,
            None,
            {"structure_support": support, "interface_audit": interface_audit},
            text_value=support["label"],
            limitations=rosetta["limitations"],
        )
    await repository.transition_candidate(
        binding.candidate_id,
        CandidateStatus.ROSETTA_SCORED,
        "structure-evidence-repair",
        "Rosetta result recovered from immutable Temporal/CAS evidence",
    )
    await session.flush()
    evaluation_count = len(
        list(
            await session.scalars(
                select(Evaluation).where(Evaluation.tool_call_id == call.id)
            )
        )
    )
    artifact_edge_count = int(
        await session.scalar(
            text("SELECT count(*) FROM evidence_artifacts WHERE tool_call_id=:call_id"),
            {"call_id": call.id},
        )
        or 0
    )
    dependency_count = int(
        await session.scalar(
            text(
                "SELECT count(*) FROM tool_call_dependencies "
                "WHERE child_tool_call_id=:call_id"
            ),
            {"call_id": call.id},
        )
        or 0
    )
    candidate = await session.get(Candidate, binding.candidate_id)
    if candidate is None:
        raise RuntimeError("candidate disappeared during structure evidence repair")
    return StructureEvidenceReceipt(
        schema_version=STRUCTURE_REPAIR_RECEIPT_SCHEMA,
        run_id=str(binding.run_id),
        candidate_id=str(binding.candidate_id),
        tool_call_id=str(call.id),
        idempotency_key=call.idempotency_key,
        reused_tool_call=existing is not None,
        evaluation_count=evaluation_count,
        artifact_edge_count=artifact_edge_count,
        dependency_count=dependency_count,
        candidate_status=str(candidate.status),
        result_sha256=binding.result_sha256,
    )


async def inspect_structure_evidence(
    session: AsyncSession,
    *,
    request: dict[str, Any],
    binding: StructureEvidenceBinding,
) -> dict[str, Any]:
    run, candidate, existing = await _validate_database_binding(
        session,
        binding=binding,
        result=request["rosetta_result"],
    )
    evaluation_count = 0
    if existing is not None:
        evaluation_count = len(
            list(
                await session.scalars(
                    select(Evaluation).where(Evaluation.tool_call_id == existing.id)
                )
            )
        )
    target_key = str(
        run.spec_json.get("target_key")
        or run.spec_json.get("branch_key")
        or run.spec_json.get("workflow_spec", {}).get("target_key")
        or ""
    ).lower()
    return {
        "run_id": str(binding.run_id),
        "candidate_id": str(binding.candidate_id),
        "candidate_status": str(candidate.status),
        "target_key": target_key,
        "seed": binding.seed,
        "decoy_count": binding.decoy_count,
        "primary_dG_separated_reu": binding.primary_dg_separated_reu,
        "result_sha256": binding.result_sha256,
        "tool_output_sha256": binding.output_sha256,
        "existing_tool_call_id": str(existing.id) if existing is not None else None,
        "existing_evaluation_count": evaluation_count,
        "would_reuse_exact_tool_call": existing is not None,
    }


async def _configure_transaction(
    session: AsyncSession,
    *,
    statement_timeout_ms: int,
    lock_timeout_ms: int,
    read_only: bool,
) -> None:
    if read_only:
        await session.execute(text("SET TRANSACTION READ ONLY"))
    await session.execute(
        text("SELECT set_config('statement_timeout', :value, true)"),
        {"value": f"{statement_timeout_ms}ms"},
    )
    await session.execute(
        text("SELECT set_config('lock_timeout', :value, true)"),
        {"value": f"{lock_timeout_ms}ms"},
    )


def _repair_session_factory() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        get_settings().database_url,
        poolclass=NullPool,
        pool_pre_ping=True,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


def _operational_context(
    *,
    source: Mapping[str, Any],
    binding: StructureEvidenceBinding,
) -> dict[str, Any]:
    return {
        "host": platform.node(),
        "process_id": os.getpid(),
        "interface": "pepagent.structure_evidence_repair",
        "source": dict(source),
        "scientific_run_id": str(binding.run_id),
        "candidate_id": str(binding.candidate_id),
        "seed": binding.seed,
    }


async def _persist_operational_state(
    factory: async_sessionmaker[AsyncSession],
    *,
    record: OperationalCallRecord,
    transaction_timeout_seconds: float,
    statement_timeout_ms: int,
    lock_timeout_ms: int,
) -> tuple[str, str]:
    async with asyncio.timeout(transaction_timeout_seconds):
        async with factory() as session, session.begin():
            await _configure_transaction(
                session,
                statement_timeout_ms=statement_timeout_ms,
                lock_timeout_ms=lock_timeout_ms,
                read_only=False,
            )
            run, call = await persist_operational_call(session, record)
            return str(run.id), str(call.id)


async def execute_structure_evidence_repair(
    *,
    request: dict[str, Any],
    source: Mapping[str, Any],
    operation_key: str,
    execute: bool,
    expected_run_id: uuid.UUID | None,
    expected_candidate_id: uuid.UUID | None,
    expected_seed: int | None,
    transaction_timeout_seconds: float = DEFAULT_TRANSACTION_TIMEOUT_SECONDS,
    statement_timeout_ms: int = DEFAULT_STATEMENT_TIMEOUT_MS,
    lock_timeout_ms: int = DEFAULT_LOCK_TIMEOUT_MS,
    engine_and_factory: tuple[Any, async_sessionmaker[AsyncSession]] | None = None,
) -> dict[str, Any]:
    binding = validate_structure_evidence_request(
        request,
        expected_run_id=expected_run_id,
        expected_candidate_id=expected_candidate_id,
        expected_seed=expected_seed,
    )
    engine, factory = engine_and_factory or _repair_session_factory()
    started_at = datetime.now(UTC)
    try:
        async with asyncio.timeout(transaction_timeout_seconds):
            async with factory() as session, session.begin():
                await _configure_transaction(
                    session,
                    statement_timeout_ms=statement_timeout_ms,
                    lock_timeout_ms=lock_timeout_ms,
                    read_only=not execute,
                )
                inspection = await inspect_structure_evidence(
                    session,
                    request=request,
                    binding=binding,
                )
        target_key = inspection["target_key"]
        if target_key not in {"acea", "angpt1", "fgf2", "gyra", "pbp2a", "vegfa"}:
            raise ValueError(f"scientific run has unsupported target key: {target_key}")
        if not execute:
            return {
                "schema_version": STRUCTURE_REPAIR_RECEIPT_SCHEMA,
                "executed": False,
                "inert": True,
                "operation_key": operation_key,
                "source": dict(source),
                "validation": inspection,
            }

        context = _operational_context(source=source, binding=binding)
        operational_input = {
            "scientific_run_id": str(binding.run_id),
            "candidate_id": str(binding.candidate_id),
            "seed": binding.seed,
            "result_sha256": binding.result_sha256,
            "tool_output_sha256": binding.output_sha256,
        }
        operational_parameters = {
            "transaction_timeout_seconds": transaction_timeout_seconds,
            "statement_timeout_ms": statement_timeout_ms,
            "lock_timeout_ms": lock_timeout_ms,
            "candidate_advisory_lock": True,
            "pool_class": "NullPool",
        }
        running = OperationalCallRecord(
            operation_key=operation_key,
            target_key=target_key,
            purpose="rosetta",
            tool_name="structure-evidence-repair",
            tool_version="1",
            status="running",
            input_payload=operational_input,
            parameters=operational_parameters,
            execution_context=context,
            started_at=started_at,
            actor="structure-evidence-repair",
        )
        operational_run_id, operational_call_id = await _persist_operational_state(
            factory,
            record=running,
            transaction_timeout_seconds=transaction_timeout_seconds,
            statement_timeout_ms=statement_timeout_ms,
            lock_timeout_ms=lock_timeout_ms,
        )
        try:
            async with asyncio.timeout(transaction_timeout_seconds):
                async with factory() as session, session.begin():
                    await _configure_transaction(
                        session,
                        statement_timeout_ms=statement_timeout_ms,
                        lock_timeout_ms=lock_timeout_ms,
                        read_only=False,
                    )
                    receipt = await persist_structure_evidence(
                        session,
                        request=request,
                        binding=binding,
                    )
        except Exception as error:
            failed = OperationalCallRecord.model_validate(
                {
                    **running.model_dump(),
                    "status": "failed",
                    "error": {
                        "error_type": type(error).__name__,
                        "message": str(error)[:4000],
                    },
                    "finished_at": datetime.now(UTC),
                }
            )
            await _persist_operational_state(
                factory,
                record=failed,
                transaction_timeout_seconds=transaction_timeout_seconds,
                statement_timeout_ms=statement_timeout_ms,
                lock_timeout_ms=lock_timeout_ms,
            )
            raise
        terminal_payload = asdict(receipt)
        succeeded = OperationalCallRecord.model_validate(
            {
                **running.model_dump(),
                "status": "succeeded",
                "output_payload": terminal_payload,
                "finished_at": datetime.now(UTC),
            }
        )
        await _persist_operational_state(
            factory,
            record=succeeded,
            transaction_timeout_seconds=transaction_timeout_seconds,
            statement_timeout_ms=statement_timeout_ms,
            lock_timeout_ms=lock_timeout_ms,
        )
        return {
            "schema_version": STRUCTURE_REPAIR_RECEIPT_SCHEMA,
            "executed": True,
            "inert": False,
            "operation_key": operation_key,
            "source": dict(source),
            "scientific_receipt": terminal_payload,
            "operational_run_id": operational_run_id,
            "operational_tool_call_id": operational_call_id,
        }
    finally:
        if engine_and_factory is None:
            await engine.dispose()


async def _load_request_from_args(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if args.stdin:
        try:
            request = json.load(sys.stdin)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("stdin does not contain one JSON activity request") from error
        if not isinstance(request, dict):
            raise ValueError("stdin activity request must be one JSON object")
        return request, {"kind": "stdin_activity_request"}
    if args.pointer_json is not None:
        reference = json.loads(args.pointer_json.read_text(encoding="utf-8-sig"))
        request = await load_structure_score_reference(reference)
        return request, {
            "kind": "cas_pointer",
            "pointer_path": str(args.pointer_json.resolve()),
            "pointer_schema": reference.get("schema_version"),
        }
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    request = await load_temporal_activity_request(
        client,
        workflow_id=args.workflow_id,
        temporal_run_id=args.temporal_run_id,
        activity_id=args.activity_id,
        expected_activity_type=args.activity_type,
    )
    return request, {
        "kind": "temporal_activity_scheduled_input",
        "workflow_id": args.workflow_id,
        "temporal_run_id": args.temporal_run_id,
        "activity_id": args.activity_id,
        "activity_type": args.activity_type,
    }


async def _execute(args: argparse.Namespace) -> dict[str, Any]:
    request, source = await _load_request_from_args(args)
    return await execute_structure_evidence_repair(
        request=request,
        source=source,
        operation_key=args.operation_key,
        execute=bool(args.execute),
        expected_run_id=args.expected_run_id,
        expected_candidate_id=args.expected_candidate_id,
        expected_seed=args.expected_seed,
        transaction_timeout_seconds=args.transaction_timeout_seconds,
        statement_timeout_ms=args.statement_timeout_ms,
        lock_timeout_ms=args.lock_timeout_ms,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or exactly-once repair Rosetta evidence from a full activity request "
            "on stdin, an immutable Temporal scheduled input, or a v2 CAS pointer. The "
            "command is inert unless --execute is set."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--stdin",
        action="store_true",
        help="Read one complete persist_rosetta_evidence activity request as JSON.",
    )
    source.add_argument("--pointer-json", type=Path)
    source.add_argument("--workflow-id")
    parser.add_argument("--temporal-run-id")
    parser.add_argument("--activity-id", default="11")
    parser.add_argument("--activity-type", default="persist_rosetta_evidence")
    parser.add_argument("--expected-run-id", type=uuid.UUID, required=True)
    parser.add_argument("--expected-candidate-id", type=uuid.UUID, required=True)
    parser.add_argument("--expected-seed", type=int, required=True)
    parser.add_argument(
        "--operation-key",
        required=True,
        help="Stable key reused for an exact retry of this repair invocation.",
    )
    parser.add_argument("--transaction-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--statement-timeout-ms", type=int, default=20_000)
    parser.add_argument("--lock-timeout-ms", type=int, default=5_000)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write the scientific evidence and operational lifecycle; default is inert.",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.workflow_id and not args.temporal_run_id:
        raise ValueError("--temporal-run-id is required with --workflow-id")
    if (args.pointer_json is not None or args.stdin) and args.temporal_run_id:
        raise ValueError("--temporal-run-id is only valid with --workflow-id")
    if args.transaction_timeout_seconds <= 0:
        raise ValueError("transaction timeout must be positive")
    if args.statement_timeout_ms <= 0 or args.lock_timeout_ms <= 0:
        raise ValueError("SQL timeouts must be positive")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _validate_args(args)
    result = asyncio.run(_execute(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
