from __future__ import annotations

import argparse
import asyncio
import io
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from pepagent import structure_evidence_repair as repair
from pepagent.domain.enums import CandidateStatus, EvaluationStatus
from pepagent.provenance.hashing import sha256_bytes

RUN_ID = uuid.UUID("0c3bd48f-c25c-5268-ba91-e16108285161")
CANDIDATE_ID = uuid.UUID("184322d3-e35a-4b04-8e5d-63f7027f777c")
PARENT_CALL_ID = uuid.UUID("3d78a63d-69ef-49fa-b9ca-b0f872c02184")
AUDIT_CALL_ID = uuid.UUID("fed41955-c1d8-48f9-8f53-68bf3f412141")
SEED = 302608293


def _artifact(name: str) -> dict[str, Any]:
    raw = name.encode()
    digest = sha256_bytes(raw)
    return {
        "path": name,
        "sha256": digest,
        "size_bytes": len(raw),
        "uri": f"s3://pepagent/sha256/{digest}",
        "media_type": "application/octet-stream",
    }


def _large_activity_request() -> dict[str, Any]:
    decoys = [
        {
            "decoy_id": f"pbp2a-{index:03d}",
            "dG_separated": -51.0 + index / 1000,
            "engine_output": "x" * 5_000,
        }
        for index in range(200)
    ]
    return {
        "run_id": str(RUN_ID),
        "rosetta_result": {
            "candidate": {
                "id": str(CANDIDATE_ID),
                "sequence": "KRWWKWWRR",
                "sequence_sha256": "a" * 64,
            },
            "input": {"seed": SEED, "candidate_id": str(CANDIDATE_ID)},
            "parameters": {
                "n_decoys": 200,
                "support_thresholds": {
                    "severe_structure_clash_count": 10,
                    "interface_min_pair_iptm_median": 0.55,
                    "interface_min_pocket_contacts": 3,
                },
            },
            "rosetta": {
                "seed": SEED,
                "primary_dG_separated_reu": -51.977196309943565,
                "dG_separated_reu": {"minimum": -57.2},
                "peptide_bb_rmsd_angstrom": {"median": 1.25},
                "best_decoy": {
                    "interface_score": -49.0,
                    "reweighted_sc": -3.1,
                    "interface_hbonds": 9,
                    "dSASA_int": 810.0,
                },
                "limitations": ["unit-test payload"],
                "decoys": decoys,
            },
            "interface_audit": {
                "structure_available": True,
                "representative_index": 0,
                "sample_audits": [
                    {
                        "pair_iptm": 0.71,
                        "pocket_contact_count": 8,
                        "cross_chain_clash_count": 0,
                    }
                ],
                "gate_checks": {},
            },
            "provenance": {
                "parent_tool_call_id": str(PARENT_CALL_ID),
                "interface_audit_tool_call_id": str(AUDIT_CALL_ID),
                "tool_name": "rosetta-interface-analyzer",
                "tool_version": "2025.09",
                "environment_sha256": "b" * 64,
                "weights_sha256": "c" * 64,
                "model_uri": "rosetta://ref2015",
                "attempt": 1,
                "raw_output_artifact": _artifact("score.json"),
                "environment_artifact": _artifact("environment.json"),
                "engine_artifacts": [
                    _artifact(f"decoy-{index:03d}.pdb") for index in range(403)
                ],
            },
        },
    }


@pytest.mark.asyncio
async def test_stdin_accepts_one_full_megabyte_activity_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _large_activity_request()
    encoded = json.dumps(request)
    assert len(encoded.encode()) > 1_000_000
    monkeypatch.setattr(repair.sys, "stdin", io.StringIO(encoded))

    loaded, source = await repair._load_request_from_args(argparse.Namespace(stdin=True))
    binding = repair.validate_structure_evidence_request(
        loaded,
        expected_run_id=RUN_ID,
        expected_candidate_id=CANDIDATE_ID,
        expected_seed=SEED,
    )

    assert source == {"kind": "stdin_activity_request"}
    assert binding.decoy_count == 200
    assert binding.primary_dg_separated_reu == -51.977196309943565


