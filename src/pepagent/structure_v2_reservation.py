from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import sys
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode

from pepagent.autoresearch_structure_cohort import TARGET_KEYS
from pepagent.db.models import (
    Artifact,
    Candidate,
    EvidenceArtifact,
    EvidenceArtifactLocation,
    ExperimentRun,
    LifecycleEvent,
    Target,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.domain.enums import CandidateStatus, RunStatus
from pepagent.provenance.hashing import sha256_json, sha256_text
from pepagent.settings import get_settings
from pepagent.structure_v2_binding import (
    STRUCTURE_COHORT_IMPORT_TOOL,
    STRUCTURE_COHORT_IMPORT_VERSION,
    STRUCTURE_ESCALATION_RUN_MODE,
    STRUCTURE_V2_SOURCE_SNAPSHOT_SCHEMA,
    STRUCTURE_V2_WORKFLOW_TYPE,
    StructureV2PgEvidence,
    bind_structure_v2_request_from_pg_evidence,
)
from pepagent.workflows.structure_v2 import (
    STRUCTURE_V2_PERSIST_QUEUE,
    STRUCTURE_V2_RECEIPT_GOAL,
    STRUCTURE_V2_ROSETTA_QUEUE,
    STRUCTURE_V2_WORKFLOW_QUEUE,
    STRUCTURE_V2_WORKFLOW_TASK_TIMEOUT,
    structure_v2_receipt_contract,
    validate_structure_v2_target_request,
)

SOURCE_SCHEMA = "ampgent.structure-v2-strict-source.1"
RESERVATION_SCHEMA = "ampgent.structure-v2-inert-reservation.1"
RECEIPT_SCHEMA = "ampgent.structure-v2-inert-receipt.1"
ACTOR = "structure-v2-pg-only-reservation"
RESERVATION_POLICY = "scarcity_first_support_desc_instability_asc_global_family_unique"
RUN_NAMESPACE = uuid.UUID("3fdaf39c-5180-45f8-92aa-8482a451240d")
WORKFLOW_NAMESPACE = uuid.UUID("cc5a2faf-a6c8-4f35-a43c-2b2237f0b964")
ARTIFACT_NAMESPACE = uuid.UUID("f14ce379-d9d6-486d-8523-41ae031dc69f")
GLOBAL_LOCK_ID = int.from_bytes(
    bytes.fromhex(sha256_text("pepagent.structure-v2.global-reservation"))[:8],
    "big",
    signed=True,
)
_HEX = frozenset("0123456789abcdef")
_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")


@dataclass(frozen=True)
class StrictSource:
    content_address_key: str
    bundle_run_id: str
    bundle_created_at: str
    bundle_storage_uri: str
    bundle_receipt_sha256: str
    bundle_receipt_size_bytes: int
    manifest_sha256: str
    manifest_size_bytes: int
    strict_library_sha256: str
    strict_library_size_bytes: int


@dataclass(frozen=True)
class StrictRow:
    source_row_ordinal: int
    source_candidate_id: str
    sequence: str
    sequence_sha256: str
    family_key_80_80: str
    target_key: str
    strict_display_eligible: bool
    toxinpred3_label: str
    macrel_hemolysis_label: str
    guruprasad_instability_index: float
    guruprasad_instability_ood: bool
    activity_model_support_count: int
    source_result_sha256: str
    strict_library_row_sha256: str


@dataclass(frozen=True)
class BranchPlan:
    target_key: str
    predecessor_run_id: uuid.UUID
    target: Target
    workflow_spec: dict[str, Any]
    selected: tuple[StrictRow, ...]
    formal_submission_key: str
    run_id: uuid.UUID
    workflow_id: str
    run_spec: dict[str, Any]


def _is_sha256(value: Any) -> bool:
    text_value = str(value or "")
    return len(text_value) == 64 and not (set(text_value) - _HEX)


def _require_sha256(value: Any, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _is_sha256(normalized):
        raise ValueError(f"structure v2 source {field} is not a SHA-256 identity")
    return normalized


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"structure v2 source {field} must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"structure v2 source {field} must be a positive integer") from error
    if result < 1:
        raise ValueError(f"structure v2 source {field} must be a positive integer")
    return result


def _literal_bool(value: Any, *, expected: bool, field: str) -> bool:
    if value is not expected:
        raise ValueError(f"structure v2 source {field} must be literal {expected}")
    return expected


def _source_from_payload(payload: Mapping[str, Any]) -> StrictSource:
    if payload.get("schema_version") != SOURCE_SCHEMA:
        raise ValueError("structure v2 strict source schema differs")
    source = payload.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("structure v2 strict source metadata is missing")
    storage_uri = str(source.get("bundle_storage_uri", "")).strip()
    if not storage_uri.startswith("ssh://") or not storage_uri.endswith("/"):
        raise ValueError("structure v2 strict source is not an immutable remote CAS URI")
    content_address_key = _require_sha256(
        source.get("content_address_key"), "content_address_key"
    )
    if f"/{content_address_key}/" not in storage_uri:
        raise ValueError("structure v2 strict source CAS URI differs from its key")
    result = StrictSource(
        content_address_key=content_address_key,
        bundle_run_id=str(source.get("bundle_run_id", "")).strip(),
        bundle_created_at=str(source.get("bundle_created_at", "")).strip(),
        bundle_storage_uri=storage_uri,
        bundle_receipt_sha256=_require_sha256(
            source.get("bundle_receipt_sha256"), "bundle_receipt_sha256"
        ),
        bundle_receipt_size_bytes=_positive_int(
            source.get("bundle_receipt_size_bytes"), "bundle_receipt_size_bytes"
        ),
        manifest_sha256=_require_sha256(source.get("manifest_sha256"), "manifest_sha256"),
        manifest_size_bytes=_positive_int(
            source.get("manifest_size_bytes"), "manifest_size_bytes"
        ),
        strict_library_sha256=_require_sha256(
            source.get("strict_library_sha256"), "strict_library_sha256"
        ),
        strict_library_size_bytes=_positive_int(
            source.get("strict_library_size_bytes"), "strict_library_size_bytes"
        ),
    )
    if not result.bundle_run_id or not result.bundle_created_at:
        raise ValueError("structure v2 strict source bundle identity is incomplete")
    return result


def _row_from_payload(payload: Mapping[str, Any]) -> StrictRow:
    target_key = str(payload.get("target_key", "")).strip().lower()
    if target_key not in TARGET_KEYS or payload.get("target_key") != target_key:
        raise ValueError("structure v2 strict row target is missing or not normalized")
    sequence = "".join(str(payload.get("sequence", "")).split()).upper()
    if (
        not sequence
        or payload.get("sequence") != sequence
        or set(sequence) - _AMINO_ACIDS
        or sha256_text(sequence) != payload.get("sequence_sha256")
    ):
        raise ValueError("structure v2 strict row sequence identity differs")
    _literal_bool(
        payload.get("strict_display_eligible"),
        expected=True,
        field="strict_display_eligible",
    )
    _literal_bool(payload.get("valid_sequence"), expected=True, field="valid_sequence")
    toxin = str(payload.get("toxinpred3_label", "")).strip()
    if toxin.lower().replace("_", "-") not in {"non-toxin", "nontoxin"}:
        raise ValueError("structure v2 strict row fails the literal ToxinPred3 gate")
    hemolysis = str(payload.get("macrel_hemolysis_label", "")).strip()
    if hemolysis.lower() != "low":
        raise ValueError("structure v2 strict row fails the literal MACREL gate")
    instability = payload.get("guruprasad_instability_index")
    if isinstance(instability, bool) or not isinstance(instability, (int, float)):
        raise ValueError("structure v2 strict row lacks numeric Guruprasad instability")
    instability = float(instability)
    if not math.isfinite(instability) or instability >= 50.0:
        raise ValueError("structure v2 strict row fails the Guruprasad <50 gate")
    _literal_bool(
        payload.get("guruprasad_instability_ood"),
        expected=False,
        field="guruprasad_instability_ood",
    )
    support = payload.get("activity_model_support_count")
    if isinstance(support, bool) or not isinstance(support, int) or not 2 <= support <= 3:
        raise ValueError("structure v2 strict row lacks activity-model support >=2")
    family = str(payload.get("family_key_80_80", "")).strip()
    candidate_id = str(payload.get("source_candidate_id", "")).strip()
    if not family or not candidate_id:
        raise ValueError("structure v2 strict row lacks candidate/family identity")
    ordinal = _positive_int(payload.get("source_row_ordinal"), "source_row_ordinal")
    source_projection = {
        "source_row_ordinal": ordinal,
        "source_candidate_id": candidate_id,
        "sequence": sequence,
        "sequence_sha256": str(payload["sequence_sha256"]),
        "family_key_80_80": family,
        "target_key": target_key,
        "strict_display_eligible": True,
        "valid_sequence": True,
        "toxinpred3_label": toxin,
        "macrel_hemolysis_label": hemolysis,
        "guruprasad_instability_index": instability,
        "guruprasad_instability_ood": False,
        "activity_model_support_count": support,
        "source_result_sha256": _require_sha256(
            payload.get("source_result_sha256"), "source_result_sha256"
        ),
    }
    return StrictRow(
        **{key: value for key, value in source_projection.items() if key != "valid_sequence"},
        strict_library_row_sha256=sha256_json(source_projection),
    )


def parse_strict_source(payload: Mapping[str, Any]) -> tuple[StrictSource, tuple[StrictRow, ...]]:
    source = _source_from_payload(payload)
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, list):
        raise ValueError("structure v2 strict source rows must be a list")
    rows = tuple(_row_from_payload(row) for row in raw_rows if isinstance(row, Mapping))
    if len(rows) != len(raw_rows):
        raise ValueError("structure v2 strict source rows must be objects")
    if not rows:
        raise ValueError("structure v2 strict source has no qualified rows")
    return source, rows


def reservation_key(source: StrictSource) -> str:
    return sha256_json(
        {
            "schema_version": RESERVATION_SCHEMA,
            "source_content_address_key": source.content_address_key,
            "strict_library_sha256": source.strict_library_sha256,
            "per_target_candidate_count": STRUCTURE_V2_RECEIPT_GOAL,
            "target_keys": list(TARGET_KEYS),
            "selection_policy": RESERVATION_POLICY,
            "global_cross_target_sequence_uniqueness": True,
            "global_cross_target_family_uniqueness": True,
        }
    )


def select_fresh_rows(
    rows: Iterable[StrictRow],
    *,
    excluded_sequence_sha256s: set[str],
    excluded_family_keys: set[str],
) -> tuple[dict[str, tuple[StrictRow, ...]], dict[str, dict[str, int]], tuple[str, ...]]:
    by_target_family: dict[str, dict[str, StrictRow]] = {
        target_key: {} for target_key in TARGET_KEYS
    }
    for row in rows:
        if (
            row.sequence_sha256 in excluded_sequence_sha256s
            or row.family_key_80_80 in excluded_family_keys
        ):
            continue
        current = by_target_family[row.target_key].get(row.family_key_80_80)
        rank = (
            -row.activity_model_support_count,
            row.guruprasad_instability_index,
            row.source_row_ordinal,
            row.sequence_sha256,
        )
        if current is None:
            by_target_family[row.target_key][row.family_key_80_80] = row
            continue
        current_rank = (
            -current.activity_model_support_count,
            current.guruprasad_instability_index,
            current.source_row_ordinal,
            current.sequence_sha256,
        )
        if rank < current_rank:
            by_target_family[row.target_key][row.family_key_80_80] = row

    order = tuple(sorted(TARGET_KEYS, key=lambda key: (len(by_target_family[key]), key)))
    selected: dict[str, tuple[StrictRow, ...]] = {}
    stats: dict[str, dict[str, int]] = {}
    used_sequences: set[str] = set()
    used_families: set[str] = set()
    for target_key in order:
        candidates = sorted(
            (
                row
                for family, row in by_target_family[target_key].items()
                if family not in used_families and row.sequence_sha256 not in used_sequences
            ),
            key=lambda row: (
                -row.activity_model_support_count,
                row.guruprasad_instability_index,
                row.source_row_ordinal,
                row.sequence_sha256,
            ),
        )
        target_selection = tuple(candidates[:STRUCTURE_V2_RECEIPT_GOAL])
        stats[target_key] = {
            "fresh_eligible_family_count": len(by_target_family[target_key]),
            "available_after_cross_target_reservation": len(candidates),
            "selected_count": len(target_selection),
            "shortfall": max(0, STRUCTURE_V2_RECEIPT_GOAL - len(target_selection)),
        }
        if len(target_selection) != STRUCTURE_V2_RECEIPT_GOAL:
            raise ValueError(
                f"structure v2 {target_key} fresh-family shortfall="
                f"{STRUCTURE_V2_RECEIPT_GOAL - len(target_selection)}"
            )
        selected[target_key] = target_selection
        used_sequences.update(row.sequence_sha256 for row in target_selection)
        used_families.update(row.family_key_80_80 for row in target_selection)
    if len(used_sequences) != 6 * STRUCTURE_V2_RECEIPT_GOAL:
        raise ValueError("structure v2 reservation did not select 300 globally unique sequences")
    if len(used_families) != 6 * STRUCTURE_V2_RECEIPT_GOAL:
        raise ValueError("structure v2 reservation did not select 300 globally unique families")
    return selected, stats, order


def _structure_filter() -> Any:
    return or_(
        Candidate.metadata_json["run_mode"].as_string() == STRUCTURE_ESCALATION_RUN_MODE,
        ExperimentRun.spec_json["workflow_type"].as_string().in_(
            ("CandidateStructureValidationWorkflow", STRUCTURE_V2_WORKFLOW_TYPE)
        ),
        ExperimentRun.temporal_workflow_id.like("pepagent-structure-%"),
    )


async def _legacy_predecessors(session: AsyncSession) -> dict[str, tuple[ExperimentRun, Target]]:
    runs = list(
        await session.scalars(
            select(ExperimentRun).where(
                ExperimentRun.spec_json["workflow_type"].as_string()
                == "CandidateStructureValidationWorkflow",
                ExperimentRun.temporal_workflow_id.like("pepagent-structure-gold-v1-%"),
            )
        )
    )
    result: dict[str, tuple[ExperimentRun, Target]] = {}
    for run in runs:
        target_key = str(run.spec_json.get("target_key", "")).strip().lower()
        if target_key not in TARGET_KEYS:
            continue
        target = await session.get(Target, run.target_id)
        if target is None:
            raise ValueError(f"structure v2 predecessor target is missing: {target_key}")
        if target_key in result:
            raise ValueError(f"structure v2 predecessor is ambiguous: {target_key}")
        result[target_key] = (run, target)
    if set(result) != set(TARGET_KEYS):
        raise ValueError("structure v2 requires all six exact legacy predecessor runs")
    return result


def _workflow_spec(predecessor: ExperimentRun, target_key: str) -> dict[str, Any]:
    raw = predecessor.spec_json.get("workflow_spec")
    if not isinstance(raw, Mapping):
        raise ValueError(f"structure v2 {target_key} predecessor workflow spec is missing")
    spec = copy.deepcopy(dict(raw))
    spec.update(
        {
            "run_mode": STRUCTURE_ESCALATION_RUN_MODE,
            "target_key": target_key,
            "candidates_per_length": STRUCTURE_V2_RECEIPT_GOAL,
            "bulk_csv_report_threshold": STRUCTURE_V2_RECEIPT_GOAL,
            "bulk_evaluation_concurrency": 1,
            "rosetta_enabled": True,
            "rosetta_top_k": 1,
            "rosetta_nstruct": 200,
            "rosetta_all_boltz_samples": False,
        }
    )
    return spec


def _branch_plan(
    *,
    source: StrictSource,
    key: str,
    target_key: str,
    predecessor: ExperimentRun,
    target: Target,
    selected: Sequence[StrictRow],
) -> BranchPlan:
    workflow_spec = _workflow_spec(predecessor, target_key)
    identity = {
        "schema_version": RESERVATION_SCHEMA,
        "structure_v2_reservation_key": key,
        "source_content_address_key": source.content_address_key,
        "strict_library_sha256": source.strict_library_sha256,
        "target_key": target_key,
        "target_sequence_sha256": target.sequence_sha256,
        "candidate_sequence_sha256s": [row.sequence_sha256 for row in selected],
        "candidate_family_keys": [row.family_key_80_80 for row in selected],
        "workflow_spec": workflow_spec,
    }
    formal_submission_key = sha256_json(identity)
    run_id = uuid.uuid5(RUN_NAMESPACE, formal_submission_key)
    workflow_uuid = uuid.uuid5(WORKFLOW_NAMESPACE, formal_submission_key)
    workflow_id = f"pepagent-structure-v2-{target_key}-{workflow_uuid}"
    run_spec = {
        **identity,
        "run_id": str(run_id),
        "workflow_type": STRUCTURE_V2_WORKFLOW_TYPE,
        "workflow_id": workflow_id,
        "formal_submission_key": formal_submission_key,
        "cohort_id": key,
        "candidate_count": STRUCTURE_V2_RECEIPT_GOAL,
        "predecessor_run_id": str(predecessor.id),
        "predecessor_workflow_id": predecessor.temporal_workflow_id,
        "predecessor_reason": "versioned_thin_pointer_retry_safe_successor",
        "receipt_contract": structure_v2_receipt_contract(),
        "workflow_task_timeout_seconds": int(
            STRUCTURE_V2_WORKFLOW_TASK_TIMEOUT.total_seconds()
        ),
        "worker_queues": {
            "workflow": STRUCTURE_V2_WORKFLOW_QUEUE,
            "rosetta": STRUCTURE_V2_ROSETTA_QUEUE,
            "persist": STRUCTURE_V2_PERSIST_QUEUE,
        },
        "temporal_submission_performed": False,
        "authoritative_call_record": "postgresql",
    }
    return BranchPlan(
        target_key=target_key,
        predecessor_run_id=predecessor.id,
        target=target,
        workflow_spec=workflow_spec,
        selected=tuple(selected),
        formal_submission_key=formal_submission_key,
        run_id=run_id,
        workflow_id=workflow_id,
        run_spec=run_spec,
    )


async def _assert_temporal_absent(workflow_ids: Iterable[str]) -> None:
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
    )
    for workflow_id in workflow_ids:
        try:
            await client.get_workflow_handle(workflow_id).describe()
        except RPCError as error:
            if error.status == RPCStatusCode.NOT_FOUND:
                continue
            raise
        raise ValueError(f"structure v2 planned Temporal workflow already exists: {workflow_id}")


