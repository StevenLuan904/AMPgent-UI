from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from pepagent.provenance.hashing import sha256_json, sha256_text
from pepagent.structure_v2_binding import (
    STRUCTURE_V2_SOURCE_SNAPSHOT_SCHEMA,
    StructureV2PgEvidence,
    _preserve_frozen_runtime_binding,
    bind_structure_v2_request_from_pg_evidence,
)
from pepagent.workflows.structure_v2 import (
    structure_v2_receipt_contract,
    validate_structure_v2_target_request,
)

RUN_ID = uuid.UUID("20000000-0000-0000-0000-000000000001")
TARGET_ID = uuid.UUID("20000000-0000-0000-0000-000000000002")
IMPORT_CALL_ID = uuid.UUID("20000000-0000-0000-0000-000000000003")
TARGET_SEQUENCE = "ACDEFGHIKLMNPQRSTVWY"
COHORT_SHA256 = "c" * 64
STRICT_LIBRARY_SHA256 = "d" * 64


def _peptide(index: int) -> str:
    return "K" + "".join("A" if index & (1 << bit) else "C" for bit in range(9))


def _source_snapshot(index: int, sequence_sha256: str, family: str) -> dict[str, Any]:
    return {
        "schema_version": STRUCTURE_V2_SOURCE_SNAPSHOT_SCHEMA,
        "target_key": "pbp2a",
        "sequence_sha256": sequence_sha256,
        "family_key_80_80": family,
        "cohort_sha256": COHORT_SHA256,
        "strict_display_eligible": True,
        "toxinpred3_label": "Non-Toxin",
        "macrel_hemolysis_label": "low",
        "guruprasad_instability_index": 12.0 + index / 100,
        "guruprasad_instability_ood": False,
        "activity_model_support_count": 2 + index % 2,
        "strict_library_sha256": STRICT_LIBRARY_SHA256,
        "strict_library_row_sha256": sha256_json({"strict_row": index}),
        "source_candidate_id": f"pbp2a-source-{index:03d}",
        "source_result_sha256": sha256_json({"source_result": index}),
    }


def _fixture(count: int = 51) -> tuple[dict[str, Any], StructureV2PgEvidence]:
    spec = {
        "target_key": "pbp2a",
        "target": {"sequence": TARGET_SEQUENCE},
        "seed": 42,
        "bulk_evaluation_concurrency": 1,
        "rosetta_all_boltz_samples": False,
        "rosetta_top_k": 1,
    }
    run_spec = {
        "workflow_type": "CandidateStructureValidationWorkflowV2",
        "target_key": "pbp2a",
        "workflow_spec": spec,
    }
    run = SimpleNamespace(
        id=RUN_ID,
        target_id=TARGET_ID,
        spec_json=run_spec,
        spec_sha256=sha256_json(run_spec),
    )
    target = SimpleNamespace(
        id=TARGET_ID,
        sequence=TARGET_SEQUENCE,
        sequence_sha256=sha256_text(TARGET_SEQUENCE),
    )
    call = SimpleNamespace(
        id=IMPORT_CALL_ID,
        run_id=RUN_ID,
        tool_name="autoresearch-structure-cohort-import",
        tool_version="1.0.0",
        status="succeeded",
        output_sha256="e" * 64,
        input_json={
            "target_key": "pbp2a",
            "candidate_count": count,
            "cohort_id": COHORT_SHA256,
        },
    )
    candidates = []
    lifecycle_events = []
    for index in range(count):
        sequence = _peptide(index)
        sequence_sha256 = sha256_text(sequence)
        family = f"family-pbp2a-{index:03d}"
        source_snapshot = _source_snapshot(index, sequence_sha256, family)
        candidates.append(
            SimpleNamespace(
                id=uuid.uuid5(RUN_ID, f"candidate-{index}"),
                run_id=RUN_ID,
                sequence=sequence,
                sequence_sha256=sequence_sha256,
                generation=0,
                generator_call_id=IMPORT_CALL_ID,
                metadata_json={
                    "run_mode": "autoresearch_wetlab_gold_structure_escalation",
                    "target_key": "pbp2a",
                    "source_candidate_id": source_snapshot["source_candidate_id"],
                    "source_result_sha256": source_snapshot["source_result_sha256"],
                    "source_sequence_sha256": sequence_sha256,
                    "family_key_80_80": family,
                    "structure_v2_eligibility": source_snapshot,
                },
            )
        )
        lifecycle_events.extend(
            [
                SimpleNamespace(
                    id=uuid.uuid5(RUN_ID, f"generated-event-{index}"),
                    aggregate_id=candidates[-1].id,
                    aggregate_type="candidate",
                    event_type="candidate.generated",
                    payload_json={
                        "run_id": str(RUN_ID),
                        "sequence_sha256": sequence_sha256,
                    },
                    payload_sha256=sha256_json({"generated": index}),
                ),
                SimpleNamespace(
                    id=uuid.uuid5(RUN_ID, f"queued-event-{index}"),
                    aggregate_id=candidates[-1].id,
                    aggregate_type="candidate",
                    event_type="candidate.status_changed",
                    payload_json={"to": "structure_queued"},
                    payload_sha256=sha256_json({"queued": index}),
                ),
            ]
        )
    request = {
        "run_id": str(RUN_ID),
        "target_key": "pbp2a",
        "spec": spec,
        "receipt_contract": structure_v2_receipt_contract(),
        "candidates": [
            {
                "id": str(item.id),
                "family_key_80_80": item.metadata_json["family_key_80_80"],
            }
            for item in candidates[:50]
        ],
    }
    return request, StructureV2PgEvidence(
        run=run,
        target=target,
        candidates=tuple(candidates),
        tool_calls=(call,),
        lifecycle_events=tuple(lifecycle_events),
    )