@pytest.mark.asyncio
async def test_first_write_and_exact_retry_reuse_scientific_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _large_activity_request()
    binding = repair.validate_structure_evidence_request(request)
    candidate = SimpleNamespace(status=CandidateStatus.ROSETTA_QUEUED)
    state: dict[str, Any] = {
        "call": None,
        "dependencies": set(),
        "artifacts": set(),
        "evaluations": {},
    }

    async def validate_binding(*_args: Any, **_kwargs: Any) -> tuple[Any, Any, Any]:
        return SimpleNamespace(), candidate, state["call"]

    class FakeRepository:
        def __init__(self, _session: Any) -> None:
            pass

        async def record_completed_tool_call(self, *_args: Any, **_kwargs: Any) -> Any:
            if state["call"] is None:
                identity = repair._expected_tool_call_identity(
                    binding.run_id,
                    request["rosetta_result"],
                )
                state["call"] = SimpleNamespace(
                    id=uuid.UUID("8c529853-b40a-4db2-b6a8-99b0f2b26f73"),
                    status=EvaluationStatus.SUCCEEDED,
                    output_sha256=binding.output_sha256,
                    **identity,
                )
            return state["call"]

        async def record_tool_dependency(
            self, child_id: uuid.UUID, parent_id: uuid.UUID, relation: str
        ) -> None:
            state["dependencies"].add((child_id, parent_id, relation))

        async def record_evaluation(
            self,
            _candidate_id: uuid.UUID,
            _call_id: uuid.UUID,
            metric_name: Any,
            *_args: Any,
            **_kwargs: Any,
        ) -> None:
            state["evaluations"][metric_name] = True

        async def transition_candidate(
            self,
            _candidate_id: uuid.UUID,
            status: CandidateStatus,
            *_args: Any,
        ) -> None:
            candidate.status = status

    class FakeSession:
        async def execute(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        async def flush(self) -> None:
            return None

        async def scalars(self, *_args: Any, **_kwargs: Any) -> list[bool]:
            return list(state["evaluations"].values())

        async def scalar(self, statement: Any, *_args: Any, **_kwargs: Any) -> int:
            sql = str(statement)
            if "evidence_artifacts" in sql:
                return len(state["artifacts"])
            return len(state["dependencies"])

        async def get(self, *_args: Any, **_kwargs: Any) -> Any:
            return candidate

    async def persist_artifacts(
        _session: Any,
        _call_id: uuid.UUID,
        result: dict[str, Any],
        binding: repair.StructureEvidenceBinding,
    ) -> int:
        observations = repair._structure_artifact_observations(result, binding)
        state["artifacts"].update(observation.role for observation in observations)
        return len(observations)

    monkeypatch.setattr(repair, "_validate_database_binding", validate_binding)
    monkeypatch.setattr(repair, "ExperimentRepository", FakeRepository)
    session = FakeSession()

    first = await repair.persist_structure_evidence(
        session,
        request=request,
        binding=binding,
        persist_artifacts=persist_artifacts,
    )
    snapshot = {
        "dependencies": set(state["dependencies"]),
        "artifacts": set(state["artifacts"]),
        "evaluations": dict(state["evaluations"]),
    }
    retry = await repair.persist_structure_evidence(
        session,
        request=request,
        binding=binding,
        persist_artifacts=persist_artifacts,
    )

    assert first.reused_tool_call is False
    assert retry.reused_tool_call is True
    assert retry.tool_call_id == first.tool_call_id
    assert first.evaluation_count == retry.evaluation_count == 8
    assert first.artifact_edge_count == retry.artifact_edge_count == 405
    assert first.dependency_count == retry.dependency_count == 2
    assert candidate.status == CandidateStatus.ROSETTA_SCORED
    assert snapshot == {
        "dependencies": state["dependencies"],
        "artifacts": state["artifacts"],
        "evaluations": state["evaluations"],
    }


@pytest.mark.asyncio
async def test_transaction_timeout_records_operational_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _large_activity_request()
    records: list[Any] = []

    class Context:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_args: Any) -> None:
            return None

    class Session:
        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def begin(self) -> Context:
            return Context()

    class Factory:
        def __call__(self) -> Session:
            return Session()

    async def configure(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def inspect(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"target_key": "pbp2a"}

    async def persist_operational(*_args: Any, **kwargs: Any) -> tuple[str, str]:
        records.append(kwargs["record"])
        return "operational-run", "operational-call"

    async def hang(*_args: Any, **_kwargs: Any) -> Any:
        await asyncio.sleep(0.1)
        raise AssertionError("transaction timeout did not fire")

    monkeypatch.setattr(repair, "_configure_transaction", configure)
    monkeypatch.setattr(repair, "inspect_structure_evidence", inspect)
    monkeypatch.setattr(repair, "_persist_operational_state", persist_operational)
    monkeypatch.setattr(repair, "persist_structure_evidence", hang)

    with pytest.raises(TimeoutError):
        await repair.execute_structure_evidence_repair(
            request=request,
            source={"kind": "stdin_activity_request"},
            operation_key="structure-evidence-repair-pbp2a-timeout-test",
            execute=True,
            expected_run_id=RUN_ID,
            expected_candidate_id=CANDIDATE_ID,
            expected_seed=SEED,
            transaction_timeout_seconds=0.01,
            engine_and_factory=(object(), Factory()),
        )

    assert [record.status for record in records] == ["running", "failed"]
    assert records[-1].error == {"error_type": "TimeoutError", "message": ""}
    assert records[-1].finished_at is not None
