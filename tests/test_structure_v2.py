from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from temporalio.converter import DataConverter

from pepagent import structure_v2_submit as submit_module
from pepagent.provenance.hashing import sha256_bytes, sha256_json, sha256_text
from pepagent.storage.object_store import StoredObject
from pepagent.structure_evidence_repair import (
    STRUCTURE_REPAIR_RECEIPT_SCHEMA,
    StructureEvidenceReceipt,
    load_structure_score_reference,
)
from pepagent.structure_v2_submit import (
    start_structure_evidence_repair_v2,
    start_structure_validation_v2,
)
from pepagent.workers import structure_v2_activities as activities
from pepagent.workers.structure_v2_temporal_worker import ROLE_CONFIG, worker_options
from pepagent.workflows import structure_v2 as workflows

RUN_ID = uuid.UUID("0c3bd48f-c25c-5268-ba91-e16108285161")
CANDIDATE_ID = uuid.UUID("184322d3-e35a-4b04-8e5d-63f7027f777c")
PARENT_CALL_ID = uuid.UUID("3d78a63d-69ef-49fa-b9ca-b0f872c02184")
AUDIT_CALL_ID = uuid.UUID("fed41955-c1d8-48f9-8f53-68bf3f412141")
SEED = 302608293


def _contract_peptide(index: int) -> str:
    return "K" + "".join("A" if index & (1 << bit) else "C" for bit in range(9))


def _bound_target_request() -> dict[str, Any]:
    target_key = "pbp2a"
    candidates: list[dict[str, Any]] = []
    eligibility_sha256s: list[str] = []
    for index in range(50):
        candidate_id = (
            CANDIDATE_ID if index == 0 else uuid.uuid5(RUN_ID, f"structure-v2-candidate-{index}")
        )
        sequence = _contract_peptide(index)
        sequence_sha256 = sha256_text(sequence)
        family = f"family-{index:02d}"
        eligibility = {
            "schema_version": "ampgent.structure-v2-candidate-eligibility.1",
            "target_key": target_key,
            "sequence_sha256": sequence_sha256,
            "family_key_80_80": family,
            "strict_display_eligible": True,
            "toxinpred3_label": "Non-Toxin",
            "macrel_hemolysis_label": "low",
            "guruprasad_instability_index": 12.0 + index / 100,
            "guruprasad_instability_ood": False,
            "activity_model_support_count": 2 + index % 2,
            "source_evidence": {
                "source_kind": "postgresql_frozen_strict_library_snapshot",
                "cohort_sha256": "b" * 64,
                "strict_library_sha256": "c" * 64,
                "strict_library_row_sha256": sha256_json({"row": index}),
                "source_candidate_id": f"source-{index:02d}",
                "source_result_sha256": sha256_json({"result": index}),
                "pg_candidate_id": str(candidate_id),
                "pg_import_tool_call_id": str(uuid.uuid5(RUN_ID, f"import-call-{index}")),
                "pg_import_tool_output_sha256": "d" * 64,
                "pg_candidate_generated_event_id": str(
                    uuid.uuid5(RUN_ID, f"generated-event-{index}")
                ),
                "pg_candidate_generated_payload_sha256": sha256_json({"generated": index}),
                "pg_structure_queued_event_id": str(uuid.uuid5(RUN_ID, f"queued-event-{index}")),
                "pg_structure_queued_payload_sha256": sha256_json({"queued": index}),
            },
        }
        eligibility_sha256 = sha256_json(eligibility)
        eligibility_sha256s.append(eligibility_sha256)
        candidates.append(
            {
                "id": str(candidate_id),
                "sequence": sequence,
                "sequence_sha256": sequence_sha256,
                "generation": 0,
                "target_key": target_key,
                "family_key_80_80": family,
                "eligibility": eligibility,
                "eligibility_sha256": eligibility_sha256,
            }
        )
    binding = {
        "schema_version": "ampgent.structure-v2-pg-binding.1",
        "source_database": "postgresql",
        "run_id": str(RUN_ID),
        "target_id": str(uuid.uuid5(RUN_ID, "target")),
        "target_key": target_key,
        "target_sequence_sha256": "e" * 64,
        "candidate_count": 50,
        "distinct_family_count": 50,
        "candidate_eligibility_sha256s": eligibility_sha256s,
        "fresh_eligible_family_count": 50,
        "legacy_exclusion_snapshot_sha256": sha256_json(
            {"sequence_sha256s": [], "family_key_80_80": []}
        ),
    }
    binding["binding_sha256"] = sha256_json(binding)
    return {
        "run_id": str(RUN_ID),
        "target_key": target_key,
        "spec": {
            "target_key": target_key,
            "seed": 42,
            "bulk_evaluation_concurrency": 1,
            "rosetta_all_boltz_samples": False,
            "rosetta_top_k": 1,
        },
        "receipt_contract": workflows.structure_v2_receipt_contract(),
        "candidates": candidates,
        "pg_eligibility_binding": binding,
    }


