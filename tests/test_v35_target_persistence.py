from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from pepagent.db.base import Base
from pepagent.db.models import (
    Artifact,
    TargetPanelSelectionMember,
    TargetPanelSelectionWitness,
    TargetQualificationAudit,
)
from pepagent.provenance.hashing import sha256_bytes
from pepagent.v35_multitarget import (
    SCHEMA_VERSION,
    TargetAuditItem,
    TargetQualificationSnapshot,
    build_selection_witness,
    canonical_json_bytes,
    deterministic_panel_selection,
)
from pepagent.v35_persistence import verify_target_qualification_database_projection


def _id(number: int) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0000-{number:012d}")


def _artifact(
    payloads: dict[str, bytes],
    artifacts: dict[uuid.UUID, Artifact],
    number: int,
    payload: bytes | dict,
) -> tuple[uuid.UUID, str]:
    raw = canonical_json_bytes(payload) if isinstance(payload, dict) else payload
    digest = sha256_bytes(raw)
    artifact_id = _id(1000 + number)
    payloads[digest] = raw
    artifacts[artifact_id] = Artifact(
        id=artifact_id,
        sha256=digest,
        size_bytes=len(raw),
        media_type="application/json",
        storage_uri=f"memory://{digest}",
        metadata_json={"synthetic": True},
    )
    return artifact_id, digest


def _fixture() -> tuple[
    list[TargetQualificationAudit],
    TargetPanelSelectionWitness,
    list[TargetPanelSelectionMember],
    dict[uuid.UUID, Artifact],
    dict[str, bytes],
]:
    payloads: dict[str, bytes] = {}
    artifacts: dict[uuid.UUID, Artifact] = {}
    audits: list[TargetQualificationAudit] = []
    items: list[TargetAuditItem] = []
    vectors = [
        [1.0, 0.0],
        [0.0, 1.0],
        [-1.0, 0.0],
        [0.0, -1.0],
        [0.7, 0.7],
        [-0.7, -0.7],
    ]
    artifact_number = 1
    for order in range(1, 9):
        qualified = order <= 6
        target_key = f"target-{order:02d}"
        sequence_artifact_id, sequence_sha = _artifact(
            payloads, artifacts, artifact_number, f"SEQUENCE{order}".encode()
        )
        artifact_number += 1
        source_id, source_sha = _artifact(
            payloads,
            artifacts,
            artifact_number,
            {"target": target_key, "source": "synthetic"},
        )
        artifact_number += 1
        feature_id, feature_sha = _artifact(
            payloads,
            artifacts,
            artifact_number,
            {"target": target_key, "features": []},
        )
        artifact_number += 1
        optional: dict[str, tuple[uuid.UUID, str] | tuple[None, None]] = {
            "coordinate": (None, None),
            "validation": (None, None),
            "mapping": (None, None),
            "primary": (None, None),
            "wrong": (None, None),
        }
        if qualified:
            for role in optional:
                optional[role] = _artifact(
                    payloads,
                    artifacts,
                    artifact_number,
                    {"target": target_key, "role": role},
                )
                artifact_number += 1
        item = TargetAuditItem.model_validate(
            {
                "shortlist_order": order,
                "target_key": target_key,
                "organism_and_strain": f"Bacterium strain {order}",
                "sequence_accession": f"P{order:05d}",
                "sequence_entry_version": "1",
                "sequence_admission_basis": "UniProtKB_reviewed",
                "sequence_sha256": sequence_sha,
                "sequence_artifact_sha256": sequence_sha,
                "source_manifest_sha256": source_sha,
                "feature_evidence_sha256": feature_sha,
                "structure_source_type": (
                    "experimental_exact_target"
                    if qualified
                    else "predicted_hypothesis_only"
                ),
                "coordinate_sha256": optional["coordinate"][1],
                "structure_validation_sha256": optional["validation"][1],
                "sequence_structure_mapping_sha256": optional["mapping"][1],
                "primary_pocket_grade": "A" if qualified else "C",
                "primary_pocket_definition_sha256": optional["primary"][1],
                "wrong_pocket_definition_sha256": optional["wrong"][1],
                "audit_status": "qualified_primary" if qualified else "rejected",
                "rejection_reasons": (
                    [] if qualified else ["pocket_evidence_below_primary_grade"]
                ),
                "diversity_vector": vectors[order - 1] if qualified else None,
            }
        )
        items.append(item)
        audits.append(
            TargetQualificationAudit(
                id=_id(order),
                audit_scope_id="v35-synthetic-persistence",
                schema_version=SCHEMA_VERSION,
                shortlist_order=order,
                target_id=_id(100 + order),
                audit_run_id=_id(200 + order),
                audit_tool_call_id=_id(300 + order),
                audit_decision_id=_id(400 + order),
                target_key=target_key,
                organism_and_strain=item.organism_and_strain,
                sequence_accession=item.sequence_accession,
                sequence_entry_version=item.sequence_entry_version,
                sequence_admission_basis=item.sequence_admission_basis,
                sequence_sha256=item.sequence_sha256,
                sequence_artifact_id=sequence_artifact_id,
                source_manifest_artifact_id=source_id,
                feature_evidence_artifact_id=feature_id,
                structure_source_type=item.structure_source_type,
                coordinate_artifact_id=optional["coordinate"][0],
                structure_validation_artifact_id=optional["validation"][0],
                sequence_structure_mapping_artifact_id=optional["mapping"][0],
                primary_pocket_id=_id(500 + order) if qualified else None,
                wrong_pocket_id=_id(600 + order) if qualified else None,
                primary_pocket_grade=item.primary_pocket_grade,
                primary_pocket_definition_artifact_id=optional["primary"][0],
                wrong_pocket_definition_artifact_id=optional["wrong"][0],
                audit_status=item.audit_status,
                rejection_reasons_json=item.rejection_reasons,
                diversity_vector_json=item.diversity_vector,
                metadata_json={"synthetic": True},
            )
        )
    selected = deterministic_panel_selection(
        items, acea_anchor_vector=[0.0, 0.0], panel_size=3
    )
    provisional = TargetQualificationSnapshot.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "audit_scope_id": "v35-synthetic-persistence",
            "target_names_selected_before_audit": False,
            "peptide_or_structure_outcomes_used_for_selection": False,
            "target_agnostic_amp_lane_retained": True,
            "acea_anchor_vector": [0.0, 0.0],
            "requested_new_target_count": 3,
            "shortlist": [item.model_dump(mode="json") for item in items],
            "selected_target_keys": selected,
            "selection_method": "hard_gate_then_anchor_aware_maximin_v1",
            "selection_witness_sha256": "0" * 64,
        }
    )
    witness_payload = build_selection_witness(provisional)
    witness_artifact_id, witness_sha = _artifact(
        payloads, artifacts, artifact_number, witness_payload
    )
    artifact_number += 1
    snapshot = provisional.model_copy(update={"selection_witness_sha256": witness_sha})
    anchor_artifact_id, _ = _artifact(
        payloads,
        artifacts,
        artifact_number,
        {
            "schema_version": SCHEMA_VERSION,
            "audit_scope_id": snapshot.audit_scope_id,
            "acea_anchor_vector": snapshot.acea_anchor_vector,
        },
    )
    artifact_number += 1
    snapshot_artifact_id, _ = _artifact(
        payloads, artifacts, artifact_number, snapshot.model_dump(mode="json")
    )
    witness = TargetPanelSelectionWitness(
        id=_id(900),
        audit_scope_id=snapshot.audit_scope_id,
        schema_version=SCHEMA_VERSION,
        selection_method=snapshot.selection_method,
        selection_run_id=_id(901),
        selection_tool_call_id=_id(902),
        selection_decision_id=_id(903),
        requested_new_target_count=3,
        target_names_selected_before_audit=False,
        peptide_or_structure_outcomes_used_for_selection=False,
        target_agnostic_amp_lane_retained=True,
        acea_anchor_vector_json=[0.0, 0.0],
        acea_anchor_artifact_id=anchor_artifact_id,
        selection_witness_artifact_id=witness_artifact_id,
        snapshot_artifact_id=snapshot_artifact_id,
        selection_status="frozen",
        metadata_json={"synthetic": True},
    )
    audits_by_key = {row.target_key: row for row in audits}
    members = [
        TargetPanelSelectionMember(
            selection_witness_id=witness.id,
            selection_rank=rank,
            target_audit_id=audits_by_key[key].id,
        )
        for rank, key in enumerate(selected, start=1)
    ]
    return audits, witness, members, artifacts, payloads