async def _ensure_artifact(
    session: AsyncSession,
    *,
    sha256: str,
    size_bytes: int,
    media_type: str,
    storage_uri: str,
    metadata: dict[str, Any],
) -> Artifact:
    matches = list(
        await session.scalars(
            select(Artifact).where(
                or_(Artifact.sha256 == sha256, Artifact.storage_uri == storage_uri)
            )
        )
    )
    if matches:
        if len(matches) != 1:
            raise ValueError("structure v2 CAS artifact identity is ambiguous")
        artifact = matches[0]
        if (
            artifact.sha256 != sha256
            or artifact.size_bytes != size_bytes
            or artifact.media_type != media_type
            or artifact.storage_uri != storage_uri
        ):
            raise ValueError("structure v2 CAS artifact identity differs")
        return artifact
    artifact = Artifact(
        id=uuid.uuid5(ARTIFACT_NAMESPACE, sha256),
        sha256=sha256,
        size_bytes=size_bytes,
        media_type=media_type,
        storage_uri=storage_uri,
        metadata_json=metadata,
    )
    session.add(artifact)
    await session.flush()
    return artifact


async def _source_artifacts(
    session: AsyncSession, source: StrictSource
) -> tuple[tuple[str, Artifact], ...]:
    base_metadata = {
        "schema_version": SOURCE_SCHEMA,
        "content_address_key": source.content_address_key,
        "bundle_run_id": source.bundle_run_id,
        "bundle_created_at": source.bundle_created_at,
    }
    rows = (
        (
            "source_strict_library",
            source.strict_library_sha256,
            source.strict_library_size_bytes,
            "text/csv",
            f"{source.bundle_storage_uri}library/strict_library_global.csv",
        ),
        (
            "source_bundle_receipt",
            source.bundle_receipt_sha256,
            source.bundle_receipt_size_bytes,
            "application/json",
            f"{source.bundle_storage_uri}bundle.receipt.json",
        ),
        (
            "source_manifest",
            source.manifest_sha256,
            source.manifest_size_bytes,
            "text/plain",
            f"{source.bundle_storage_uri}MANIFEST.sha256",
        ),
    )
    result: list[tuple[str, Artifact]] = []
    for role, digest, size, media_type, uri in rows:
        result.append(
            (
                role,
                await _ensure_artifact(
                    session,
                    sha256=digest,
                    size_bytes=size,
                    media_type=media_type,
                    storage_uri=uri,
                    metadata={**base_metadata, "role": role},
                ),
            )
        )
    return tuple(result)


