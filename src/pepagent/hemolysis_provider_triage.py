from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json

TRIAGE_SCHEMA = "ampgent.hemolysis-second-family-triage.1"


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value.strip()


def acceptance_artifacts(witness: Mapping[str, Any]) -> dict[str, str]:
    candidates = witness.get("candidates")
    decision = witness.get("decision")
    if not isinstance(candidates, list) or not isinstance(decision, Mapping):
        raise ValueError("triage witness has no candidate matrix or decision")
    lineage = [
        {
            "candidate_id": item.get("candidate_id"),
            "training_sources": item.get("training_sources"),
            "independence_status": item.get("independence_status"),
        }
        for item in candidates
    ]
    license_runtime = [
        {
            "candidate_id": item.get("candidate_id"),
            "commercial_internal_execution_confirmed": item.get(
                "commercial_internal_execution_confirmed"
            ),
            "immutable_weights_available": item.get("immutable_weights_available"),
            "probability_semantics_reproducible": item.get(
                "probability_semantics_reproducible"
            ),
            "sequence_first_compatible": item.get("sequence_first_compatible"),
        }
        for item in candidates
    ]
    return {
        "primary_source_candidate_matrix_sha256": sha256_json(candidates),
        "training_dataset_independence_witness_sha256": sha256_json(lineage),
        "license_and_runtime_feasibility_witness_sha256": sha256_json(license_runtime),
        "selected_provider_benchmark_contract_or_no_candidate_witness_sha256": sha256_json(
            decision
        ),
    }


def validate_hemolysis_provider_triage(witness: Mapping[str, Any]) -> dict[str, str]:
    if witness.get("schema_version") != TRIAGE_SCHEMA:
        raise ValueError("hemolysis provider triage schema is invalid")
    candidates = witness.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("hemolysis provider triage requires candidates")

    candidate_ids: set[str] = set()
    qualified: list[str] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            raise ValueError("hemolysis provider candidate must be an object")
        candidate_id = _text(item.get("candidate_id"), label="candidate_id")
        if candidate_id in candidate_ids:
            raise ValueError(f"duplicate hemolysis provider candidate: {candidate_id}")
        candidate_ids.add(candidate_id)
        sources = item.get("primary_sources")
        reasons = item.get("reason_codes")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"candidate has no primary sources: {candidate_id}")
        if not isinstance(reasons, list) or not reasons:
            raise ValueError(f"candidate has no reason codes: {candidate_id}")
        is_qualified = all(
            (
                item.get("independence_status") == "passed",
                item.get("commercial_internal_execution_confirmed") is True,
                item.get("immutable_weights_available") is True,
                item.get("probability_semantics_reproducible") is True,
                item.get("sequence_first_compatible") is True,
            )
        )
        if is_qualified:
            qualified.append(candidate_id)
            if item.get("qualification_status") != "qualified":
                raise ValueError(f"qualified candidate status drifted: {candidate_id}")
        elif item.get("qualification_status") != "rejected":
            raise ValueError(f"unqualified candidate was not rejected: {candidate_id}")

    decision = witness.get("decision")
    if not isinstance(decision, Mapping):
        raise ValueError("triage witness has no decision")
    selected = decision.get("selected_provider_id")
    if qualified:
        if selected not in qualified:
            raise ValueError("triage decision did not select a qualified provider")
    elif selected is not None or decision.get("outcome") != "no_public_candidate_qualified":
        raise ValueError("no-candidate triage decision is inconsistent")
    if decision.get("safety_gate_lowered") is not False:
        raise ValueError("hemolysis triage must not lower the safety gate")

    computed = acceptance_artifacts(witness)
    declared = witness.get("acceptance_artifacts")
    if declared != computed:
        raise ValueError("hemolysis triage acceptance artifact hashes drifted")
    return computed