def test_v35_typed_models_and_migration_cover_target_lineage() -> None:
    expected = {
        "target_qualification_audits",
        "target_panel_selection_witnesses",
        "target_panel_selection_members",
    }
    assert expected.issubset(Base.metadata.tables)
    migration = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "0011_target_qualification_lineage.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision = "0010_harness_evolution_lineage"' in migration
    for table in expected:
        assert f'"{table}"' in migration


def test_v35_database_projection_replays_exactly_without_candidate_rows() -> None:
    audits, witness, members, artifacts, payloads = _fixture()
    receipt = verify_target_qualification_database_projection(
        audits, witness, members, artifacts, payloads
    )
    assert receipt["exact_replay"] is True
    assert receipt["database_projection_exact"] is True
    assert receipt["typed_audit_count"] == 8
    assert receipt["typed_member_count"] == 3
    assert receipt["candidate_count"] == 0
    assert receipt["evaluation_count"] == 0
    assert receipt["panel_execution_authorized"] is False


def test_v35_database_projection_rejects_member_and_object_drift() -> None:
    audits, witness, members, artifacts, payloads = _fixture()
    members[0].target_audit_id = _id(999)
    with pytest.raises(ValueError, match="outside the scope"):
        verify_target_qualification_database_projection(
            audits, witness, members, artifacts, payloads
        )

    audits, witness, members, artifacts, payloads = _fixture()
    snapshot_artifact = artifacts[witness.snapshot_artifact_id]
    payloads[snapshot_artifact.sha256] += b"drift"
    with pytest.raises(ValueError, match="metadata differs"):
        verify_target_qualification_database_projection(
            audits, witness, members, artifacts, payloads
        )
