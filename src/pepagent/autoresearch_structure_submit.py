from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import or_, select, text
from temporalio.client import Client, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from pepagent.autoresearch_structure_cohort import (
    TARGET_KEYS,
    StructureEscalationCandidate,
    StructureEscalationCohort,
)
from pepagent.db.models import Candidate, ExperimentRun, LifecycleEvent, Target, ToolCall
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.enums import CandidateStatus, RunStatus
from pepagent.domain.schemas import ExperimentSpec, PocketCatalogSpec, TargetSpec
from pepagent.provenance.hashing import sha256_json, sha256_text
from pepagent.settings import get_settings

WORKFLOW_TYPE = "CandidateStructureValidationWorkflow"
CONTROL_QUEUE = "pepagent-control"
PLAN_SCHEMA_VERSION = "ampgent.structure-formal-plan.1"
RUN_SCHEMA_VERSION = "ampgent.structure-formal-run.1"
ACTOR = "autoresearch-structure-formal-submit"
RUN_NAMESPACE = uuid.UUID("9f9d361c-2667-41fb-b15d-5e7506a23962")
WORKFLOW_NAMESPACE = uuid.UUID("f8e50b1f-5e2d-4a70-b8d4-06c45ca5d430")


@dataclass(frozen=True)
class StructureFormalBranch:
    target_key: str
    target_sequence_sha256: str
    accession: str
    qualification: dict[str, Any]
    candidates: tuple[StructureEscalationCandidate, ...]
    experiment_spec: ExperimentSpec
    workflow_spec: dict[str, Any]
    formal_submission_key: str
    run_id: uuid.UUID
    workflow_id: str
    run_spec: dict[str, Any]


@dataclass(frozen=True)
class StructureFormalPlan:
    cohort: StructureEscalationCohort
    cohort_path: Path
    plan_identity: str
    branches: tuple[StructureFormalBranch, ...]

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "cohort_id": self.cohort.cohort_sha256,
            "selected_count": self.cohort.selected_count,
            "plan_identity": self.plan_identity,
            "branches": [
                {
                    "target_key": branch.target_key,
                    "candidate_count": len(branch.candidates),
                    "structure_evidence_mode": branch.qualification[
                        "structure_evidence_mode"
                    ],
                    "pocket_evidence_grade": branch.qualification[
                        "pocket_evidence_grade"
                    ],
                    "rosetta_nstruct": branch.workflow_spec["rosetta_nstruct"],
                    "run_id": str(branch.run_id),
                    "workflow_id": branch.workflow_id,
                    "formal_submission_key": branch.formal_submission_key,
                }
                for branch in self.branches
            ],
        }


@dataclass(frozen=True)
class StructureReservation:
    created: bool
    workflow_requests: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class TemporalBinding:
    handle: WorkflowHandle
    temporal_run_id: str
    recovered: bool


def _load_object(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8-sig")
    payload = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"document root must be an object: {path}")
    return payload


def load_structure_cohort(path: Path) -> StructureEscalationCohort:
    payload = _load_object(path.resolve())
    embedded_identity = payload.pop("cohort_sha256", None)
    embedded_count = payload.pop("selected_count", None)
    cohort = StructureEscalationCohort.model_validate(payload)
    if embedded_identity != cohort.cohort_sha256:
        raise ValueError("structure cohort embedded identity differs from its content")
    if embedded_count != cohort.selected_count:
        raise ValueError("structure cohort embedded selected count differs")
    return cohort