async def _link_source_artifacts(
    session: AsyncSession,
    *,
    call: ToolCall,
    artifacts: Sequence[tuple[str, Artifact]],
) -> None:
    for role, artifact in artifacts:
        edge = await session.get(EvidenceArtifact, (call.id, artifact.id, role))
        if edge is None:
            session.add(
                EvidenceArtifact(tool_call_id=call.id, artifact_id=artifact.id, role=role)
            )
            await session.flush()
        witness = sha256_json(
            {
                "tool_call_id": str(call.id),
                "artifact_sha256": artifact.sha256,
                "role": role,
                "requested_storage_uri": artifact.storage_uri,
            }
        )
        location = await session.get(
            EvidenceArtifactLocation,
            (call.id, artifact.id, role, witness),
        )
        if location is None:
            session.add(
                EvidenceArtifactLocation(
                    tool_call_id=call.id,
                    artifact_id=artifact.id,
                    role=role,
                    location_witness_sha256=witness,
                    requested_storage_uri=artifact.storage_uri,
                    location_metadata_json={
                        "source": "published_global_strict_cas",
                        "content_address_key": artifact.metadata_json[
                            "content_address_key"
                        ],
                    },
                )
            )


def _candidate_metadata(
    *, source: StrictSource, key: str, row: StrictRow, rank: int
) -> dict[str, Any]:
    snapshot = {
        "schema_version": STRUCTURE_V2_SOURCE_SNAPSHOT_SCHEMA,
        "target_key": row.target_key,
        "sequence_sha256": row.sequence_sha256,
        "family_key_80_80": row.family_key_80_80,
        "cohort_sha256": key,
        "strict_display_eligible": True,
        "toxinpred3_label": row.toxinpred3_label,
        "macrel_hemolysis_label": row.macrel_hemolysis_label,
        "guruprasad_instability_index": row.guruprasad_instability_index,
        "guruprasad_instability_ood": False,
        "activity_model_support_count": row.activity_model_support_count,
        "strict_library_sha256": source.strict_library_sha256,
        "strict_library_row_sha256": row.strict_library_row_sha256,
        "source_candidate_id": row.source_candidate_id,
        "source_result_sha256": row.source_result_sha256,
    }
    return {
        "run_mode": STRUCTURE_ESCALATION_RUN_MODE,
        "target_key": row.target_key,
        "source_candidate_id": row.source_candidate_id,
        "source_result_sha256": row.source_result_sha256,
        "source_sequence_sha256": row.sequence_sha256,
        "family_key_80_80": row.family_key_80_80,
        "structure_rank": rank,
        "activity_model_support_count": row.activity_model_support_count,
        "guruprasad_instability_index": row.guruprasad_instability_index,
        "guruprasad_instability_ood": False,
        "minimum_rosetta_decoys": 200,
        "no_binding_or_affinity_claim": True,
        "structure_v2_eligibility": snapshot,
    }


