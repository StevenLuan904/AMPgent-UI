from __future__ import annotations

import copy
import math
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select

from pepagent.db.models import Candidate, ExperimentRun, LifecycleEvent, Target, ToolCall
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_json, sha256_text

STRUCTURE_V2_ELIGIBILITY_SCHEMA = "ampgent.structure-v2-candidate-eligibility.1"
STRUCTURE_V2_PG_BINDING_SCHEMA = "ampgent.structure-v2-pg-binding.1"
STRUCTURE_V2_SOURCE_SNAPSHOT_SCHEMA = "ampgent.structure-v2-source-snapshot.1"
STRUCTURE_COHORT_IMPORT_TOOL = "autoresearch-structure-cohort-import"
STRUCTURE_COHORT_IMPORT_VERSION = "1.0.0"
STRUCTURE_ESCALATION_RUN_MODE = "autoresearch_wetlab_gold_structure_escalation"
STRUCTURE_V2_WORKFLOW_TYPE = "CandidateStructureValidationWorkflowV2"
LEGACY_STRUCTURE_WORKFLOW_TYPE = "CandidateStructureValidationWorkflow"
PG_SOURCE_SNAPSHOT_KEY = "structure_v2_eligibility"
REQUIRED_CANDIDATE_COUNT = 50
_HEX = frozenset("0123456789abcdef")
_NON_TOXIN_LABELS = frozenset({"non-toxin", "nontoxin"})


@dataclass(frozen=True)
class StructureV2PgEvidence:
    run: ExperimentRun
    target: Target
    candidates: tuple[Candidate, ...]
    tool_calls: tuple[ToolCall, ...]
    lifecycle_events: tuple[LifecycleEvent, ...] = ()
    legacy_sequence_sha256s: frozenset[str] = frozenset()
    legacy_family_keys: frozenset[str] = frozenset()


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and not (set(text) - _HEX)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"structure v2 {field} must be an object")
    return value