def _target_manifest_items(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = payload.get("targets")
    if payload.get("target_count") != 6 or not isinstance(rows, list):
        raise ValueError("six-target manifest is incomplete")
    items = {str(row.get("target_key")): row for row in rows if isinstance(row, dict)}
    if tuple(key for key in TARGET_KEYS if key in items) != TARGET_KEYS or len(items) != 6:
        raise ValueError("six-target manifest target keys differ")
    for key in TARGET_KEYS:
        row = items[key]
        sequence = "".join(str(row.get("sequence", "")).split()).upper()
        if sha256_text(sequence) != row.get("sequence_sha256"):
            raise ValueError(f"target manifest sequence identity differs for {key}")
        row["sequence"] = sequence
    return items


def _structure_protocol(mode: str) -> str:
    if mode == "admitted_target_conditioned_relative_ranking":
        return "legacy_ensemble_gate"
    if mode == "exploratory_low_confidence_relative_ranking":
        return "diagnostic_fast"
    raise ValueError(f"unsupported structure evidence mode: {mode}")


def _build_experiment_spec(
    *,
    target_key: str,
    manifest_item: dict[str, Any],
    catalog: PocketCatalogSpec,
    qualification: dict[str, Any],
    candidates: tuple[StructureEscalationCandidate, ...],
    seed: int,
) -> tuple[ExperimentSpec, dict[str, Any]]:
    sequence_sha256 = str(manifest_item["sequence_sha256"])
    catalog_targets = [
        item for item in catalog.targets if sha256_text(item.sequence) == sequence_sha256
    ]
    if len(catalog_targets) != 1:
        raise ValueError(f"target {target_key} has no unique exact-sequence catalog target")
    catalog_target = catalog_targets[0]
    pockets = [
        item
        for item in catalog_target.pockets
        if item.key == qualification["pocket_key"]
    ]
    if len(pockets) != 1:
        raise ValueError(f"target {target_key} qualification pocket differs from catalog")
    pocket = pockets[0]
    if pocket.evidence_grade != qualification["pocket_evidence_grade"]:
        raise ValueError(f"target {target_key} pocket evidence grade differs")
    admitted = qualification["structure_evidence_mode"] == (
        "admitted_target_conditioned_relative_ranking"
    )
    if admitted != bool(pocket.conditioning_enabled):
        raise ValueError(f"target {target_key} pocket admission differs")
    pocket_residues = list(pocket.residue_indices) if admitted else []
    lengths = sorted({len(item.sequence) for item in candidates})
    spec = ExperimentSpec(
        target=TargetSpec(
            name=str(manifest_item["display_name"]),
            sequence=str(manifest_item["sequence"]),
            organism=str(manifest_item.get("organism") or ""),
            accession=str(manifest_item["protein_accession"]),
            source_database="NCBI",
            source_uri=str(manifest_item.get("source_uri") or ""),
            source_version=str(manifest_item["protein_accession"]),
            pocket_residues=pocket_residues,
        ),
        peptide_lengths=lengths,
        candidates_per_length=max(1, len(candidates)),
        structure_top_k=1,
        generations=1,
        structure_protocol=_structure_protocol(
            str(qualification["structure_evidence_mode"])
        ),
        final_structure_candidate_count=1,
        seed=seed,
        diffusion_samples=1,
        boltz_seeds_per_candidate=3,
        boltz_seed_values=[],
        boltz_recycling_steps=3,
        boltz_sampling_steps=200,
        boltz_use_potentials=True,
        boltz_no_kernels=True,
        use_msa_server=False,
        boltz_force_pocket=admitted,
        interface_min_seed_consistency=2.0 / 3.0,
        interface_min_pose_cluster_fraction=2.0 / 3.0,
        rosetta_enabled=True,
        rosetta_top_k=1,
        rosetta_nstruct=200,
        rosetta_parallel_decoys=8,
        rosetta_all_boltz_samples=False,
        rosetta_score_function="ref2015",
        bulk_evaluation_concurrency=1,
        bulk_csv_report_threshold=len(candidates),
    )
    workflow_spec = {
        **spec.model_dump(mode="json"),
        "run_mode": "autoresearch_wetlab_gold_structure_escalation",
        "target_key": target_key,
        "target_role": catalog_target.role,
        "target_structure_evidence_mode": qualification["structure_evidence_mode"],
        "target_pocket_key": pocket.key,
        "target_pocket_evidence_grade": pocket.evidence_grade,
        "target_structure_limitations": list(qualification["limitations"]),
        "binding_or_affinity_claim_allowed": False,
        "rosetta_interpretation": (
            "same-protocol relative target-conditioned ranking only"
            if admitted
            else "exploratory low-confidence structure diagnostic only"
        ),
    }
    return spec, workflow_spec


def build_structure_formal_plan(
    *,
    cohort: StructureEscalationCohort,
    cohort_path: Path,
    target_manifest_payload: dict[str, Any],
    pocket_catalog_payload: dict[str, Any],
) -> StructureFormalPlan:
    manifest = _target_manifest_items(target_manifest_payload)
    catalog = PocketCatalogSpec.model_validate(pocket_catalog_payload)
    branches: list[StructureFormalBranch] = []
    for ordinal, target_cohort in enumerate(cohort.target_cohorts, start=1):
        target_key = target_cohort.target_key
        manifest_item = manifest[target_key]
        if (
            target_cohort.qualification.target_sequence_sha256
            != manifest_item["sequence_sha256"]
        ):
            raise ValueError(f"target {target_key} qualification sequence differs")
        candidates = tuple(target_cohort.selected)
        if len(candidates) < 50:
            raise ValueError(f"target {target_key} has fewer than 50 structure candidates")
        qualification = target_cohort.qualification.model_dump(mode="json")
        experiment_spec, workflow_spec = _build_experiment_spec(
            target_key=target_key,
            manifest_item=manifest_item,
            catalog=catalog,
            qualification=qualification,
            candidates=candidates,
            seed=202_608_290 + ordinal,
        )
        identity = {
            "schema_version": PLAN_SCHEMA_VERSION,
            "cohort_id": cohort.cohort_sha256,
            "target_key": target_key,
            "target_sequence_sha256": manifest_item["sequence_sha256"],
            "qualification": qualification,
            "candidate_sequence_sha256s": [item.sequence_sha256 for item in candidates],
            "workflow_spec": workflow_spec,
        }
        formal_submission_key = sha256_json(identity)
        run_id = uuid.uuid5(RUN_NAMESPACE, formal_submission_key)
        workflow_uuid = uuid.uuid5(WORKFLOW_NAMESPACE, formal_submission_key)
        workflow_id = f"pepagent-structure-gold-v1-{target_key}-{workflow_uuid}"
        run_spec = {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": str(run_id),
            "workflow_type": WORKFLOW_TYPE,
            "workflow_id": workflow_id,
            "formal_submission_key": formal_submission_key,
            "cohort_id": cohort.cohort_sha256,
            "target_key": target_key,
            "target_sequence_sha256": manifest_item["sequence_sha256"],
            "qualification": qualification,
            "candidate_count": len(candidates),
            "candidate_sequence_sha256s": [item.sequence_sha256 for item in candidates],
            "workflow_spec": workflow_spec,
            "authoritative_call_record": "postgresql",
            "no_binding_or_affinity_claim": True,
        }
        branches.append(
            StructureFormalBranch(
                target_key=target_key,
                target_sequence_sha256=str(manifest_item["sequence_sha256"]),
                accession=str(manifest_item["protein_accession"]),
                qualification=qualification,
                candidates=candidates,
                experiment_spec=experiment_spec,
                workflow_spec=workflow_spec,
                formal_submission_key=formal_submission_key,
                run_id=run_id,
                workflow_id=workflow_id,
                run_spec=run_spec,
            )
        )
    if tuple(item.target_key for item in branches) != TARGET_KEYS:
        raise ValueError("structure formal plan target order differs")
    plan_identity = sha256_json(
        {
            "schema_version": PLAN_SCHEMA_VERSION,
            "cohort_id": cohort.cohort_sha256,
            "formal_submission_keys": [item.formal_submission_key for item in branches],
        }
    )
    return StructureFormalPlan(
        cohort=cohort,
        cohort_path=cohort_path.resolve(),
        plan_identity=plan_identity,
        branches=tuple(branches),
    )


def load_structure_formal_plan(
    *, cohort_path: Path, target_manifest_path: Path, pocket_catalog_path: Path
) -> StructureFormalPlan:
    return build_structure_formal_plan(
        cohort=load_structure_cohort(cohort_path),
        cohort_path=cohort_path,
        target_manifest_payload=_load_object(target_manifest_path.resolve()),
        pocket_catalog_payload=_load_object(pocket_catalog_path.resolve()),
    )


def _lock_id(plan_identity: str) -> int:
    return int.from_bytes(bytes.fromhex(plan_identity)[:8], "big", signed=True)


async def _resolve_target(session: Any, branch: StructureFormalBranch) -> Target:
    target = await session.scalar(
        select(Target).where(Target.sequence_sha256 == branch.target_sequence_sha256)
    )
    if target is None:
        target = Target(
            name=branch.experiment_spec.target.name,
            organism=branch.experiment_spec.target.organism,
            accession=branch.experiment_spec.target.accession,
            sequence=branch.experiment_spec.target.sequence,
            sequence_sha256=branch.target_sequence_sha256,
            metadata_json={
                "source_database": branch.experiment_spec.target.source_database,
                "source_uri": branch.experiment_spec.target.source_uri,
                "source_version": branch.experiment_spec.target.source_version,
                "pocket_residues": branch.experiment_spec.target.pocket_residues,
            },
        )
        session.add(target)
        await session.flush()
    if target.sequence != branch.experiment_spec.target.sequence:
        raise ValueError(f"target sequence differs for {branch.target_key}")
    return target


def _candidate_metadata(
    branch: StructureFormalBranch, candidate: StructureEscalationCandidate
) -> dict[str, Any]:
    return {
        "run_mode": "autoresearch_wetlab_gold_structure_escalation",
        "target_key": branch.target_key,
        "source_candidate_id": candidate.candidate_id,
        "source_result_sha256": candidate.source_result_sha256,
        "source_sequence_sha256": candidate.sequence_sha256,
        "family_key_80_80": candidate.family_key_80_80,
        "structure_rank": candidate.structure_rank,
        "selection_front": candidate.selection_front,
        "activity_model_support_count": candidate.activity_model_support_count,
        "guruprasad_instability_index": candidate.guruprasad_instability_index,
        "guruprasad_instability_ood": candidate.guruprasad_instability_ood,
        "qualification": branch.qualification,
        "minimum_rosetta_decoys": 200,
        "no_binding_or_affinity_claim": True,
    }


def _workflow_request(
    branch: StructureFormalBranch, candidates: list[Candidate]
) -> dict[str, Any]:
    return {
        "run_id": str(branch.run_id),
        "spec": branch.workflow_spec,
        "candidates": [
            {
                "id": str(candidate.id),
                "sequence": candidate.sequence,
                "sequence_sha256": candidate.sequence_sha256,
                "generation": candidate.generation,
            }
            for candidate in candidates
        ],
    }


async def _existing_candidates(
    session: Any, branch: StructureFormalBranch
) -> list[Candidate]:
    candidates = list(
        await session.scalars(
            select(Candidate)
            .where(Candidate.run_id == branch.run_id)
            .order_by(Candidate.proposal_rank, Candidate.id)
        )
    )
    expected = list(branch.candidates)
    if len(candidates) != len(expected):
        raise ValueError(f"{branch.target_key} reserved candidate count differs")
    for row, source in zip(candidates, expected, strict=True):
        if (
            row.proposal_rank != source.structure_rank
            or row.sequence_sha256 != source.sequence_sha256
            or row.sequence != source.sequence
            or row.metadata_json != _candidate_metadata(branch, source)
        ):
            raise ValueError(f"{branch.target_key} reserved candidate identity differs")
    if len({row.generator_call_id for row in candidates}) != 1 or candidates[
        0
    ].generator_call_id is None:
        raise ValueError(f"{branch.target_key} import ToolCall binding is incomplete")
    return candidates


async def reserve_structure_formal_plan(
    plan: StructureFormalPlan,
    *,
    session_factory: Callable[[], Any] = SessionFactory,
) -> StructureReservation:
    run_ids = [branch.run_id for branch in plan.branches]
    formal_keys = [branch.formal_submission_key for branch in plan.branches]
    workflow_ids = [branch.workflow_id for branch in plan.branches]
    workflow_requests: dict[str, dict[str, Any]] = {}
    async with session_factory() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _lock_id(plan.plan_identity)},
        )
        existing = list(
            await session.scalars(
                select(ExperimentRun).where(
                    or_(
                        ExperimentRun.id.in_(run_ids),
                        ExperimentRun.formal_submission_key.in_(formal_keys),
                        ExperimentRun.temporal_workflow_id.in_(workflow_ids),
                    )
                )
            )
        )
        if existing and len(existing) != len(plan.branches):
            raise ValueError("structure formal reservation is partially present")
        created = not existing
        by_id = {run.id: run for run in existing}
        repository = ExperimentRepository(session)
        for branch in plan.branches:
            target = await _resolve_target(session, branch)
            run = by_id.get(branch.run_id)
            if run is None:
                run = ExperimentRun(
                    id=branch.run_id,
                    target_id=target.id,
                    spec_json=branch.run_spec,
                    spec_sha256=sha256_json(branch.run_spec),
                    formal_submission_key=branch.formal_submission_key,
                    status=RunStatus.CREATED,
                    temporal_workflow_id=branch.workflow_id,
                )
                session.add(run)
                await session.flush()
                await repository.append_event(
                    "run", run.id, "structure.formal_reserved", ACTOR, branch.run_spec
                )
                import_call = await repository.record_completed_tool_call(
                    run.id,
                    "autoresearch-structure-cohort-import",
                    "1.0.0",
                    sha256_json({"actor": ACTOR, "storage": "postgresql"}),
                    {
                        "cohort_id": plan.cohort.cohort_sha256,
                        "target_key": branch.target_key,
                        "candidate_count": len(branch.candidates),
                    },
                    {
                        "preserve_order": True,
                        "preserve_target_branch": True,
                        "weighted_total_forbidden": True,
                    },
                    {
                        "candidate_identities": [
                            {
                                "source_candidate_id": item.candidate_id,
                                "sequence": item.sequence,
                                "sequence_sha256": item.sequence_sha256,
                                "structure_rank": item.structure_rank,
                            }
                            for item in branch.candidates
                        ]
                    },
                    model_uri="deterministic://autoresearch-structure-cohort-import-v1",
                )
                staged: list[Candidate] = []
                for source in branch.candidates:
                    candidate = await repository.add_candidate(
                        run.id,
                        source.sequence,
                        generation=0,
                        proposal_rank=source.structure_rank,
                        generator_call_id=import_call.id,
                        metadata=_candidate_metadata(branch, source),
                        actor=ACTOR,
                    )
                    await repository.transition_candidate(
                        candidate.id,
                        CandidateStatus.STRUCTURE_QUEUED,
                        ACTOR,
                        "selected display-safe family-unique candidate queued for "
                        "target structure evaluation",
                    )
                    staged.append(candidate)
                workflow_requests[branch.target_key] = _workflow_request(branch, staged)
                continue
            if (
                run.target_id != target.id
                or run.spec_json != branch.run_spec
                or run.spec_sha256 != sha256_json(branch.run_spec)
                or run.formal_submission_key != branch.formal_submission_key
                or run.temporal_workflow_id != branch.workflow_id
            ):
                raise ValueError(f"existing {branch.target_key} structure run identity differs")
            if run.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
                raise ValueError(
                    f"existing {branch.target_key} structure run is terminal {run.status}"
                )
            workflow_requests[branch.target_key] = _workflow_request(
                branch, await _existing_candidates(session, branch)
            )
    return StructureReservation(created=created, workflow_requests=workflow_requests)