async def _validate_branch_binding(
    session: AsyncSession,
    *,
    branch: BranchPlan,
    excluded_sequences: frozenset[str],
    excluded_families: frozenset[str],
) -> dict[str, Any]:
    candidates = tuple(
        await session.scalars(
            select(Candidate)
            .where(Candidate.run_id == branch.run_id)
            .order_by(Candidate.proposal_rank, Candidate.id)
        )
    )
    calls = tuple(
        await session.scalars(select(ToolCall).where(ToolCall.run_id == branch.run_id))
    )
    events = tuple(
        await session.scalars(
            select(LifecycleEvent).where(
                LifecycleEvent.aggregate_type == "candidate",
                LifecycleEvent.aggregate_id.in_([row.id for row in candidates]),
            )
        )
    )
    run = await session.get(ExperimentRun, branch.run_id)
    target = await session.get(Target, branch.target.id)
    if run is None or target is None:
        raise ValueError(f"structure v2 {branch.target_key} reservation disappeared")
    request = {
        "run_id": str(branch.run_id),
        "target_key": branch.target_key,
        "spec": branch.workflow_spec,
        "receipt_contract": structure_v2_receipt_contract(),
        "candidates": [
            {
                "id": str(row.id),
                "sequence": row.sequence,
                "sequence_sha256": row.sequence_sha256,
                "generation": row.generation,
                "target_key": branch.target_key,
                "family_key_80_80": row.metadata_json["family_key_80_80"],
            }
            for row in candidates
        ],
    }
    evidence = StructureV2PgEvidence(
        run=run,
        target=target,
        candidates=candidates,
        tool_calls=calls,
        lifecycle_events=events,
        legacy_sequence_sha256s=excluded_sequences,
        legacy_family_keys=excluded_families,
    )
    bound = bind_structure_v2_request_from_pg_evidence(request, evidence)
    validate_structure_v2_target_request(bound)
    return bound


