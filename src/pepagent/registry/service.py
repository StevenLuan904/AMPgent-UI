from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import select

from pepagent.db.models import Artifact, ModelRelease, ModelReleaseArtifact
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file
from pepagent.registry.mlflow_registry import register_model_version
from pepagent.storage.object_store import ContentAddressedObjectStore


def _release_files(release_dir: Path) -> list[Path]:
    return sorted(path for path in release_dir.iterdir() if path.is_file())


async def register_local_model_release(
    *,
    name: str,
    role: str,
    release_dir: Path,
    source_uri: str,
    source_revision: str,
    expected_weights_sha256: str,
    weights_filename: str = "pytorch_model.bin",
    adapter_version: str,
    admission_status: str,
) -> dict[str, Any]:
    weights_path = release_dir / weights_filename
    if not await asyncio.to_thread(weights_path.is_file):
        raise FileNotFoundError(f"weights missing: {weights_path}")
    actual_weights_sha256 = await asyncio.to_thread(sha256_file, weights_path)
    if actual_weights_sha256 != expected_weights_sha256:
        raise OSError(
            f"weight checksum mismatch: expected {expected_weights_sha256}, "
            f"got {actual_weights_sha256}"
        )

    files = await asyncio.to_thread(_release_files, release_dir)
    stored_files = []
    store = ContentAddressedObjectStore()
    for path in files:
        stored = await asyncio.to_thread(store.put_file, path)
        stored_files.append({"name": path.name, "stored": stored})
    weight_object = next(
        item["stored"] for item in stored_files if item["name"] == weights_path.name
    )
    mlflow_version = await asyncio.to_thread(
        register_model_version,
        name,
        weight_object.uri,
        source_revision,
        expected_weights_sha256,
        admission_status,
    )

    async with SessionFactory() as session, session.begin():
        release = await session.scalar(
            select(ModelRelease).where(
                ModelRelease.name == name,
                ModelRelease.source_revision == source_revision,
                ModelRelease.weights_sha256 == expected_weights_sha256,
            )
        )
        manifest = [
            {
                "name": item["name"],
                "sha256": item["stored"].sha256,
                "size_bytes": item["stored"].size_bytes,
                "uri": item["stored"].uri,
            }
            for item in stored_files
        ]
        if release is None:
            release = ModelRelease(
                name=name,
                role=role,
                source_uri=source_uri,
                source_revision=source_revision,
                weights_sha256=expected_weights_sha256,
                adapter_version=adapter_version,
                admission_status=admission_status,
                mlflow_model_name=name,
                mlflow_model_version=mlflow_version,
                metadata_json={"files": manifest},
            )
            session.add(release)
            await session.flush()
        else:
            release.mlflow_model_name = name
            release.mlflow_model_version = mlflow_version
            release.admission_status = admission_status
            release.metadata_json = {"files": manifest}

        for index, item in enumerate(stored_files):
            stored = item["stored"]
            artifact = await session.scalar(
                select(Artifact).where(Artifact.sha256 == stored.sha256)
            )
            if artifact is None:
                artifact = Artifact(
                    sha256=stored.sha256,
                    size_bytes=stored.size_bytes,
                    media_type=stored.media_type,
                    storage_uri=stored.uri,
                    metadata_json={"model_release": name, "filename": item["name"]},
                )
                session.add(artifact)
                await session.flush()
            role_name = "weights" if item["name"] == weights_path.name else f"file_{index}"
            link = await session.get(
                ModelReleaseArtifact,
                {
                    "model_release_id": release.id,
                    "artifact_id": artifact.id,
                    "role": role_name,
                },
            )
            if link is None:
                session.add(
                    ModelReleaseArtifact(
                        model_release_id=release.id,
                        artifact_id=artifact.id,
                        role=role_name,
                    )
                )
    return {
        "model_release_id": str(release.id),
        "name": name,
        "source_revision": source_revision,
        "weights_sha256": expected_weights_sha256,
        "admission_status": admission_status,
        "mlflow_model_version": mlflow_version,
        "files": manifest,
    }