def _memo(branch: StructureFormalBranch) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "target_key": branch.target_key,
        "run_id": str(branch.run_id),
        "formal_submission_key": branch.formal_submission_key,
        "cohort_id": branch.run_spec["cohort_id"],
        "candidate_count": len(branch.candidates),
        "rosetta_nstruct": branch.workflow_spec["rosetta_nstruct"],
    }


async def _description_memo(description: Any) -> dict[str, Any]:
    memo: Any = getattr(description, "memo", None)
    if callable(memo):
        memo = memo()
    if inspect.isawaitable(memo):
        memo = await memo
    if not isinstance(memo, dict):
        raise ValueError("existing structure workflow memo is unavailable")
    return memo


async def _start_or_recover(
    client: Client,
    *,
    branch: StructureFormalBranch,
    request: dict[str, Any],
) -> TemporalBinding:
    memo = _memo(branch)
    recovered = False
    try:
        handle = await client.start_workflow(
            WORKFLOW_TYPE,
            request,
            id=branch.workflow_id,
            task_queue=CONTROL_QUEUE,
            memo={"ampgent_structure_identity": memo},
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
        )
    except WorkflowAlreadyStartedError as error:
        recovered = True
        handle = client.get_workflow_handle(branch.workflow_id)
        description = await handle.describe()
        if getattr(description, "workflow_type", None) != WORKFLOW_TYPE:
            raise ValueError("existing structure workflow type differs") from error
        existing_memo = await _description_memo(description)
        if existing_memo.get("ampgent_structure_identity") != memo:
            raise ValueError("existing structure workflow memo differs") from error
    description = await handle.describe()
    temporal_run_id = str(getattr(description, "run_id", "") or "")
    if not temporal_run_id:
        raise ValueError("structure workflow Temporal run ID is missing")
    return TemporalBinding(
        handle=handle,
        temporal_run_id=temporal_run_id,
        recovered=recovered,
    )


