from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from pepagent.db.models import Artifact
from pepagent.workers.activities import (
    _artifact_identity_mismatches,
    _get_or_create_stored_artifact,
    _register_artifact,
)

SHA256 = "6" * 64
URI = f"s3://pepagent-artifacts/cas/{SHA256}/receipt.json"


def _stored_payload() -> dict[str, Any]:
    return {
        "sha256": SHA256,
        "size_bytes": 1973,
        "media_type": "application/json",
        "uri": URI,
    }


def _artifact() -> Artifact:
    return Artifact(
        id=uuid.uuid4(),
        sha256=SHA256,
        size_bytes=1973,
        media_type="application/json",
        storage_uri=URI,
        metadata_json={"first_registrar": "branch-a"},
    )


class _ArtifactSession:
    def __init__(self, artifact: Artifact, *, insert_wins: bool) -> None:
        self.artifact = artifact
        self.insert_wins = insert_wins
        self.scalar_statements: list[Any] = []
        self.execute_statements: list[Any] = []

    async def scalar(self, statement: Any) -> Any:
        self.scalar_statements.append(statement)
        if len(self.scalar_statements) == 1:
            await asyncio.sleep(0)
            return self.artifact.id if self.insert_wins else None
        return self.artifact

    async def get(self, _model: Any, artifact_id: uuid.UUID) -> Artifact | None:
        assert artifact_id == self.artifact.id
        return self.artifact

    async def execute(self, statement: Any) -> None:
        self.execute_statements.append(statement)


def _compiled(statement: Any) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        )
    )


@pytest.mark.asyncio
async def test_concurrent_cross_run_artifact_registration_reuses_global_sha_and_links_both_calls(
) -> None:
    artifact = _artifact()
    winner = _ArtifactSession(artifact, insert_wins=True)
    conflicting_branch = _ArtifactSession(artifact, insert_wins=False)
    first_call_id = uuid.uuid4()
    second_call_id = uuid.uuid4()

    first, second = await asyncio.gather(
        _register_artifact(
            winner,
            first_call_id,
            _stored_payload(),
            "autoresearch_score_source_map_receipt",
            {"target_key": "acea"},
        ),
        _register_artifact(
            conflicting_branch,
            second_call_id,
            _stored_payload(),
            "autoresearch_score_source_map_receipt",
            {"target_key": "vegfa"},
        ),
    )

    assert first.id == second.id == artifact.id
    assert len(winner.scalar_statements) == 1
    assert len(conflicting_branch.scalar_statements) == 2
    assert "ON CONFLICT (sha256) DO NOTHING" in _compiled(winner.scalar_statements[0])
    assert len(winner.execute_statements) == len(conflicting_branch.execute_statements) == 1
    assert "ON CONFLICT (tool_call_id, artifact_id, role) DO NOTHING" in _compiled(
        winner.execute_statements[0]
    )
    winner_params = winner.execute_statements[0].compile().params
    conflict_params = conflicting_branch.execute_statements[0].compile().params
    assert winner_params["tool_call_id"] == first_call_id
    assert conflict_params["tool_call_id"] == second_call_id


@pytest.mark.asyncio
async def test_reused_artifact_fails_closed_when_same_sha_has_different_physical_identity() -> None:
    artifact = _artifact()
    artifact.storage_uri = "s3://wrong-bucket/drifted.json"
    session = _ArtifactSession(artifact, insert_wins=False)

    with pytest.raises(ValueError, match="Artifact identity drifted"):
        await _get_or_create_stored_artifact(session, _stored_payload(), {"target_key": "acea"})


def test_artifact_identity_allows_per_run_metadata_to_differ() -> None:
    artifact = _artifact()
    artifact.metadata_json = {"target_key": "first-registrar"}

    assert _artifact_identity_mismatches(artifact, _stored_payload()) == {}