def _stored(path: str) -> dict[str, Any]:
    payload = path.encode()
    digest = sha256_bytes(payload)
    return {
        "path": path,
        "sha256": digest,
        "size_bytes": len(payload),
        "uri": f"s3://pepagent/sha256/{digest[:2]}/{digest}",
        "media_type": "application/octet-stream",
    }


def _pbp_shaped_result() -> dict[str, Any]:
    return {
        "candidate": {
            "id": str(CANDIDATE_ID),
            "sequence": "KRWWKWWRR",
            "sequence_sha256": "a" * 64,
            "generation": 0,
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
            "limitations": ["PBP2a-shaped v2 history benchmark"],
            "decoys": [
                {
                    "decoy_id": f"pbp2a-{index:03d}",
                    "dG_separated": -51.0 + index / 1000,
                    "engine_output": "x" * 5_000,
                }
                for index in range(200)
            ],
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
            "tool_name": "pyrosetta-flexpepdock-interface-analyzer",
            "tool_version": "2026.29+releasequarterly.80a0635615",
            "environment_sha256": "b" * 64,
            "weights_sha256": "c" * 64,
            "model_uri": "rosetta://ref2015",
            "attempt": 1,
            "raw_output_artifact": _stored("score.json"),
            "environment_artifact": _stored("environment.json"),
            "engine_artifacts": [_stored(f"decoy-{index:03d}.pdb") for index in range(403)],
        },
    }