async def submit_structure_formal_plan(
    plan: StructureFormalPlan,
    reservation: StructureReservation,
    *,
    client: Client,
    session_factory: Callable[[], Any] = SessionFactory,
) -> dict[str, Any]:
    if set(reservation.workflow_requests) != set(TARGET_KEYS):
        raise ValueError("structure submission requires all six reserved branches")
    bindings: dict[str, TemporalBinding] = {}
    for branch in plan.branches:
        bindings[branch.target_key] = await _start_or_recover(
            client,
            branch=branch,
            request=reservation.workflow_requests[branch.target_key],
        )
    async with session_factory() as session, session.begin():
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _lock_id(plan.plan_identity)},
        )
        runs = list(
            await session.scalars(
                select(ExperimentRun)
                .where(ExperimentRun.id.in_([branch.run_id for branch in plan.branches]))
                .with_for_update()
            )
        )
        if len(runs) != len(plan.branches):
            raise ValueError("structure reservation disappeared before submission")
        by_id = {run.id: run for run in runs}
        repository = ExperimentRepository(session)
        for branch in plan.branches:
            run = by_id[branch.run_id]
            binding = bindings[branch.target_key]
            if run.temporal_run_id not in {None, binding.temporal_run_id}:
                raise ValueError(f"{branch.target_key} Temporal run identity differs")
            newly_bound = run.temporal_run_id is None
            run.temporal_run_id = binding.temporal_run_id
            if run.status == RunStatus.CREATED:
                run.status = RunStatus.RUNNING
            elif run.status not in {RunStatus.RUNNING, RunStatus.SUCCEEDED}:
                raise ValueError(f"{branch.target_key} structure run cannot be submitted")
            if newly_bound:
                await repository.append_event(
                    "run",
                    run.id,
                    "structure.formal_workflow_submitted",
                    ACTOR,
                    {
                        "workflow_id": branch.workflow_id,
                        "temporal_run_id": binding.temporal_run_id,
                        "target_key": branch.target_key,
                        "candidate_count": len(branch.candidates),
                    },
                )
    return {
        "submitted": True,
        "branch_count": len(plan.branches),
        "branches": [
            {
                "target_key": branch.target_key,
                "run_id": str(branch.run_id),
                "workflow_id": branch.workflow_id,
                "temporal_run_id": bindings[branch.target_key].temporal_run_id,
                "recovered": bindings[branch.target_key].recovered,
            }
            for branch in plan.branches
        ],
    }


