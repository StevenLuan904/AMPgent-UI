from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from pepagent.db.models import Artifact, EvidenceArtifactLocation
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
        self.location_witnesses: dict[str, EvidenceArtifactLocation] = {}

    async def scalar(self, statement: Any) -> Any:
        self.scalar_statements.append(statement)
        compiled = _compiled(statement)
        if "INSERT INTO artifacts" in compiled:
            await asyncio.sleep(0)
            return self.artifact.id if self.insert_wins else None
        if "FROM artifacts" in compiled:
            return self.artifact
        if "FROM evidence_artifact_locations" in compiled:
            witness_sha256 = statement.compile().params["location_witness_sha256_1"]
            return self.location_witnesses.get(witness_sha256)
        raise AssertionError(f"unexpected scalar statement: {compiled}")

    async def get(self, _model: Any, artifact_id: uuid.UUID) -> Artifact | None:
        assert artifact_id == self.artifact.id
        return self.artifact

    async def execute(self, statement: Any) -> None:
        self.execute_statements.append(statement)
        compiled = _compiled(statement)
        if "INSERT INTO evidence_artifact_locations" in compiled:
            params = statement.compile().params
            witness = EvidenceArtifactLocation(
                tool_call_id=params["tool_call_id"],
                artifact_id=params["artifact_id"],
                role=params["role"],
                location_witness_sha256=params["location_witness_sha256"],
                requested_storage_uri=params["requested_storage_uri"],
                location_metadata_json=params["location_metadata_json"],
            )
            self.location_witnesses[witness.location_witness_sha256] = witness


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
    assert len(winner.scalar_statements) == 2
    assert len(conflicting_branch.scalar_statements) == 3
    assert "ON CONFLICT (sha256) DO NOTHING" in _compiled(winner.scalar_statements[0])
    assert len(winner.execute_statements) == len(conflicting_branch.execute_statements) == 2
    assert "ON CONFLICT (tool_call_id, artifact_id, role) DO NOTHING" in _compiled(
        winner.execute_statements[0]
    )
    location_insert = _compiled(winner.execute_statements[1])
    assert (
        "ON CONFLICT (tool_call_id, artifact_id, role, location_witness_sha256) "
        "DO NOTHING"
    ) in location_insert
    winner_params = winner.execute_statements[0].compile().params
    conflict_params = conflicting_branch.execute_statements[0].compile().params
    assert winner_params["tool_call_id"] == first_call_id
    assert conflict_params["tool_call_id"] == second_call_id


@pytest.mark.asyncio
async def test_reused_artifact_allows_same_content_at_a_different_location() -> None:
    artifact = _artifact()
    artifact.storage_uri = "s3://wrong-bucket/drifted.json"
    session = _ArtifactSession(artifact, insert_wins=False)

    reused = await _get_or_create_stored_artifact(
        session, _stored_payload(), {"target_key": "acea"}
    )

    assert reused is artifact


@pytest.mark.asyncio
async def test_real_score_all_sha_preserves_multiple_requested_locations_on_one_edge() -> None:
    real_sha = "3483ca5d60e91af0a5e097c34e87d26fcef8928c5ee341d0fa03782144e26a38"
    first_uri = (
        "ssh://huangyueshan@192.168.99.19/data0/ampgent-pepglad-huangyueshan/"
        "v1/artifacts/score-all/de455c5fb6c2b3654d25f33b58d3b2649e7429a7fce6dbbf03feabe70400500b/"
        "score/work/hemolysis_risk/candidates.csv"
    )
    second_uri = (
        "ssh://huangyueshan@192.168.99.19/data0/ampgent-pepglad-huangyueshan/"
        "v1/artifacts/score-all/de455c5fb6c2b3654d25f33b58d3b2649e7429a7fce6dbbf03feabe70400500b/"
        "score/work/mic_potency/candidates.csv"
    )
    artifact = Artifact(
        id=uuid.uuid4(),
        sha256=real_sha,
        size_bytes=38795,
        media_type="text/csv; charset=utf-8",
        storage_uri=first_uri,
        metadata_json={"target_key": "angpt1"},
    )
    session = _ArtifactSession(artifact, insert_wins=False)
    call_id = uuid.uuid4()
    first_payload = {
        "sha256": real_sha,
        "size_bytes": 38795,
        "media_type": "text/csv; charset=utf-8",
        "uri": first_uri,
    }
    second_payload = {**first_payload, "uri": second_uri}

    await _register_artifact(
        session,
        call_id,
        first_payload,
        "autoresearch_score_candidates_csv",
        {"metric": "hemolysis_risk", "target_key": "angpt1"},
    )
    await _register_artifact(
        session,
        call_id,
        second_payload,
        "autoresearch_score_candidates_csv",
        {"metric": "mic_potency", "target_key": "angpt1"},
    )

    witnesses = list(session.location_witnesses.values())
    assert len(witnesses) == 2
    assert {item.requested_storage_uri for item in witnesses} == {
        first_uri,
        second_uri,
    }
    assert {item.location_metadata_json["metric"] for item in witnesses} == {
        "hemolysis_risk",
        "mic_potency",
    }


@pytest.mark.parametrize(
    ("field", "drifted"),
    [("size_bytes", 1974), ("media_type", "text/plain")],
)
def test_artifact_content_identity_fails_closed_on_size_or_media_drift(
    field: str, drifted: Any
) -> None:
    artifact = _artifact()
    setattr(artifact, field, drifted)

    mismatches = _artifact_identity_mismatches(artifact, _stored_payload())

    assert field in mismatches


def test_artifact_reserved_content_metadata_fails_closed_on_drift() -> None:
    artifact = _artifact()
    artifact.metadata_json = {"content_identity": {"schema": "score-all.1"}}

    mismatches = _artifact_identity_mismatches(
        artifact,
        _stored_payload(),
        {"content_identity": {"schema": "score-all.2"}},
    )

    assert mismatches["content_identity"] == {
        "expected": {"schema": "score-all.2"},
        "actual": {"schema": "score-all.1"},
    }


def test_artifact_identity_allows_per_run_metadata_to_differ() -> None:
    artifact = _artifact()
    artifact.metadata_json = {"target_key": "first-registrar"}

    assert _artifact_identity_mismatches(artifact, _stored_payload()) == {}