def _normalized_toxicity_label(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _candidate_ids(request: Mapping[str, Any]) -> tuple[uuid.UUID, ...]:
    candidates = request.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != REQUIRED_CANDIDATE_COUNT:
        raise ValueError("structure v2 requires exactly 50 candidates per target")
    result: list[uuid.UUID] = []
    for item in candidates:
        row = _mapping(item, "candidate")
        try:
            result.append(uuid.UUID(str(row.get("id", ""))))
        except (TypeError, ValueError) as error:
            raise ValueError("structure v2 candidate ID must be a UUID") from error
    if len(set(result)) != len(result):
        raise ValueError("structure v2 target candidates must have distinct identities")
    return tuple(result)


async def _load_pg_evidence(
    request: Mapping[str, Any],
    *,
    session_factory: Callable[[], Any],
) -> StructureV2PgEvidence:
    try:
        run_id = uuid.UUID(str(request.get("run_id", "")))
    except (TypeError, ValueError) as error:
        raise ValueError("structure v2 run ID must be a UUID") from error
    _candidate_ids(request)
    async with session_factory() as session:
        run = await session.scalar(select(ExperimentRun).where(ExperimentRun.id == run_id))
        if run is None:
            raise KeyError(f"structure v2 run not found: {run_id}")
        target = await session.scalar(select(Target).where(Target.id == run.target_id))
        if target is None:
            raise KeyError(f"structure v2 target not found: {run.target_id}")
        candidates = tuple(
            await session.scalars(select(Candidate).where(Candidate.run_id == run_id))
        )
        call_ids = {item.generator_call_id for item in candidates if item.generator_call_id}
        tool_calls = tuple(await session.scalars(select(ToolCall).where(ToolCall.id.in_(call_ids))))
        lifecycle_events = tuple(
            await session.scalars(
                select(LifecycleEvent).where(
                    LifecycleEvent.aggregate_type == "candidate",
                    LifecycleEvent.aggregate_id.in_([item.id for item in candidates]),
                )
            )
        )
        legacy_candidates = tuple(
            await session.scalars(
                select(Candidate)
                .join(ExperimentRun, Candidate.run_id == ExperimentRun.id)
                .where(
                    ExperimentRun.id != run_id,
                    or_(
                        Candidate.metadata_json["run_mode"].as_string()
                        == STRUCTURE_ESCALATION_RUN_MODE,
                        ExperimentRun.spec_json["workflow_type"]
                        .as_string()
                        .in_(
                            (
                                LEGACY_STRUCTURE_WORKFLOW_TYPE,
                                STRUCTURE_V2_WORKFLOW_TYPE,
                            )
                        ),
                        ExperimentRun.temporal_workflow_id.like("pepagent-structure-%"),
                    ),
                )
            )
        )
    legacy_families = {
        str(item.metadata_json.get("family_key_80_80", ""))
        for item in legacy_candidates
        if isinstance(item.metadata_json, Mapping) and item.metadata_json.get("family_key_80_80")
    }
    return StructureV2PgEvidence(
        run=run,
        target=target,
        candidates=candidates,
        tool_calls=tool_calls,
        lifecycle_events=lifecycle_events,
        legacy_sequence_sha256s=frozenset(item.sequence_sha256 for item in legacy_candidates),
        legacy_family_keys=frozenset(legacy_families),
    )


def _validate_source_snapshot(
    *,
    row: Candidate,
    metadata: Mapping[str, Any],
    target_key: str,
    family_key: str,
    cohort_id: str,
) -> dict[str, Any]:
    snapshot = _mapping(metadata.get(PG_SOURCE_SNAPSHOT_KEY), PG_SOURCE_SNAPSHOT_KEY)
    if snapshot.get("schema_version") != STRUCTURE_V2_SOURCE_SNAPSHOT_SCHEMA:
        raise ValueError("structure v2 PG source snapshot schema differs")
    if snapshot.get("target_key") != target_key:
        raise ValueError("structure v2 PG source snapshot crossed target branches")
    if snapshot.get("sequence_sha256") != row.sequence_sha256:
        raise ValueError("structure v2 PG source snapshot sequence differs")
    if snapshot.get("family_key_80_80") != family_key:
        raise ValueError("structure v2 PG source snapshot family differs")
    if snapshot.get("cohort_sha256") != cohort_id:
        raise ValueError("structure v2 PG source snapshot cohort differs")
    if snapshot.get("strict_display_eligible") is not True:
        raise ValueError("structure v2 candidate is not strict-display eligible")
    toxicity = _normalized_toxicity_label(snapshot.get("toxinpred3_label"))
    if toxicity not in _NON_TOXIN_LABELS:
        raise ValueError("structure v2 candidate fails the ToxinPred3 literal gate")
    hemolysis = str(snapshot.get("macrel_hemolysis_label", "")).strip().lower()
    if hemolysis != "low":
        raise ValueError("structure v2 candidate fails the MACREL hemolysis literal gate")
    instability = snapshot.get("guruprasad_instability_index")
    if isinstance(instability, bool) or not isinstance(instability, (int, float)):
        raise ValueError("structure v2 candidate lacks numeric Guruprasad instability")
    instability = float(instability)
    if not math.isfinite(instability) or instability >= 50.0:
        raise ValueError("structure v2 candidate fails the instability <50 gate")
    if snapshot.get("guruprasad_instability_ood") is not False:
        raise ValueError("structure v2 candidate has Guruprasad instability OOD evidence")
    support = snapshot.get("activity_model_support_count")
    if isinstance(support, bool) or not isinstance(support, int) or not 2 <= support <= 3:
        raise ValueError("structure v2 candidate lacks activity-model support >=2")
    if not _is_sha256(snapshot.get("strict_library_row_sha256")):
        raise ValueError("structure v2 candidate lacks its frozen strict-library row identity")
    if not _is_sha256(snapshot.get("strict_library_sha256")):
        raise ValueError("structure v2 candidate lacks its frozen strict-library identity")
    if not _is_sha256(snapshot.get("source_result_sha256")):
        raise ValueError("structure v2 candidate lacks its source-result identity")
    if snapshot.get("source_result_sha256") != metadata.get("source_result_sha256"):
        raise ValueError("structure v2 source-result identity differs from PG candidate metadata")
    if str(snapshot.get("source_candidate_id", "")) != str(metadata.get("source_candidate_id", "")):
        raise ValueError("structure v2 source candidate identity differs")
    if not str(snapshot.get("source_candidate_id", "")):
        raise ValueError("structure v2 source candidate identity is empty")
    return {
        "schema_version": STRUCTURE_V2_ELIGIBILITY_SCHEMA,
        "target_key": target_key,
        "sequence_sha256": row.sequence_sha256,
        "family_key_80_80": family_key,
        "strict_display_eligible": True,
        "toxinpred3_label": str(snapshot["toxinpred3_label"]),
        "macrel_hemolysis_label": str(snapshot["macrel_hemolysis_label"]),
        "guruprasad_instability_index": instability,
        "guruprasad_instability_ood": False,
        "activity_model_support_count": support,
        "source_evidence": {
            "source_kind": "postgresql_frozen_strict_library_snapshot",
            "cohort_sha256": cohort_id,
            "strict_library_sha256": str(snapshot["strict_library_sha256"]),
            "strict_library_row_sha256": str(snapshot["strict_library_row_sha256"]),
            "source_candidate_id": str(snapshot["source_candidate_id"]),
            "source_result_sha256": str(snapshot["source_result_sha256"]),
        },
    }


def _valid_import_call(
    call: ToolCall,
    run_id: uuid.UUID,
    target_key: str,
) -> bool:
    if (
        call.run_id != run_id
        or call.tool_name != STRUCTURE_COHORT_IMPORT_TOOL
        or call.tool_version != STRUCTURE_COHORT_IMPORT_VERSION
        or str(call.status) != "succeeded"
        or not _is_sha256(call.output_sha256)
        or not isinstance(call.input_json, Mapping)
    ):
        return False
    try:
        candidate_count = int(call.input_json.get("candidate_count", 0))
    except (TypeError, ValueError):
        return False
    return (
        call.input_json.get("target_key") == target_key
        and candidate_count >= REQUIRED_CANDIDATE_COUNT
        and _is_sha256(call.input_json.get("cohort_id"))
    )


def _candidate_lifecycle_witness(
    row: Candidate,
    events: tuple[LifecycleEvent, ...],
) -> dict[str, str] | None:
    generated = [
        event
        for event in events
        if event.aggregate_id == row.id
        and event.aggregate_type == "candidate"
        and event.event_type == "candidate.generated"
        and isinstance(event.payload_json, Mapping)
        and event.payload_json.get("run_id") == str(row.run_id)
        and event.payload_json.get("sequence_sha256") == row.sequence_sha256
        and _is_sha256(event.payload_sha256)
    ]
    queued = [
        event
        for event in events
        if event.aggregate_id == row.id
        and event.aggregate_type == "candidate"
        and event.event_type == "candidate.status_changed"
        and isinstance(event.payload_json, Mapping)
        and event.payload_json.get("to") == "structure_queued"
        and _is_sha256(event.payload_sha256)
    ]
    if len(generated) != 1 or len(queued) != 1:
        return None
    return {
        "pg_candidate_generated_event_id": str(generated[0].id),
        "pg_candidate_generated_payload_sha256": generated[0].payload_sha256,
        "pg_structure_queued_event_id": str(queued[0].id),
        "pg_structure_queued_payload_sha256": queued[0].payload_sha256,
    }


def bind_structure_v2_request_from_pg_evidence(
    request: Mapping[str, Any],
    evidence: StructureV2PgEvidence,
) -> dict[str, Any]:
    """Replace request claims with exact candidate qualification evidence read from PG."""

    candidate_ids = _candidate_ids(request)
    target_key = str(request.get("target_key", "")).strip().lower()
    if not target_key or request.get("target_key") != target_key:
        raise ValueError("structure v2 target key must be non-empty normalized lowercase")
    spec = _mapping(request.get("spec"), "spec")
    if spec.get("target_key") != target_key:
        raise ValueError("structure v2 workflow spec crossed target branches")
    run_spec = _mapping(evidence.run.spec_json, "PG run spec")
    if run_spec.get("target_key") != target_key:
        raise ValueError("structure v2 PG run crossed target branches")
    if run_spec.get("workflow_type") != STRUCTURE_V2_WORKFLOW_TYPE:
        raise ValueError("structure v2 PG run is not a versioned successor run")
    if run_spec.get("workflow_spec") != dict(spec):
        raise ValueError("structure v2 workflow spec differs from its PG run binding")
    if evidence.run.spec_sha256 != sha256_json(evidence.run.spec_json):
        raise ValueError("structure v2 PG run spec identity differs")
    if evidence.run.target_id != evidence.target.id:
        raise ValueError("structure v2 PG target binding differs")
    target_spec = _mapping(spec.get("target"), "target spec")
    target_sequence = "".join(str(target_spec.get("sequence", "")).split()).upper()
    if (
        target_sequence != evidence.target.sequence
        or sha256_text(target_sequence) != evidence.target.sequence_sha256
    ):
        raise ValueError("structure v2 target sequence differs from its PG target")

    by_candidate_id = {row.id: row for row in evidence.candidates}
    if not set(candidate_ids) <= set(by_candidate_id):
        raise ValueError("structure v2 candidate set differs from its PG run binding")
    by_call_id = {call.id: call for call in evidence.tool_calls}
    incoming = {
        uuid.UUID(str(_mapping(item, "candidate")["id"])): _mapping(item, "candidate")
        for item in request["candidates"]
    }
    fresh_families: set[str] = set()
    for row in evidence.candidates:
        metadata = row.metadata_json
        if not isinstance(metadata, Mapping):
            continue
        family = str(metadata.get("family_key_80_80", ""))
        if (
            not family
            or row.sequence_sha256 in evidence.legacy_sequence_sha256s
            or family in evidence.legacy_family_keys
            or metadata.get("target_key") != target_key
            or metadata.get("source_sequence_sha256") != row.sequence_sha256
        ):
            continue
        call = by_call_id.get(row.generator_call_id)
        if (
            call is None
            or not _valid_import_call(call, evidence.run.id, target_key)
            or _candidate_lifecycle_witness(row, evidence.lifecycle_events) is None
        ):
            continue
        cohort_id = str(call.input_json.get("cohort_id", ""))
        try:
            _validate_source_snapshot(
                row=row,
                metadata=metadata,
                target_key=target_key,
                family_key=family,
                cohort_id=cohort_id,
            )
        except (TypeError, ValueError):
            continue
        fresh_families.add(family)
    if len(fresh_families) < REQUIRED_CANDIDATE_COUNT:
        shortfall = REQUIRED_CANDIDATE_COUNT - len(fresh_families)
        raise ValueError(
            "structure v2 target has only "
            f"{len(fresh_families)} fresh eligible families after legacy exclusion; "
            f"shortfall={shortfall}"
        )
    bound_candidates: list[dict[str, Any]] = []
    binding_hashes: list[str] = []
    observed_families: set[str] = set()
    for candidate_id in candidate_ids:
        row = by_candidate_id[candidate_id]
        source = incoming[candidate_id]
        if row.run_id != evidence.run.id:
            raise ValueError("structure v2 candidate crossed PG runs")
        if sha256_text(row.sequence) != row.sequence_sha256:
            raise ValueError("structure v2 PG candidate sequence identity differs")
        metadata = _mapping(row.metadata_json, "PG candidate metadata")
        if metadata.get("run_mode") != STRUCTURE_ESCALATION_RUN_MODE:
            raise ValueError("structure v2 candidate lacks the structure-escalation run mode")
        if metadata.get("target_key") != target_key:
            raise ValueError("structure v2 candidate metadata crossed target branches")
        if metadata.get("source_sequence_sha256") != row.sequence_sha256:
            raise ValueError("structure v2 candidate source sequence differs")
        family_key = str(metadata.get("family_key_80_80", ""))
        if not family_key or source.get("family_key_80_80") != family_key:
            raise ValueError("structure v2 requested family differs from PG evidence")
        if family_key in observed_families:
            raise ValueError("structure v2 target requires 50 distinct 80/80 families")
        observed_families.add(family_key)
        if (
            row.sequence_sha256 in evidence.legacy_sequence_sha256s
            or family_key in evidence.legacy_family_keys
        ):
            raise ValueError(
                "structure v2 candidate/family already exists in a legacy structure run"
            )
        for field, expected in (
            ("sequence", row.sequence),
            ("sequence_sha256", row.sequence_sha256),
            ("generation", row.generation),
            ("target_key", target_key),
        ):
            if field in source and source[field] != expected:
                raise ValueError(f"structure v2 requested candidate {field} differs from PG")
        if row.generator_call_id is None or row.generator_call_id not in by_call_id:
            raise ValueError("structure v2 candidate lacks its PG cohort-import ToolCall")
        call = by_call_id[row.generator_call_id]
        if not _valid_import_call(call, evidence.run.id, target_key):
            raise ValueError("structure v2 PG cohort-import ToolCall is not authoritative")
        lifecycle_witness = _candidate_lifecycle_witness(
            row,
            evidence.lifecycle_events,
        )
        if lifecycle_witness is None:
            raise ValueError("structure v2 candidate lacks its PG lifecycle binding")
        call_input = _mapping(call.input_json, "cohort-import ToolCall input")
        cohort_id = str(call_input.get("cohort_id", ""))
        if (
            call_input.get("target_key") != target_key
            or int(call_input.get("candidate_count", 0)) < REQUIRED_CANDIDATE_COUNT
            or not _is_sha256(cohort_id)
        ):
            raise ValueError("structure v2 PG cohort-import ToolCall input differs")
        eligibility = _validate_source_snapshot(
            row=row,
            metadata=metadata,
            target_key=target_key,
            family_key=family_key,
            cohort_id=cohort_id,
        )
        eligibility["source_evidence"].update(
            {
                "pg_candidate_id": str(row.id),
                "pg_import_tool_call_id": str(call.id),
                "pg_import_tool_output_sha256": str(call.output_sha256),
                **lifecycle_witness,
            }
        )
        eligibility_sha256 = sha256_json(eligibility)
        binding_hashes.append(eligibility_sha256)
        bound_candidates.append(
            {
                "id": str(row.id),
                "sequence": row.sequence,
                "sequence_sha256": row.sequence_sha256,
                "generation": row.generation,
                "target_key": target_key,
                "family_key_80_80": family_key,
                "eligibility": eligibility,
                "eligibility_sha256": eligibility_sha256,
            }
        )

    binding = {
        "schema_version": STRUCTURE_V2_PG_BINDING_SCHEMA,
        "source_database": "postgresql",
        "run_id": str(evidence.run.id),
        "target_id": str(evidence.target.id),
        "target_key": target_key,
        "target_sequence_sha256": evidence.target.sequence_sha256,
        "candidate_count": len(bound_candidates),
        "distinct_family_count": len(observed_families),
        "candidate_eligibility_sha256s": binding_hashes,
        "fresh_eligible_family_count": len(fresh_families),
        "legacy_exclusion_snapshot_sha256": sha256_json(
            {
                "sequence_sha256s": sorted(evidence.legacy_sequence_sha256s),
                "family_key_80_80": sorted(evidence.legacy_family_keys),
            }
        ),
    }
    binding["binding_sha256"] = sha256_json(binding)
    bound = copy.deepcopy(dict(request))
    bound["run_id"] = str(evidence.run.id)
    bound["target_key"] = target_key
    bound["candidates"] = bound_candidates
    bound["pg_eligibility_binding"] = binding
    return bound


async def bind_structure_v2_target_request(
    request: Mapping[str, Any],
    *,
    session_factory: Callable[[], Any] = SessionFactory,
) -> dict[str, Any]:
    evidence = await _load_pg_evidence(request, session_factory=session_factory)
    current = bind_structure_v2_request_from_pg_evidence(request, evidence)
    return _preserve_frozen_runtime_binding(request, current)


def _preserve_frozen_runtime_binding(
    request: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep the submitted exclusion snapshot after current PG evidence revalidates.

    The exclusion universe is append-only and can grow from unrelated structure runs
    after submission.  That growth must not rewrite an already frozen workflow input.
    Candidate/family overlap and every candidate qualification are still recomputed by
    ``bind_structure_v2_request_from_pg_evidence`` before reaching this function.
    """

    frozen_binding = request.get("pg_eligibility_binding")
    if not isinstance(frozen_binding, Mapping):
        return copy.deepcopy(dict(current))
    current_binding = current.get("pg_eligibility_binding")
    if not isinstance(current_binding, Mapping):
        raise ValueError("structure v2 current PG eligibility binding is missing")

    frozen_unsigned = copy.deepcopy(dict(frozen_binding))
    frozen_hash = frozen_unsigned.pop("binding_sha256", None)
    if frozen_hash != sha256_json(frozen_unsigned):
        raise ValueError("structure v2 frozen PG eligibility binding digest differs")

    frozen_stable = copy.deepcopy(dict(frozen_binding))
    current_stable = copy.deepcopy(dict(current_binding))
    for value in (frozen_stable, current_stable):
        value.pop("legacy_exclusion_snapshot_sha256", None)
        value.pop("binding_sha256", None)
    if frozen_stable != current_stable:
        raise ValueError("structure v2 current PG eligibility binding differs from frozen input")

    reconciled = copy.deepcopy(dict(current))
    reconciled["pg_eligibility_binding"] = copy.deepcopy(dict(frozen_binding))
    if reconciled != dict(request):
        raise ValueError("structure v2 current PG request differs from frozen workflow input")
    return reconciled


__all__ = [
    "PG_SOURCE_SNAPSHOT_KEY",
    "STRUCTURE_V2_ELIGIBILITY_SCHEMA",
    "STRUCTURE_V2_PG_BINDING_SCHEMA",
    "STRUCTURE_V2_SOURCE_SNAPSHOT_SCHEMA",
    "StructureV2PgEvidence",
    "_preserve_frozen_runtime_binding",
    "bind_structure_v2_request_from_pg_evidence",
    "bind_structure_v2_target_request",
]