async def execute_structure_formal_plan(
    plan: StructureFormalPlan, *, reserve_only: bool
) -> dict[str, Any]:
    reservation = await reserve_structure_formal_plan(plan)
    result: dict[str, Any] = {
        "executed": True,
        "reserve_only": reserve_only,
        "reservation_created": reservation.created,
        **plan.summary(),
    }
    if reserve_only:
        return result
    settings = get_settings()
    client = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    result["submission"] = await submit_structure_formal_plan(
        plan, reservation, client=client
    )
    return result


async def count_structure_lifecycle_events(
    plan: StructureFormalPlan,
    *,
    session_factory: Callable[[], Any] = SessionFactory,
) -> dict[str, int]:
    """Read back the DB evidence counts used for operator-facing acceptance."""

    run_ids = [branch.run_id for branch in plan.branches]
    async with session_factory() as session:
        run_count = len(
            list(
                await session.scalars(
                    select(ExperimentRun.id).where(ExperimentRun.id.in_(run_ids))
                )
            )
        )
        call_count = len(
            list(
                await session.scalars(
                    select(ToolCall.id).where(ToolCall.run_id.in_(run_ids))
                )
            )
        )
        event_count = len(
            list(
                await session.scalars(
                    select(LifecycleEvent.id).where(
                        LifecycleEvent.aggregate_type == "run",
                        LifecycleEvent.aggregate_id.in_(run_ids),
                    )
                )
            )
        )
    return {"runs": run_count, "tool_calls": call_count, "run_events": event_count}


__all__ = [
    "StructureFormalBranch",
    "StructureFormalPlan",
    "StructureReservation",
    "build_structure_formal_plan",
    "count_structure_lifecycle_events",
    "execute_structure_formal_plan",
    "load_structure_cohort",
    "load_structure_formal_plan",
    "reserve_structure_formal_plan",
    "submit_structure_formal_plan",
]
