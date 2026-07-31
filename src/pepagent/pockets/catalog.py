from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import LifecycleEvent, PocketEvidence, Target, TargetPocket
from pepagent.domain.schemas import PocketCatalogSpec
from pepagent.provenance.hashing import sha256_json, sha256_text


async def import_pocket_catalog(
    session: AsyncSession,
    catalog: PocketCatalogSpec,
    *,
    actor: str = "pocket-catalog-import",
) -> dict[str, Any]:
    """Idempotently import a versioned catalog while retaining independent source records."""
    result: dict[str, Any] = {
        "catalog_version": catalog.catalog_version,
        "targets_created": 0,
        "targets_updated": 0,
        "pockets_created": 0,
        "pockets_updated": 0,
        "evidence_created": 0,
        "evidence_existing": 0,
        "targets": [],
    }
    for item in catalog.targets:
        sequence_sha256 = sha256_text(item.sequence)
        target = await session.scalar(
            select(Target).where(Target.sequence_sha256 == sequence_sha256)
        )
        target_metadata = {
            "role": item.role,
            "source_database": item.source_database,
            "source_uri": item.source_uri,
            "source_version": item.source_version,
            "source_retrieved_at": item.source_retrieved_at.isoformat(),
            "source_document_sha256": item.source_document_sha256,
            "pocket_catalog_version": catalog.catalog_version,
        }
        if target is None:
            target = Target(
                name=item.name,
                organism=item.organism,
                accession=item.accession,
                sequence=item.sequence,
                sequence_sha256=sequence_sha256,
                metadata_json=target_metadata,
            )
            session.add(target)
            await session.flush()
            result["targets_created"] += 1
        else:
            target.name = item.name
            target.organism = item.organism
            target.accession = item.accession
            target.metadata_json = {**target.metadata_json, **target_metadata}
            result["targets_updated"] += 1

        target_result = {
            "target_id": str(target.id),
            "accession": item.accession,
            "pocket_ids": [],
        }
        for pocket_item in item.pockets:
            pocket = await session.scalar(
                select(TargetPocket).where(
                    TargetPocket.target_id == target.id,
                    TargetPocket.pocket_key == pocket_item.key,
                )
            )
            pocket_values = {
                "name": pocket_item.name,
                "pocket_type": pocket_item.pocket_type,
                "functional_role": pocket_item.functional_role,
                "status": pocket_item.status,
                "evidence_grade": pocket_item.evidence_grade,
                "evidence_score": pocket_item.evidence_score,
                "conditioning_priority": pocket_item.conditioning_priority,
                "conditioning_enabled": pocket_item.conditioning_enabled,
                "residue_indices": pocket_item.residue_indices,
                "context_json": pocket_item.context,
                "limitations_json": pocket_item.limitations,
                "metadata_json": {
                    **pocket_item.metadata,
                    "catalog_version": catalog.catalog_version,
                },
            }
            if pocket is None:
                pocket = TargetPocket(
                    target_id=target.id,
                    pocket_key=pocket_item.key,
                    **pocket_values,
                )
                session.add(pocket)
                await session.flush()
                result["pockets_created"] += 1
                await _append_event(
                    session,
                    "pocket",
                    pocket.id,
                    "pocket.created",
                    actor,
                    {"target_id": str(target.id), "pocket_key": pocket.pocket_key},
                )
            else:
                changed = any(getattr(pocket, key) != value for key, value in pocket_values.items())
                for key, value in pocket_values.items():
                    setattr(pocket, key, value)
                if changed:
                    result["pockets_updated"] += 1
                    await _append_event(
                        session,
                        "pocket",
                        pocket.id,
                        "pocket.catalog_updated",
                        actor,
                        {"catalog_version": catalog.catalog_version},
                    )

            target_result["pocket_ids"].append(str(pocket.id))
            for evidence_item in pocket_item.evidence:
                canonical_evidence = evidence_item.model_dump(mode="json")
                evidence_sha256 = sha256_json(
                    {
                        "target_sequence_sha256": sequence_sha256,
                        "pocket_key": pocket_item.key,
                        "evidence": canonical_evidence,
                    }
                )
                existing = await session.scalar(
                    select(PocketEvidence).where(
                        PocketEvidence.evidence_sha256 == evidence_sha256
                    )
                )
                if existing is not None:
                    result["evidence_existing"] += 1
                    continue
                session.add(
                    PocketEvidence(
                        target_id=target.id,
                        pocket_id=pocket.id,
                        evidence_kind=evidence_item.evidence_kind,
                        evidence_grade=evidence_item.evidence_grade,
                        source_type=evidence_item.source_type,
                        source_uri=evidence_item.source_uri,
                        source_accession=evidence_item.source_accession,
                        source_version=evidence_item.source_version,
                        source_revision_date=evidence_item.source_revision_date,
                        retrieved_at=evidence_item.retrieved_at,
                        chain_ids=evidence_item.chain_ids,
                        source_residue_indices=evidence_item.source_residue_indices,
                        residue_indices=evidence_item.target_residue_indices,
                        confidence=evidence_item.confidence,
                        experimental_method=evidence_item.experimental_method,
                        resolution_angstrom=evidence_item.resolution_angstrom,
                        mapping_json=evidence_item.mapping,
                        limitations_json=evidence_item.limitations,
                        evidence_json=evidence_item.details,
                        evidence_sha256=evidence_sha256,
                    )
                )
                result["evidence_created"] += 1
        result["targets"].append(target_result)
    return result


async def _append_event(
    session: AsyncSession,
    aggregate_type: str,
    aggregate_id: Any,
    event_type: str,
    actor: str,
    payload: dict[str, Any],
) -> None:
    events = list(
        await session.scalars(
            select(LifecycleEvent)
            .where(
                LifecycleEvent.aggregate_type == aggregate_type,
                LifecycleEvent.aggregate_id == aggregate_id,
            )
            .order_by(LifecycleEvent.sequence_no)
        )
    )
    session.add(
        LifecycleEvent(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            sequence_no=(events[-1].sequence_no + 1) if events else 1,
            event_type=event_type,
            actor=actor,
            payload_json=payload,
            payload_sha256=sha256_json(payload),
        )
    )