def test_pg_binding_replaces_request_claims_with_strict_authoritative_evidence() -> None:
    request, evidence = _fixture()

    bound = bind_structure_v2_request_from_pg_evidence(request, evidence)

    validate_structure_v2_target_request(bound)
    assert bound["pg_eligibility_binding"]["candidate_count"] == 50
    assert bound["pg_eligibility_binding"]["fresh_eligible_family_count"] == 51
    first = bound["candidates"][0]
    assert first["eligibility"]["strict_display_eligible"] is True
    assert first["eligibility"]["toxinpred3_label"] == "Non-Toxin"
    assert first["eligibility"]["macrel_hemolysis_label"] == "low"
    assert first["eligibility"]["activity_model_support_count"] >= 2
    assert first["eligibility"]["source_evidence"]["pg_candidate_id"] == first["id"]


def test_pg_binding_rejects_literal_safety_gate_drift() -> None:
    request, evidence = _fixture()
    evidence.candidates[0].metadata_json["structure_v2_eligibility"]["toxinpred3_label"] = "Toxin"

    with pytest.raises(ValueError, match="ToxinPred3 literal gate"):
        bind_structure_v2_request_from_pg_evidence(request, evidence)


def test_pg_binding_rejects_requested_legacy_sequence_or_family() -> None:
    request, evidence = _fixture()
    first = evidence.candidates[0]
    evidence = StructureV2PgEvidence(
        run=evidence.run,
        target=evidence.target,
        candidates=evidence.candidates,
        tool_calls=evidence.tool_calls,
        lifecycle_events=evidence.lifecycle_events,
        legacy_sequence_sha256s=frozenset({first.sequence_sha256}),
        legacy_family_keys=frozenset({first.metadata_json["family_key_80_80"]}),
    )

    with pytest.raises(ValueError, match="already exists in a legacy"):
        bind_structure_v2_request_from_pg_evidence(request, evidence)


def test_pg_binding_reports_fresh_family_shortfall_after_legacy_exclusion() -> None:
    request, evidence = _fixture(count=50)
    first = evidence.candidates[0]
    evidence = StructureV2PgEvidence(
        run=evidence.run,
        target=evidence.target,
        candidates=evidence.candidates,
        tool_calls=evidence.tool_calls,
        lifecycle_events=evidence.lifecycle_events,
        legacy_sequence_sha256s=frozenset({first.sequence_sha256}),
        legacy_family_keys=frozenset(),
    )

    with pytest.raises(ValueError, match="only 49 fresh eligible families.*shortfall=1"):
        bind_structure_v2_request_from_pg_evidence(request, evidence)


def test_workflow_contract_rejects_tampered_request_eligibility() -> None:
    request, evidence = _fixture()
    bound = bind_structure_v2_request_from_pg_evidence(request, evidence)
    tampered = copy.deepcopy(bound)
    tampered["candidates"][0]["eligibility"]["activity_model_support_count"] = 1

    with pytest.raises(ValueError, match="support >=2"):
        validate_structure_v2_target_request(tampered)


def test_runtime_binding_ignores_unrelated_structure_exclusion_growth() -> None:
    request, evidence = _fixture()
    frozen = bind_structure_v2_request_from_pg_evidence(request, evidence)
    unrelated = StructureV2PgEvidence(
        run=evidence.run,
        target=evidence.target,
        candidates=evidence.candidates,
        tool_calls=evidence.tool_calls,
        lifecycle_events=evidence.lifecycle_events,
        legacy_sequence_sha256s=frozenset({"f" * 64}),
        legacy_family_keys=frozenset({"unrelated-family"}),
    )
    current = bind_structure_v2_request_from_pg_evidence(frozen, unrelated)

    assert current["pg_eligibility_binding"]["legacy_exclusion_snapshot_sha256"] != (
        frozen["pg_eligibility_binding"]["legacy_exclusion_snapshot_sha256"]
    )
    assert _preserve_frozen_runtime_binding(frozen, current) == frozen


def test_runtime_binding_still_rejects_real_sequence_or_family_intersection() -> None:
    request, evidence = _fixture()
    frozen = bind_structure_v2_request_from_pg_evidence(request, evidence)
    first = evidence.candidates[0]
    intersecting = StructureV2PgEvidence(
        run=evidence.run,
        target=evidence.target,
        candidates=evidence.candidates,
        tool_calls=evidence.tool_calls,
        lifecycle_events=evidence.lifecycle_events,
        legacy_sequence_sha256s=frozenset({first.sequence_sha256}),
        legacy_family_keys=frozenset({first.metadata_json["family_key_80_80"]}),
    )

    with pytest.raises(ValueError, match="already exists in a legacy"):
        current = bind_structure_v2_request_from_pg_evidence(frozen, intersecting)
        _preserve_frozen_runtime_binding(frozen, current)


def test_runtime_binding_rejects_current_qualification_value_drift() -> None:
    request, evidence = _fixture()
    frozen = bind_structure_v2_request_from_pg_evidence(request, evidence)
    drifted = copy.deepcopy(evidence)
    drifted.candidates[0].metadata_json["structure_v2_eligibility"][
        "guruprasad_instability_index"
    ] += 1.0
    current = bind_structure_v2_request_from_pg_evidence(frozen, drifted)

    with pytest.raises(ValueError, match="current PG eligibility binding differs from frozen"):
        _preserve_frozen_runtime_binding(frozen, current)