async def _existing_reservation_runs(
    session: AsyncSession, key: str
) -> tuple[ExperimentRun, ...]:
    return tuple(
        await session.scalars(
            select(ExperimentRun).where(
                ExperimentRun.spec_json["structure_v2_reservation_key"].as_string() == key
            )
        )
    )


async def reserve_structure_v2_inert(
    source: StrictSource,
    rows: Sequence[StrictRow],
    *,
    execute: bool,
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    key = reservation_key(source)
    async with session_factory() as session, session.begin():
        await session.execute(text("SET LOCAL statement_timeout = '300s'"))
        await session.execute(text("SET LOCAL lock_timeout = '30s'"))
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": GLOBAL_LOCK_ID}
        )
        existing = await _existing_reservation_runs(session, key)
        if existing:
            if len(existing) != len(TARGET_KEYS):
                raise ValueError("structure v2 reservation is partially present")
            run_targets = {str(run.spec_json.get("target_key", "")): run for run in existing}
            if set(run_targets) != set(TARGET_KEYS):
                raise ValueError("structure v2 existing reservation target set differs")
            await _assert_temporal_absent(run.temporal_workflow_id for run in existing)
            return await _readback_receipt(
                session,
                source=source,
                key=key,
                runs=tuple(run_targets[target] for target in TARGET_KEYS),
                created=False,
            )

        excluded_rows = tuple(
            await session.execute(
                select(
                    Candidate.sequence_sha256,
                    Candidate.metadata_json["family_key_80_80"].as_string(),
                )
                .join(ExperimentRun, Candidate.run_id == ExperimentRun.id)
                .where(_structure_filter())
            )
        )
        excluded_sequences = {row[0] for row in excluded_rows if row[0]}
        excluded_families = {row[1] for row in excluded_rows if row[1]}
        selected, preflight_stats, selection_order = select_fresh_rows(
            rows,
            excluded_sequence_sha256s=excluded_sequences,
            excluded_family_keys=excluded_families,
        )
        predecessors = await _legacy_predecessors(session)
        branches = tuple(
            _branch_plan(
                source=source,
                key=key,
                target_key=target_key,
                predecessor=predecessors[target_key][0],
                target=predecessors[target_key][1],
                selected=selected[target_key],
            )
            for target_key in TARGET_KEYS
        )
        await _assert_temporal_absent(branch.workflow_id for branch in branches)
        plan = {
            "schema_version": RECEIPT_SCHEMA,
            "status": "ready_inert" if execute else "preflight_inert",
            "reservation_key": key,
            "source_content_address_key": source.content_address_key,
            "strict_library_sha256": source.strict_library_sha256,
            "historical_structure_exclusion": {
                "candidate_rows": len(excluded_rows),
                "distinct_sequences": len(excluded_sequences),
                "distinct_families": len(excluded_families),
            },
            "selection_order": list(selection_order),
            "preflight": preflight_stats,
            "global_selected_sequences": 6 * STRUCTURE_V2_RECEIPT_GOAL,
            "global_selected_families": 6 * STRUCTURE_V2_RECEIPT_GOAL,
            "temporal_submission_performed": False,
            "branches": [
                {
                    "target_key": branch.target_key,
                    "run_id": str(branch.run_id),
                    "workflow_id": branch.workflow_id,
                    "predecessor_run_id": str(branch.predecessor_run_id),
                    "candidate_count": len(branch.selected),
                }
                for branch in branches
            ],
        }
        if not execute:
            return plan

        artifacts = await _source_artifacts(session, source)
        repository = ExperimentRepository(session)
        created_runs: list[ExperimentRun] = []
        for branch in branches:
            collision = await session.scalar(
                select(ExperimentRun.id).where(
                    or_(
                        ExperimentRun.id == branch.run_id,
                        ExperimentRun.formal_submission_key == branch.formal_submission_key,
                        ExperimentRun.temporal_workflow_id == branch.workflow_id,
                    )
                )
            )
            if collision is not None:
                raise ValueError(
                    f"structure v2 {branch.target_key} deterministic identity collides"
                )
            run = ExperimentRun(
                id=branch.run_id,
                target_id=branch.target.id,
                spec_json=branch.run_spec,
                spec_sha256=sha256_json(branch.run_spec),
                formal_submission_key=branch.formal_submission_key,
                status=RunStatus.CREATED,
                temporal_workflow_id=branch.workflow_id,
                temporal_run_id=None,
                parent_run_id=branch.predecessor_run_id,
            )
            session.add(run)
            await session.flush()
            await repository.append_event(
                "run",
                run.id,
                "structure.v2.pg_reserved",
                ACTOR,
                {
                    "reservation_key": key,
                    "target_key": branch.target_key,
                    "candidate_count": STRUCTURE_V2_RECEIPT_GOAL,
                    "workflow_id": branch.workflow_id,
                    "temporal_submission_performed": False,
                },
            )
            import_call = await repository.record_completed_tool_call(
                run.id,
                STRUCTURE_COHORT_IMPORT_TOOL,
                STRUCTURE_COHORT_IMPORT_VERSION,
                sha256_json({"actor": ACTOR, "storage": "postgresql"}),
                {
                    "cohort_id": key,
                    "target_key": branch.target_key,
                    "candidate_count": STRUCTURE_V2_RECEIPT_GOAL,
                    "source_content_address_key": source.content_address_key,
                    "strict_library_sha256": source.strict_library_sha256,
                },
                {
                    "selection_policy": RESERVATION_POLICY,
                    "history_sequence_exclusion": True,
                    "history_family_exclusion": True,
                    "cross_target_sequence_uniqueness": True,
                    "cross_target_family_uniqueness": True,
                    "literal_safety_and_support_fields_only": True,
                },
                {
                    "candidate_identities": [
                        {
                            "source_candidate_id": row.source_candidate_id,
                            "sequence_sha256": row.sequence_sha256,
                            "family_key_80_80": row.family_key_80_80,
                            "strict_library_row_sha256": row.strict_library_row_sha256,
                        }
                        for row in branch.selected
                    ]
                },
                model_uri=(
                    f"{source.bundle_storage_uri}library/strict_library_global.csv"
                ),
            )
            await _link_source_artifacts(session, call=import_call, artifacts=artifacts)
            for rank, source_row in enumerate(branch.selected, start=1):
                candidate = await repository.add_candidate(
                    run.id,
                    source_row.sequence,
                    generation=0,
                    proposal_rank=rank,
                    generator_call_id=import_call.id,
                    metadata=_candidate_metadata(
                        source=source,
                        key=key,
                        row=source_row,
                        rank=rank,
                    ),
                    actor=ACTOR,
                )
                await repository.transition_candidate(
                    candidate.id,
                    CandidateStatus.STRUCTURE_QUEUED,
                    ACTOR,
                    "frozen fresh strict-library family queued for inert structure v2",
                )
            created_runs.append(run)

        new_run_ids = {branch.run_id for branch in branches}
        other_structure_rows = tuple(
            await session.execute(
                select(
                    Candidate.sequence_sha256,
                    Candidate.metadata_json["family_key_80_80"].as_string(),
                )
                .join(ExperimentRun, Candidate.run_id == ExperimentRun.id)
                .where(ExperimentRun.id.not_in(new_run_ids), _structure_filter())
            )
        )
        other_sequences = frozenset(row[0] for row in other_structure_rows if row[0])
        other_families = frozenset(row[1] for row in other_structure_rows if row[1])
        bound_requests = []
        for branch in branches:
            bound_requests.append(
                await _validate_branch_binding(
                    session,
                    branch=branch,
                    excluded_sequences=other_sequences,
                    excluded_families=other_families,
                )
            )
        selected_sequences = {
            item["sequence_sha256"]
            for request in bound_requests
            for item in request["candidates"]
        }
        selected_families = {
            item["family_key_80_80"]
            for request in bound_requests
            for item in request["candidates"]
        }
        if selected_sequences & other_sequences or selected_families & other_families:
            raise ValueError("structure v2 reservation intersects existing structure work")
        if len(selected_sequences) != 300 or len(selected_families) != 300:
            raise ValueError("structure v2 reservation global identity counts differ")
        for branch, run in zip(branches, created_runs, strict=True):
            await repository.append_event(
                "run",
                run.id,
                "structure.v2.ready_inert",
                ACTOR,
                {
                    "reservation_key": key,
                    "target_key": branch.target_key,
                    "candidate_count": STRUCTURE_V2_RECEIPT_GOAL,
                    "distinct_family_count": STRUCTURE_V2_RECEIPT_GOAL,
                    "workflow_id": branch.workflow_id,
                    "workflow_task_timeout_seconds": int(
                        STRUCTURE_V2_WORKFLOW_TASK_TIMEOUT.total_seconds()
                    ),
                    "worker_queues": branch.run_spec["worker_queues"],
                    "temporal_workflow_absent": True,
                    "temporal_submission_performed": False,
                },
            )
        await session.flush()
        return await _readback_receipt(
            session,
            source=source,
            key=key,
            runs=tuple(created_runs),
            created=True,
        )


