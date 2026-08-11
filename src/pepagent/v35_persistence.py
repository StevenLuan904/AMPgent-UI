from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import (
    AgentDecision,
    AgentDecisionToolCallEdge,
    Artifact,
    ExperimentRun,
    Target,
    TargetPanelSelectionMember,
    TargetPanelSelectionWitness,
    TargetPocket,
    TargetQualificationAudit,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.provenance.hashing import sha256_bytes
from pepagent.v35_multitarget import (
    SCHEMA_VERSION,
    SELECTION_METHOD,
    TargetAuditItem,
    TargetQualificationSnapshot,
    canonical_json_bytes,
    verify_target_qualification_snapshot,
)

AUDIT_ARTIFACT_ID_FIELDS = {
    "sequence_artifact_id": "sequence_artifact_sha256",
    "source_manifest_artifact_id": "source_manifest_sha256",
    "feature_evidence_artifact_id": "feature_evidence_sha256",
    "coordinate_artifact_id": "coordinate_sha256",
    "structure_validation_artifact_id": "structure_validation_sha256",
    "sequence_structure_mapping_artifact_id": "sequence_structure_mapping_sha256",
    "primary_pocket_definition_artifact_id": "primary_pocket_definition_sha256",
    "wrong_pocket_definition_artifact_id": "wrong_pocket_definition_sha256",
}
REQUIRED_AUDIT_ARTIFACT_FIELDS = {
    "sequence_artifact_id",
    "source_manifest_artifact_id",
    "feature_evidence_artifact_id",
}


def _artifact_digest(
    artifacts_by_id: Mapping[uuid.UUID, Artifact], artifact_id: uuid.UUID | None
) -> str | None:
    if artifact_id is None:
        return None
    artifact = artifacts_by_id.get(artifact_id)
    if artifact is None:
        raise KeyError(f"typed target evidence artifact not found: {artifact_id}")
    return artifact.sha256


def audit_row_to_item(
    row: TargetQualificationAudit,
    artifacts_by_id: Mapping[uuid.UUID, Artifact],
) -> TargetAuditItem:
    return TargetAuditItem.model_validate(
        {
            "shortlist_order": row.shortlist_order,
            "target_key": row.target_key,
            "organism_and_strain": row.organism_and_strain,
            "sequence_accession": row.sequence_accession,
            "sequence_entry_version": row.sequence_entry_version,
            "sequence_admission_basis": row.sequence_admission_basis,
            "sequence_sha256": row.sequence_sha256,
            "sequence_artifact_sha256": _artifact_digest(
                artifacts_by_id, row.sequence_artifact_id
            ),
            "source_manifest_sha256": _artifact_digest(
                artifacts_by_id, row.source_manifest_artifact_id
            ),
            "feature_evidence_sha256": _artifact_digest(
                artifacts_by_id, row.feature_evidence_artifact_id
            ),
            "structure_source_type": row.structure_source_type,
            "coordinate_sha256": _artifact_digest(
                artifacts_by_id, row.coordinate_artifact_id
            ),
            "structure_validation_sha256": _artifact_digest(
                artifacts_by_id, row.structure_validation_artifact_id
            ),
            "sequence_structure_mapping_sha256": _artifact_digest(
                artifacts_by_id, row.sequence_structure_mapping_artifact_id
            ),
            "primary_pocket_grade": row.primary_pocket_grade,
            "primary_pocket_definition_sha256": _artifact_digest(
                artifacts_by_id, row.primary_pocket_definition_artifact_id
            ),
            "wrong_pocket_definition_sha256": _artifact_digest(
                artifacts_by_id, row.wrong_pocket_definition_artifact_id
            ),
            "audit_status": row.audit_status,
            "rejection_reasons": row.rejection_reasons_json,
            "diversity_vector": row.diversity_vector_json,
        }
    )


def build_target_qualification_snapshot_from_typed_rows(
    audits: Sequence[TargetQualificationAudit],
    witness: TargetPanelSelectionWitness,
    members: Sequence[TargetPanelSelectionMember],
    artifacts_by_id: Mapping[uuid.UUID, Artifact],
) -> dict[str, Any]:
    ordered_audits = sorted(audits, key=lambda row: row.shortlist_order)
    if not ordered_audits:
        raise ValueError("typed target qualification ledger is empty")
    if any(row.audit_scope_id != witness.audit_scope_id for row in ordered_audits):
        raise ValueError("typed target audit row escaped the selection scope")
    if any(row.schema_version != SCHEMA_VERSION for row in ordered_audits):
        raise ValueError("typed target audit schema version drifted")
    audit_by_id = {row.id: row for row in ordered_audits}
    ordered_members = sorted(members, key=lambda row: row.selection_rank)
    if [row.selection_rank for row in ordered_members] != list(
        range(1, len(ordered_members) + 1)
    ):
        raise ValueError("typed target panel member ranks are not contiguous")
    if any(row.selection_witness_id != witness.id for row in ordered_members):
        raise ValueError("typed target panel member escaped its witness")
    try:
        selected_target_keys = [
            audit_by_id[row.target_audit_id].target_key for row in ordered_members
        ]
    except KeyError as error:
        raise ValueError(
            "typed target panel member references an audit outside the scope"
        ) from error
    payload = {
        "schema_version": witness.schema_version,
        "audit_scope_id": witness.audit_scope_id,
        "target_names_selected_before_audit": witness.target_names_selected_before_audit,
        "peptide_or_structure_outcomes_used_for_selection": (
            witness.peptide_or_structure_outcomes_used_for_selection
        ),
        "target_agnostic_amp_lane_retained": witness.target_agnostic_amp_lane_retained,
        "acea_anchor_vector": witness.acea_anchor_vector_json,
        "requested_new_target_count": witness.requested_new_target_count,
        "shortlist": [
            audit_row_to_item(row, artifacts_by_id).model_dump(mode="json")
            for row in ordered_audits
        ],
        "selected_target_keys": selected_target_keys,
        "selection_method": witness.selection_method,
        "selection_witness_sha256": _artifact_digest(
            artifacts_by_id, witness.selection_witness_artifact_id
        ),
    }
    return TargetQualificationSnapshot.model_validate(payload).model_dump(mode="json")


def verify_target_qualification_database_projection(
    audits: Sequence[TargetQualificationAudit],
    witness: TargetPanelSelectionWitness,
    members: Sequence[TargetPanelSelectionMember],
    artifacts_by_id: Mapping[uuid.UUID, Artifact],
    artifact_payloads: Mapping[str, bytes],
) -> dict[str, Any]:
    snapshot = build_target_qualification_snapshot_from_typed_rows(
        audits, witness, members, artifacts_by_id
    )
    for artifact in artifacts_by_id.values():
        raw = artifact_payloads.get(artifact.sha256)
        if raw is None or len(raw) != artifact.size_bytes or sha256_bytes(raw) != artifact.sha256:
            raise ValueError("typed target replay artifact metadata differs from object bytes")
    anchor = canonical_json_bytes(
        {
            "schema_version": SCHEMA_VERSION,
            "audit_scope_id": witness.audit_scope_id,
            "acea_anchor_vector": witness.acea_anchor_vector_json,
        }
    )
    anchor_sha = _artifact_digest(artifacts_by_id, witness.acea_anchor_artifact_id)
    if anchor_sha is None or artifact_payloads.get(anchor_sha) != anchor:
        raise ValueError("typed target selection anchor differs from immutable artifact")
    snapshot_sha = _artifact_digest(artifacts_by_id, witness.snapshot_artifact_id)
    if snapshot_sha is None or artifact_payloads.get(snapshot_sha) != canonical_json_bytes(
        snapshot
    ):
        raise ValueError("typed target qualification snapshot differs from immutable artifact")
    receipt = verify_target_qualification_snapshot(snapshot, dict(artifact_payloads))
    receipt.update(
        {
            "typed_audit_count": len(audits),
            "typed_member_count": len(members),
            "selection_witness_id": str(witness.id),
            "database_projection_exact": True,
        }
    )
    return receipt


class TargetQualificationRepository:
    """Retry-safe writes for v35 audit and panel-selection evidence."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.events = ExperimentRepository(session)

    async def _require_artifacts(
        self, artifact_ids: Sequence[uuid.UUID | None]
    ) -> dict[uuid.UUID, Artifact]:
        required = {artifact_id for artifact_id in artifact_ids if artifact_id is not None}
        artifacts = {
            artifact.id: artifact
            for artifact in await self.session.scalars(
                select(Artifact).where(Artifact.id.in_(required))
            )
        }
        if set(artifacts) != required:
            raise KeyError("one or more target qualification artifacts do not exist")
        return artifacts

    async def _require_decision_tool_edge(
        self, decision_id: uuid.UUID, tool_call_id: uuid.UUID
    ) -> None:
        edge = await self.session.scalar(
            select(AgentDecisionToolCallEdge).where(
                AgentDecisionToolCallEdge.decision_id == decision_id,
                AgentDecisionToolCallEdge.tool_call_id == tool_call_id,
            )
        )
        if edge is None:
            raise ValueError("target qualification decision is detached from its ToolCall")

    async def record_audit(
        self,
        item: TargetAuditItem,
        *,
        audit_scope_id: str,
        target_id: uuid.UUID,
        audit_run_id: uuid.UUID,
        audit_tool_call_id: uuid.UUID,
        audit_decision_id: uuid.UUID,
        artifact_ids: Mapping[str, uuid.UUID | None],
        primary_pocket_id: uuid.UUID | None = None,
        wrong_pocket_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TargetQualificationAudit:
        if set(artifact_ids) != set(AUDIT_ARTIFACT_ID_FIELDS):
            raise ValueError("target qualification artifact fields are incomplete")
        if any(artifact_ids[field] is None for field in REQUIRED_AUDIT_ARTIFACT_FIELDS):
            raise ValueError("target qualification core artifacts cannot be null")
        target = await self.session.get(Target, target_id)
        run = await self.session.get(ExperimentRun, audit_run_id)
        call = await self.session.get(ToolCall, audit_tool_call_id)
        decision = await self.session.get(AgentDecision, audit_decision_id)
        if target is None or run is None or call is None or decision is None:
            raise KeyError("target qualification identity graph is incomplete")
        if (
            run.target_id != target_id
            or call.run_id != audit_run_id
            or decision.run_id != audit_run_id
        ):
            raise ValueError(
                "target qualification run, ToolCall, decision, and target are detached"
            )
        await self._require_decision_tool_edge(audit_decision_id, audit_tool_call_id)
        if target.sequence_sha256 != item.sequence_sha256:
            raise ValueError("target qualification sequence differs from Target identity")
        if target.accession is not None and target.accession != item.sequence_accession:
            raise ValueError("target qualification accession differs from Target identity")
        primary_pocket = (
            await self.session.get(TargetPocket, primary_pocket_id)
            if primary_pocket_id is not None
            else None
        )
        wrong_pocket = (
            await self.session.get(TargetPocket, wrong_pocket_id)
            if wrong_pocket_id is not None
            else None
        )
        if any(
            pocket is not None and pocket.target_id != target_id
            for pocket in (primary_pocket, wrong_pocket)
        ):
            raise ValueError("target qualification pocket is cross-target")
        if primary_pocket_id is not None and primary_pocket is None:
            raise ValueError("target qualification primary pocket is missing")
        if wrong_pocket_id is not None and wrong_pocket is None:
            raise ValueError("target qualification wrong pocket is missing")
        if item.audit_status == "qualified_primary" and (
            primary_pocket is None or wrong_pocket is None
        ):
            raise ValueError("qualified target requires typed primary and wrong pockets")
        if (
            primary_pocket is not None
            and primary_pocket.evidence_grade != item.primary_pocket_grade
        ):
            raise ValueError("target qualification pocket grade differs from TargetPocket")
        artifacts = await self._require_artifacts(list(artifact_ids.values()))
        for id_field, sha_field in AUDIT_ARTIFACT_ID_FIELDS.items():
            artifact_id = artifact_ids[id_field]
            expected_sha = getattr(item, sha_field)
            observed_sha = artifacts[artifact_id].sha256 if artifact_id is not None else None
            if observed_sha != expected_sha:
                raise ValueError(f"target qualification {id_field} differs from frozen SHA")
        identity = {
            "audit_scope_id": audit_scope_id,
            "schema_version": SCHEMA_VERSION,
            "shortlist_order": item.shortlist_order,
            "target_id": target_id,
            "audit_run_id": audit_run_id,
            "audit_tool_call_id": audit_tool_call_id,
            "audit_decision_id": audit_decision_id,
            "target_key": item.target_key,
            "organism_and_strain": item.organism_and_strain,
            "sequence_accession": item.sequence_accession,
            "sequence_entry_version": item.sequence_entry_version,
            "sequence_admission_basis": item.sequence_admission_basis,
            "sequence_sha256": item.sequence_sha256,
            **artifact_ids,
            "structure_source_type": item.structure_source_type,
            "primary_pocket_id": primary_pocket_id,
            "wrong_pocket_id": wrong_pocket_id,
            "primary_pocket_grade": item.primary_pocket_grade,
            "audit_status": item.audit_status,
            "rejection_reasons_json": item.rejection_reasons,
            "diversity_vector_json": item.diversity_vector,
            "metadata_json": metadata or {},
        }
        existing = await self.session.scalar(
            select(TargetQualificationAudit).where(
                TargetQualificationAudit.audit_scope_id == audit_scope_id,
                TargetQualificationAudit.shortlist_order == item.shortlist_order,
            )
        )
        if existing is not None:
            if any(getattr(existing, field) != value for field, value in identity.items()):
                raise ValueError("target qualification audit retry payload drifted")
            return existing
        frozen_panel = await self.session.scalar(
            select(TargetPanelSelectionWitness).where(
                TargetPanelSelectionWitness.audit_scope_id == audit_scope_id
            )
        )
        if frozen_panel is not None:
            raise ValueError("target qualification ledger is immutable after panel freeze")
        row = TargetQualificationAudit(**identity)
        self.session.add(row)
        await self.session.flush()
        await self.events.append_event(
            "target_qualification",
            row.id,
            "target_qualification.recorded",
            "v35-target-qualification",
            {
                "audit_scope_id": audit_scope_id,
                "shortlist_order": item.shortlist_order,
                "target_key": item.target_key,
                "audit_status": item.audit_status,
            },
        )
        return row

    async def record_panel_selection(
        self,
        snapshot: TargetQualificationSnapshot,
        *,
        selection_run_id: uuid.UUID,
        selection_tool_call_id: uuid.UUID,
        selection_decision_id: uuid.UUID,
        acea_anchor_artifact_id: uuid.UUID,
        selection_witness_artifact_id: uuid.UUID,
        snapshot_artifact_id: uuid.UUID,
        artifact_payloads: Mapping[str, bytes],
        metadata: dict[str, Any] | None = None,
    ) -> TargetPanelSelectionWitness:
        run = await self.session.get(ExperimentRun, selection_run_id)
        call = await self.session.get(ToolCall, selection_tool_call_id)
        decision = await self.session.get(AgentDecision, selection_decision_id)
        if run is None or call is None or decision is None:
            raise KeyError("target panel selection identity graph is incomplete")
        if call.run_id != selection_run_id or decision.run_id != selection_run_id:
            raise ValueError("target panel selection ToolCall or decision is cross-run")
        await self._require_decision_tool_edge(selection_decision_id, selection_tool_call_id)
        artifacts = await self._require_artifacts(
            [
                acea_anchor_artifact_id,
                selection_witness_artifact_id,
                snapshot_artifact_id,
            ]
        )
        if artifacts[selection_witness_artifact_id].sha256 != snapshot.selection_witness_sha256:
            raise ValueError("target panel witness artifact differs from snapshot")
        verify_target_qualification_snapshot(
            snapshot.model_dump(mode="json"), dict(artifact_payloads)
        )
        anchor_bytes = canonical_json_bytes(
            {
                "schema_version": SCHEMA_VERSION,
                "audit_scope_id": snapshot.audit_scope_id,
                "acea_anchor_vector": snapshot.acea_anchor_vector,
            }
        )
        if artifacts[acea_anchor_artifact_id].sha256 != sha256_bytes(anchor_bytes):
            raise ValueError("target panel AceA anchor artifact differs from snapshot")
        anchor_sha = artifacts[acea_anchor_artifact_id].sha256
        if artifact_payloads.get(anchor_sha) != anchor_bytes:
            raise ValueError("target panel AceA anchor object bytes are missing or corrupt")
        snapshot_bytes = canonical_json_bytes(snapshot.model_dump(mode="json"))
        if artifacts[snapshot_artifact_id].sha256 != sha256_bytes(snapshot_bytes):
            raise ValueError("target panel snapshot artifact differs from snapshot")
        snapshot_sha = artifacts[snapshot_artifact_id].sha256
        if artifact_payloads.get(snapshot_sha) != snapshot_bytes:
            raise ValueError("target panel snapshot object bytes are missing or corrupt")
        audits = list(
            await self.session.scalars(
                select(TargetQualificationAudit)
                .where(TargetQualificationAudit.audit_scope_id == snapshot.audit_scope_id)
                .order_by(TargetQualificationAudit.shortlist_order)
            )
        )
        if len(audits) != len(snapshot.shortlist):
            raise ValueError("typed target audit denominator differs from frozen snapshot")
        audit_artifact_ids = {
            getattr(row, field)
            for row in audits
            for field in AUDIT_ARTIFACT_ID_FIELDS
            if getattr(row, field) is not None
        }
        audit_artifacts = await self._require_artifacts(list(audit_artifact_ids))
        typed_items = [audit_row_to_item(row, audit_artifacts) for row in audits]
        if typed_items != snapshot.shortlist:
            raise ValueError("typed target audit rows differ from frozen snapshot")
        audits_by_key = {row.target_key: row for row in audits}
        if set(snapshot.selected_target_keys) - set(audits_by_key):
            raise ValueError("target panel selection references an unknown audit")
        identity = {
            "audit_scope_id": snapshot.audit_scope_id,
            "schema_version": SCHEMA_VERSION,
            "selection_method": SELECTION_METHOD,
            "selection_run_id": selection_run_id,
            "selection_tool_call_id": selection_tool_call_id,
            "selection_decision_id": selection_decision_id,
            "requested_new_target_count": snapshot.requested_new_target_count,
            "target_names_selected_before_audit": snapshot.target_names_selected_before_audit,
            "peptide_or_structure_outcomes_used_for_selection": (
                snapshot.peptide_or_structure_outcomes_used_for_selection
            ),
            "target_agnostic_amp_lane_retained": snapshot.target_agnostic_amp_lane_retained,
            "acea_anchor_vector_json": snapshot.acea_anchor_vector,
            "acea_anchor_artifact_id": acea_anchor_artifact_id,
            "selection_witness_artifact_id": selection_witness_artifact_id,
            "snapshot_artifact_id": snapshot_artifact_id,
            "selection_status": "frozen",
            "metadata_json": metadata or {},
        }
        existing = await self.session.scalar(
            select(TargetPanelSelectionWitness).where(
                TargetPanelSelectionWitness.audit_scope_id == snapshot.audit_scope_id
            )
        )
        if existing is not None:
            if any(getattr(existing, field) != value for field, value in identity.items()):
                raise ValueError("target panel selection retry payload drifted")
            members = list(
                await self.session.scalars(
                    select(TargetPanelSelectionMember)
                    .where(TargetPanelSelectionMember.selection_witness_id == existing.id)
                    .order_by(TargetPanelSelectionMember.selection_rank)
                )
            )
            expected_ids = [audits_by_key[key].id for key in snapshot.selected_target_keys]
            if [member.target_audit_id for member in members] != expected_ids:
                raise ValueError("target panel selection member retry payload drifted")
            return existing
        witness = TargetPanelSelectionWitness(**identity)
        self.session.add(witness)
        await self.session.flush()
        for rank, target_key in enumerate(snapshot.selected_target_keys, start=1):
            self.session.add(
                TargetPanelSelectionMember(
                    selection_witness_id=witness.id,
                    selection_rank=rank,
                    target_audit_id=audits_by_key[target_key].id,
                )
            )
        await self.session.flush()
        await self.events.append_event(
            "target_panel",
            witness.id,
            "target_panel.frozen",
            "v35-target-qualification",
            {
                "audit_scope_id": snapshot.audit_scope_id,
                "selected_target_keys": snapshot.selected_target_keys,
                "selection_witness_sha256": snapshot.selection_witness_sha256,
            },
        )
        return witness


async def load_and_verify_target_qualification_from_database(
    session: AsyncSession,
    audit_scope_id: str,
    artifact_payloads: Mapping[str, bytes],
) -> dict[str, Any]:
    witness = await session.scalar(
        select(TargetPanelSelectionWitness).where(
            TargetPanelSelectionWitness.audit_scope_id == audit_scope_id
        )
    )
    if witness is None:
        raise KeyError(f"target panel selection witness not found: {audit_scope_id}")
    audits = list(
        await session.scalars(
            select(TargetQualificationAudit)
            .where(TargetQualificationAudit.audit_scope_id == audit_scope_id)
            .order_by(TargetQualificationAudit.shortlist_order)
        )
    )
    members = list(
        await session.scalars(
            select(TargetPanelSelectionMember)
            .where(TargetPanelSelectionMember.selection_witness_id == witness.id)
            .order_by(TargetPanelSelectionMember.selection_rank)
        )
    )
    artifact_ids = {
        getattr(row, field)
        for row in audits
        for field in AUDIT_ARTIFACT_ID_FIELDS
        if getattr(row, field) is not None
    } | {
        witness.acea_anchor_artifact_id,
        witness.selection_witness_artifact_id,
        witness.snapshot_artifact_id,
    }
    artifacts = {
        artifact.id: artifact
        for artifact in await session.scalars(select(Artifact).where(Artifact.id.in_(artifact_ids)))
    }
    if set(artifacts) != artifact_ids:
        raise KeyError("database-only target replay is missing artifact metadata")
    return verify_target_qualification_database_projection(
        audits, witness, members, artifacts, artifact_payloads
    )