def _reference(result: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    encoded = activities.canonical_structure_result_bytes(result)
    artifact = StoredObject(
        sha256=sha256_bytes(encoded),
        size_bytes=len(encoded),
        uri=f"s3://pepagent/sha256/{sha256_bytes(encoded)}",
        media_type="application/json",
    )
    return (
        activities.build_structure_score_reference(
            run_id=str(RUN_ID),
            result=result,
            artifact=artifact,
        ),
        encoded,
    )


class _MemoryStore:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def get_bytes(self, uri: str) -> bytes:
        assert uri.startswith("s3://pepagent/")
        return self.payload


@pytest.mark.asyncio
async def test_pbp_shaped_score_result_becomes_thin_history_pointer() -> None:
    result = _pbp_shaped_result()
    reference, encoded = _reference(result)
    converter = DataConverter.default
    full_payload = (await converter.encode([result]))[0]
    reference_payload = (await converter.encode([reference]))[0]

    assert len(encoded) > 1_000_000
    assert reference["summary"]["decoy_count"] == 200
    assert reference["summary"]["artifact_observation_count"] == 405
    assert len(reference_payload.data) < 2_500
    assert len(reference_payload.data) < len(full_payload.data) / 100

    resolved = await load_structure_score_reference(
        reference,
        object_store=_MemoryStore(encoded),
    )
    assert resolved == {"run_id": str(RUN_ID), "rosetta_result": result}


@pytest.mark.asyncio
async def test_score_reference_rejects_bound_identity_drift() -> None:
    result = _pbp_shaped_result()
    reference, encoded = _reference(result)
    reference["candidate_sequence_sha256"] = "d" * 64

    with pytest.raises(ValueError, match="binding differs"):
        await load_structure_score_reference(
            reference,
            object_store=_MemoryStore(encoded),
        )


@pytest.mark.asyncio
async def test_score_activity_stores_full_result_and_returns_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _pbp_shaped_result()
    encoded = activities.canonical_structure_result_bytes(result)
    heartbeats: list[dict[str, Any]] = []

    class _Store:
        def put_bytes(self, payload: bytes, media_type: str) -> StoredObject:
            assert payload == encoded
            assert media_type == "application/json"
            return StoredObject(
                sha256=sha256_bytes(payload),
                size_bytes=len(payload),
                uri=f"s3://pepagent/sha256/{sha256_bytes(payload)}",
                media_type=media_type,
            )

    monkeypatch.setattr(
        activities,
        "_score_rosetta_payload_v2",
        AsyncMock(return_value=result),
    )
    monkeypatch.setattr(activities, "ContentAddressedObjectStore", _Store)
    monkeypatch.setattr(activities.activity, "heartbeat", heartbeats.append)

    reference = await activities.score_rosetta_complex_v2({"run_id": str(RUN_ID)})

    assert reference["result_sha256"] == sha256_json(result)
    assert [item["stage"] for item in heartbeats] == [
        "score_result_validated",
        "score_result_stored",
    ]
    assert "decoys" not in json.dumps(reference)


@pytest.mark.asyncio
async def test_score_retry_reuses_cross_host_cas_pointer_from_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _pbp_shaped_result()
    reference, _ = _reference(result)
    score = AsyncMock()
    heartbeats: list[dict[str, Any]] = []
    monkeypatch.setattr(activities, "_score_rosetta_payload_v2", score)
    monkeypatch.setattr(
        activities,
        "load_structure_score_reference",
        AsyncMock(return_value={"run_id": str(RUN_ID), "rosetta_result": result}),
    )
    monkeypatch.setattr(
        activities.activity,
        "info",
        lambda: SimpleNamespace(
            attempt=2,
            heartbeat_details=[{"score_reference": reference}],
        ),
    )
    monkeypatch.setattr(activities.activity, "heartbeat", heartbeats.append)

    recovered = await activities.score_rosetta_complex_v2(
        {
            "run_id": str(RUN_ID),
            "seed": SEED,
            "structure": {"candidate": result["candidate"]},
        }
    )

    assert recovered == reference
    score.assert_not_awaited()
    assert heartbeats[-1]["stage"] == "score_reference_reused"


def test_v2_rosetta_requires_one_absolute_same_host_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    monkeypatch.delenv(activities.STRUCTURE_V2_SHARED_ROOT_ENV, raising=False)
    with pytest.raises(RuntimeError, match="same-host shared root"):
        activities.structure_v2_shared_rosetta_root()
    monkeypatch.setenv(activities.STRUCTURE_V2_SHARED_ROOT_ENV, "relative/root")
    with pytest.raises(ValueError, match="absolute path"):
        activities.structure_v2_shared_rosetta_root()
    shared = tmp_path / "shared-rosetta"
    monkeypatch.setenv(activities.STRUCTURE_V2_SHARED_ROOT_ENV, str(shared))
    assert activities.structure_v2_shared_rosetta_root() == shared
    assert shared.is_dir()


class _FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction()


class _FakeFactory:
    def __call__(self) -> _FakeSession:
        return _FakeSession()


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_persist_activity_returns_thin_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _pbp_shaped_result()
    reference, _ = _reference(result)
    resolved = {"run_id": str(RUN_ID), "rosetta_result": result}
    receipt = StructureEvidenceReceipt(
        schema_version=STRUCTURE_REPAIR_RECEIPT_SCHEMA,
        run_id=str(RUN_ID),
        candidate_id=str(CANDIDATE_ID),
        tool_call_id=str(uuid.uuid4()),
        idempotency_key="f" * 64,
        reused_tool_call=False,
        evaluation_count=8,
        artifact_edge_count=405,
        dependency_count=2,
        candidate_status="rosetta_scored",
        result_sha256=sha256_json(result),
    )
    engine = _FakeEngine()
    heartbeats: list[dict[str, Any]] = []
    configure = AsyncMock()
    persist = AsyncMock(return_value=receipt)
    monkeypatch.setattr(
        activities,
        "load_structure_score_reference",
        AsyncMock(return_value=resolved),
    )
    monkeypatch.setattr(activities, "_repair_session_factory", lambda: (engine, _FakeFactory()))
    monkeypatch.setattr(activities, "_configure_transaction", configure)
    monkeypatch.setattr(activities, "persist_structure_evidence", persist)
    monkeypatch.setattr(activities.activity, "heartbeat", heartbeats.append)

    thin = await activities.persist_rosetta_evidence_v2(
        {"run_id": str(RUN_ID), "score_reference": reference}
    )

    assert thin["tool_call_id"] == receipt.tool_call_id
    assert thin["artifact_edge_count"] == 405
    assert thin["score_reference_sha256"] == sha256_json(reference)
    assert len((await DataConverter.default.encode([thin]))[0].data) < 2_500
    assert engine.disposed is True
    configure.assert_awaited_once()
    persist.assert_awaited_once()
    assert [item["stage"] for item in heartbeats] == [
        "score_reference_loading",
        "score_payload_validated",
        "scientific_transaction_started",
        "scientific_transaction_committed",
    ]


@pytest.mark.asyncio
async def test_runtime_pg_preflight_rejects_submission_binding_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _bound_target_request()
    drifted = json.loads(json.dumps(request))
    drifted["pg_eligibility_binding"]["fresh_eligible_family_count"] = 51
    engine = _FakeEngine()
    monkeypatch.setattr(activities, "_repair_session_factory", lambda: (engine, object()))
    monkeypatch.setattr(
        activities,
        "bind_structure_v2_target_request",
        AsyncMock(return_value=drifted),
    )
    monkeypatch.setattr(activities.activity, "heartbeat", lambda _value: None)

    with pytest.raises(ValueError, match="current PG eligibility binding"):
        await activities.preflight_structure_v2_target_request_v2(request)
    assert engine.disposed is True


def test_structure_v2_worker_roles_are_disjoint_and_max_one() -> None:
    assert len({role.task_queue for role in ROLE_CONFIG.values()}) == 3
    assert not ROLE_CONFIG["structure_v2_workflow"].activities
    assert not ROLE_CONFIG["structure_v2_rosetta"].workflows
    assert not ROLE_CONFIG["structure_v2_persist"].workflows
    assert (
        ROLE_CONFIG["structure_v2_rosetta"].activities
        != ROLE_CONFIG["structure_v2_persist"].activities
    )
    for role_name in ROLE_CONFIG:
        options = worker_options(role_name)
        assert options["max_concurrent_activities"] == 1
        assert options["max_concurrent_workflow_tasks"] == 1
        assert options["disable_eager_activity_execution"] is True


@pytest.mark.asyncio
async def test_parent_child_and_submission_contract_use_sixty_second_wft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    activity_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    request = _bound_target_request()

    async def execute_activity(*args: Any, **kwargs: Any) -> Any:
        activity_calls.append((args, kwargs))
        if args[0] == "preflight_structure_v2_target_request_v2":
            return request["pg_eligibility_binding"]
        return None

    async def execute_child(*args: Any, **kwargs: Any) -> dict[str, Any]:
        child_calls.append((args, kwargs))
        candidate = args[1]["candidate"]
        return {
            "candidate_id": candidate["id"],
            "family_key_80_80": candidate["family_key_80_80"],
            "status": "succeeded",
            "structure_receipt_count": 3,
            "rosetta_receipts": [
                {
                    "candidate_id": candidate["id"],
                    "tool_call_id": f"tool-{candidate['id']}",
                    "result_sha256": "a" * 64,
                    "candidate_status": "rosetta_scored",
                    "evaluation_count": 8,
                    "artifact_edge_count": 405,
                    "primary_dG_separated_reu": -51.0,
                    "structure_support": "conflicting",
                    "dG_le_minus_50": True,
                }
            ],
        }

    monkeypatch.setattr(workflows.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(workflows.workflow, "execute_child_workflow", execute_child)
    monkeypatch.setattr(
        workflows.workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="new-structure-v2"),
    )
    await workflows.CandidateStructureValidationWorkflowV2().run(request)

    assert activity_calls[0][0][0] == "preflight_structure_v2_target_request_v2"
    assert child_calls[0][1]["task_timeout"].total_seconds() == 60
    assert len(child_calls) == 50
    finalize_request = next(args[1] for args, _ in activity_calls if args[0] == "finalize_run")
    assert finalize_request["persisted_structure_count"] == 150
    assert finalize_request["persisted_rosetta_receipt_count"] == 50
    assert finalize_request["rosetta_receipt_summary"]["complete_dG_candidate_receipts"] == 50
    assert finalize_request["rosetta_receipt_summary"]["dG_le_minus_50_count"] == 50

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        async def start_workflow(self, *args: Any, **kwargs: Any) -> object:
            self.calls.append((args, kwargs))
            return object()

    client = _Client()
    bind = AsyncMock(return_value=request)
    monkeypatch.setattr(submit_module, "bind_structure_v2_target_request", bind)
    await start_structure_validation_v2(
        client,  # type: ignore[arg-type]
        workflow_id="new-structure-v2",
        request=request,
    )
    assert client.calls[0][0][0] == "CandidateStructureValidationWorkflowV2"
    assert client.calls[0][1]["task_timeout"].total_seconds() == 60
    assert client.calls[0][1]["task_queue"] == workflows.STRUCTURE_V2_WORKFLOW_QUEUE
    assert client.calls[0][0][1] == request
    bind.assert_awaited_once()


def test_target_contract_rejects_current_partial_baseline_as_final() -> None:
    request = {
        "run_id": str(RUN_ID),
        "receipt_contract": workflows.structure_v2_receipt_contract(),
        "candidates": [{"id": "current-pg-row", "family_key_80_80": "one-family"}],
    }
    with pytest.raises(ValueError, match="exactly 50"):
        workflows.validate_structure_v2_target_request(request)
    contract = workflows.structure_v2_receipt_contract()
    assert contract["required_candidate_receipts"] == 50
    assert contract["required_distinct_families"] == 50
    assert contract["structure_support_interpretation"] == ("independent_from_dG_threshold")
    assert contract["dG_threshold_reu"] == -50.0
    assert contract["wetlab_stop_condition_metric"] == (
        "complete_dG_distinct_family_candidate_receipts"
    )
    assert contract["structure_support_is_stop_condition"] is False
    assert contract["dG_threshold_is_stop_condition"] is False


@pytest.mark.asyncio
async def test_repair_successor_requires_a_new_workflow_id() -> None:
    predecessor = {
        "workflow_id": "legacy-child",
        "run_id": "legacy-temporal-run",
        "activity_id": "11",
        "reason": "legacy persist activity did not reach a terminal event",
    }
    with pytest.raises(ValueError, match="new workflow ID"):
        await start_structure_evidence_repair_v2(
            SimpleNamespace(),  # type: ignore[arg-type]
            workflow_id="legacy-child",
            run_id=str(RUN_ID),
            score_reference={"result_sha256": "a" * 64},
            predecessor=predecessor,
        )