async def _readback_receipt(
    session: AsyncSession,
    *,
    source: StrictSource,
    key: str,
    runs: Sequence[ExperimentRun],
    created: bool,
) -> dict[str, Any]:
    run_ids = {run.id for run in runs}
    candidates = tuple(
        await session.scalars(select(Candidate).where(Candidate.run_id.in_(run_ids)))
    )
    calls = tuple(await session.scalars(select(ToolCall).where(ToolCall.run_id.in_(run_ids))))
    candidate_ids = {row.id for row in candidates}
    events = tuple(
        await session.scalars(
            select(LifecycleEvent).where(
                or_(
                    (
                        (LifecycleEvent.aggregate_type == "candidate")
                        & LifecycleEvent.aggregate_id.in_(candidate_ids)
                    ),
                    (
                        (LifecycleEvent.aggregate_type == "run")
                        & LifecycleEvent.aggregate_id.in_(run_ids)
                    ),
                )
            )
        )
    )
    artifact_edges = tuple(
        await session.scalars(
            select(EvidenceArtifact).where(
                EvidenceArtifact.tool_call_id.in_([call.id for call in calls])
            )
        )
    )
    artifact_locations = tuple(
        await session.scalars(
            select(EvidenceArtifactLocation).where(
                EvidenceArtifactLocation.tool_call_id.in_([call.id for call in calls])
            )
        )
    )
    by_target: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_target[str(candidate.metadata_json.get("target_key", ""))].append(candidate)
    selected_sequences = {row.sequence_sha256 for row in candidates}
    selected_families = {
        str(row.metadata_json.get("family_key_80_80", "")) for row in candidates
    }
    other_rows = tuple(
        await session.execute(
            select(
                Candidate.sequence_sha256,
                Candidate.metadata_json["family_key_80_80"].as_string(),
            )
            .join(ExperimentRun, Candidate.run_id == ExperimentRun.id)
            .where(ExperimentRun.id.not_in(run_ids), _structure_filter())
        )
    )
    other_sequences = {row[0] for row in other_rows if row[0]}
    other_families = {row[1] for row in other_rows if row[1]}
    branches = []
    for run in sorted(runs, key=lambda item: str(item.spec_json.get("target_key", ""))):
        target_key = str(run.spec_json.get("target_key", ""))
        target_candidates = by_target[target_key]
        target_calls = [call for call in calls if call.run_id == run.id]
        branches.append(
            {
                "target_key": target_key,
                "run_id": str(run.id),
                "workflow_id": run.temporal_workflow_id,
                "temporal_run_id": run.temporal_run_id,
                "run_status": str(run.status),
                "candidate_count": len(target_candidates),
                "distinct_sequence_count": len(
                    {row.sequence_sha256 for row in target_candidates}
                ),
                "distinct_family_count": len(
                    {
                        row.metadata_json.get("family_key_80_80")
                        for row in target_candidates
                    }
                ),
                "eligibility_snapshot_count": sum(
                    "structure_v2_eligibility" in row.metadata_json
                    for row in target_candidates
                ),
                "cohort_import_tool_call_count": sum(
                    call.tool_name == STRUCTURE_COHORT_IMPORT_TOOL
                    and str(call.status) == "succeeded"
                    for call in target_calls
                ),
            }
        )
    generated_events = sum(event.event_type == "candidate.generated" for event in events)
    queued_events = sum(event.event_type == "candidate.status_changed" for event in events)
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "status": "ready_inert",
        "created": created,
        "reservation_key": key,
        "source_content_address_key": source.content_address_key,
        "strict_library_sha256": source.strict_library_sha256,
        "run_count": len(runs),
        "candidate_count": len(candidates),
        "distinct_sequence_count": len(selected_sequences),
        "distinct_family_count": len(selected_families),
        "cohort_import_tool_call_count": sum(
            call.tool_name == STRUCTURE_COHORT_IMPORT_TOOL for call in calls
        ),
        "candidate_generated_event_count": generated_events,
        "candidate_structure_queued_event_count": queued_events,
        "source_artifact_edge_count": len(artifact_edges),
        "source_artifact_location_count": len(artifact_locations),
        "existing_structure_sequence_intersection_count": len(
            selected_sequences & other_sequences
        ),
        "existing_structure_family_intersection_count": len(
            selected_families & other_families
        ),
        "temporal_submission_performed": False,
        "branches": branches,
    }
    expected = {
        "run_count": 6,
        "candidate_count": 300,
        "distinct_sequence_count": 300,
        "distinct_family_count": 300,
        "cohort_import_tool_call_count": 6,
        "candidate_generated_event_count": 300,
        "candidate_structure_queued_event_count": 300,
        "source_artifact_edge_count": 18,
        "source_artifact_location_count": 18,
        "existing_structure_sequence_intersection_count": 0,
        "existing_structure_family_intersection_count": 0,
    }
    for field, value in expected.items():
        if receipt[field] != value:
            raise ValueError(
                f"structure v2 inert readback {field}={receipt[field]} expected={value}"
            )
    if any(
        branch["candidate_count"] != 50
        or branch["distinct_sequence_count"] != 50
        or branch["distinct_family_count"] != 50
        or branch["eligibility_snapshot_count"] != 50
        or branch["cohort_import_tool_call_count"] != 1
        or branch["temporal_run_id"] is not None
        or branch["run_status"] != "created"
        for branch in branches
    ):
        raise ValueError("structure v2 inert branch readback differs")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reserve a six-target Structure v2 cohort in PostgreSQL without "
            "Temporal submission"
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="commit the PG-only reservation; otherwise run a read-only inert preflight",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, Mapping):
        raise ValueError("structure v2 strict source document must be an object")
    source, rows = parse_strict_source(payload)
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        poolclass=NullPool,
        pool_pre_ping=True,
        isolation_level="SERIALIZABLE",
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        return await reserve_structure_v2_inert(
            source,
            rows,
            execute=bool(args.execute),
            session_factory=factory,
        )
    finally:
        await engine.dispose()


def main() -> None:
    result = asyncio.run(_run(_parser().parse_args()))
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()


__all__ = [
    "BranchPlan",
    "StrictRow",
    "StrictSource",
    "parse_strict_source",
    "reservation_key",
    "reserve_structure_v2_inert",
    "select_fresh_rows",
]
