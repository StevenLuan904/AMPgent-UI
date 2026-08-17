from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import activity

from pepagent.db.models import AgentDecision, Candidate, Evaluation
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_bytes, sha256_json, sha256_text
from pepagent.storage.object_store import ContentAddressedObjectStore
from pepagent.v38_persistence import (
    GeneratorCellToolBinding,
    persist_score_all_proposal_cohort,
)
from pepagent.v38_science_execution import (
    V38_METRIC_OBSERVATIONS,
    RawProposal,
    RefinementChildProposal,
    ScoreAllProposalCohort,
    V38SequenceExecutionContract,
    build_score_all_proposal_cohort,
)
from pepagent.v38_sequence_first_multitarget import (
    KnowledgeUseTrace,
    MetricObservation,
    SequenceCandidateEvidence,
    SequenceRefinementPlan,
    admit_sequence_cohort,
    build_default_v38_maturity_policy,
    build_sequence_refinement_plan,
    compute_leave_one_objective_out_rank_stability,
)
from pepagent.workers.activities import _register_artifact, _store_json
from pepagent.workers.v37_activities import _select_v37_declared_observations

V38_METRIC_RESULT_REFERENCE_SCHEMA = "v38.metric-result-reference.1"
V38_ADMISSION_REFERENCE_SCHEMA = "v38.sequence-admission-reference.1"


def validate_v38_refinement_result(
    plan: SequenceRefinementPlan,
    result: dict[str, Any],
) -> tuple[RefinementChildProposal, ...]:
    raw = result.get("proposals")
    if not isinstance(raw, list):
        raise ValueError("v38 refinement result lacks proposals")
    proposals = tuple(RefinementChildProposal.model_validate(item) for item in raw)
    tasks = {task.parent_candidate_id: task for task in plan.tasks}
    expected = {parent_id: task.requested_children for parent_id, task in tasks.items()}
    observed = {parent_id: 0 for parent_id in expected}
    for proposal in proposals:
        if proposal.refinement_round != plan.refinement_round:
            raise ValueError("v38 refinement proposal round differs from plan")
        if proposal.parent_candidate_id not in observed:
            raise ValueError("v38 refinement proposal parent is not planned")
        task = tasks[proposal.parent_candidate_id]
        if "".join(proposal.parent_sequence.split()).upper() != task.parent_sequence:
            raise ValueError("v38 refinement proposal parent sequence drifted")
        if any(
            trace.provider_task_id != task.provider_task_id
            for trace in proposal.knowledge_traces
        ):
            raise ValueError("v38 refinement proposal cites another knowledge provider task")
        observed[proposal.parent_candidate_id] += 1
    if observed != expected:
        raise ValueError("v38 refinement result does not exactly cover planned children")
    return proposals


def build_v38_score_all_cohort_from_results(
    contract: V38SequenceExecutionContract,
    generated_cells: list[dict[str, Any]],
) -> ScoreAllProposalCohort:
    by_identity: dict[tuple[str, int], dict[str, Any]] = {}
    for generated in generated_cells:
        result = generated.get("result")
        if not isinstance(result, dict):
            raise ValueError("v38 generated cell lacks a result object")
        identity = (str(result.get("generator_id")), int(result.get("seed", -1)))
        if identity in by_identity:
            raise ValueError("v38 generated cell identity is duplicated")
        by_identity[identity] = generated
    expected = {(cell.generator_id, cell.seed) for cell in contract.cells}
    if set(by_identity) != expected:
        raise ValueError("v38 generated cells do not exactly cover the frozen contract")
    proposals: list[RawProposal] = []
    for cell in contract.cells:
        result = by_identity[(cell.generator_id, cell.seed)]["result"]
        records = result.get("records")
        if (
            int(result.get("raw_proposal_budget", -1)) != cell.requested_proposals
            or not isinstance(records, list)
            or len(records) != cell.requested_proposals
        ):
            raise ValueError("v38 generated cell count differs from the frozen contract")
        for expected_rank, record in enumerate(records, start=1):
            if not isinstance(record, dict) or int(record.get("raw_rank", -1)) != expected_rank:
                raise ValueError("v38 generated cell raw ranks are not contiguous")
            proposals.append(
                RawProposal(
                    generator_id=cell.generator_id,
                    seed=cell.seed,
                    raw_rank=expected_rank,
                    sequence=str(record.get("sequence", "")),
                )
            )
    return build_score_all_proposal_cohort(contract, proposals)


async def _resolve_v38_metric_result(reference: dict[str, Any]) -> dict[str, Any]:
    if reference.get("schema_version") != V38_METRIC_RESULT_REFERENCE_SCHEMA:
        raise ValueError("v38 metric result reference schema is invalid")
    artifact = reference.get("metric_result_artifact")
    if not isinstance(artifact, dict) or set(artifact) != {
        "sha256",
        "size_bytes",
        "uri",
        "media_type",
    }:
        raise ValueError("v38 metric result artifact reference is invalid")
    if artifact["media_type"] != "application/json":
        raise ValueError("v38 metric result artifact media type is invalid")
    raw = await asyncio.to_thread(
        ContentAddressedObjectStore().get_bytes, str(artifact["uri"])
    )
    if len(raw) != int(artifact["size_bytes"]) or sha256_bytes(raw) != artifact["sha256"]:
        raise ValueError("v38 metric result artifact identity is invalid")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("v38 metric result artifact is not canonical JSON") from error
    if not isinstance(payload, dict) or sha256_json(payload) != reference.get(
        "metric_result_sha256"
    ):
        raise ValueError("v38 metric result payload identity is invalid")
    if artifact["sha256"] != reference["metric_result_sha256"]:
        raise ValueError("v38 metric result content hashes disagree")
    if (
        payload.get("result", {}).get("plugin", {}).get("name")
        != reference.get("plugin_name")
        or payload.get("activity_transition_receipt")
        != reference.get("activity_transition_receipt")
    ):
        raise ValueError("v38 metric compact receipt differs from payload")
    return payload


async def _resolve_v38_admission(reference: dict[str, Any]) -> dict[str, Any]:
    if reference.get("schema_version") != V38_ADMISSION_REFERENCE_SCHEMA:
        raise ValueError("v38 sequence admission reference schema is invalid")
    artifact = reference.get("admission_artifact")
    if not isinstance(artifact, dict) or set(artifact) != {
        "sha256",
        "size_bytes",
        "uri",
        "media_type",
    }:
        raise ValueError("v38 sequence admission artifact reference is invalid")
    if artifact["media_type"] != "application/json":
        raise ValueError("v38 sequence admission artifact media type is invalid")
    raw = await asyncio.to_thread(
        ContentAddressedObjectStore().get_bytes, str(artifact["uri"])
    )
    if len(raw) != int(artifact["size_bytes"]) or sha256_bytes(raw) != artifact["sha256"]:
        raise ValueError("v38 sequence admission artifact identity is invalid")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or sha256_json(payload) != reference.get(
        "admission_sha256"
    ):
        raise ValueError("v38 sequence admission payload identity is invalid")
    if artifact["sha256"] != reference["admission_sha256"]:
        raise ValueError("v38 sequence admission hashes disagree")
    return payload


async def _build_v38_sequence_admission_payload(
    *,
    session: AsyncSession,
    run_id: uuid.UUID,
    refinement_round: int,
    knowledge_context_pack_sha256: str,
) -> tuple[dict[str, Any], set[uuid.UUID]]:
    policy = build_default_v38_maturity_policy()
    candidates = list(
        await session.scalars(
            select(Candidate).where(Candidate.run_id == run_id).order_by(Candidate.id)
        )
    )
    if not candidates:
        raise ValueError("v38 sequence admission requires persisted candidates")
    evaluations = list(
        await session.scalars(
            select(Evaluation)
            .where(Evaluation.candidate_id.in_([item.id for item in candidates]))
            .order_by(Evaluation.candidate_id, Evaluation.metric_name, Evaluation.id)
        )
    )
    by_candidate: dict[uuid.UUID, list[Evaluation]] = {
        item.id: [] for item in candidates
    }
    evidence_call_ids: set[uuid.UUID] = set()
    for evaluation in evaluations:
        if evaluation.metric_name in policy.required_metrics:
            by_candidate[evaluation.candidate_id].append(evaluation)
            evidence_call_ids.add(evaluation.tool_call_id)
    provisional: list[SequenceCandidateEvidence] = []
    parent_sequences: dict[uuid.UUID, str] = {}
    for candidate in candidates:
        rows = by_candidate[candidate.id]
        names = [item.metric_name for item in rows]
        if len(rows) != len(policy.required_metrics) or set(names) != policy.required_metrics:
            raise ValueError(f"v38 candidate metric coverage is incomplete: {candidate.id}")
        if len(names) != len(set(names)):
            raise ValueError(f"v38 candidate metric evidence is duplicated: {candidate.id}")
        traces = tuple(
            KnowledgeUseTrace.model_validate(item)
            for item in candidate.metadata_json.get("knowledge_traces", [])
        )
        context_sha = candidate.metadata_json.get("cohort_sha256")
        if not isinstance(context_sha, str) or len(context_sha) != 64:
            context_sha = sha256_json(candidate.metadata_json)
        provisional.append(
            SequenceCandidateEvidence(
                candidate_id=candidate.id,
                sequence_sha256=candidate.sequence_sha256,
                parent_candidate_id=candidate.parent_id,
                generation=candidate.generation,
                observations=tuple(
                    MetricObservation(
                        metric_name=row.metric_name,
                        status=("succeeded" if row.status == "succeeded" else "failed"),
                        numeric_value=row.numeric_value,
                        text_value=row.text_value,
                        out_of_domain=row.out_of_domain,
                    )
                    for row in rows
                ),
                rank_stability=1.0,
                knowledge_traces=traces,
                proposal_context_sha256=context_sha,
            )
        )
        parent_sequences[candidate.id] = candidate.sequence
        if candidate.generator_call_id is not None:
            evidence_call_ids.add(candidate.generator_call_id)
    provisional_tuple = tuple(provisional)
    stability = compute_leave_one_objective_out_rank_stability(
        provisional_tuple, policy
    )
    evidence = tuple(
        item.model_copy(update={"rank_stability": stability[item.candidate_id]})
        for item in provisional_tuple
    )
    admission = admit_sequence_cohort(
        evidence,
        policy,
        refinement_round=refinement_round,
    )
    refinement = (
        build_sequence_refinement_plan(
            admission=admission,
            candidates=evidence,
            parent_sequences=parent_sequences,
            policy=policy,
            knowledge_context_pack_sha256=knowledge_context_pack_sha256,
        )
        if admission.refinement_required
        else None
    )
    payload = {
        "schema_version": "v38.sequence-admission-evidence.1",
        "run_id": str(run_id),
        "policy": policy.model_dump(mode="json"),
        "candidate_evidence_sha256": sha256_json(
            [item.model_dump(mode="json") for item in evidence]
        ),
        "admission": admission.model_dump(mode="json"),
        "refinement_plan": (
            refinement.model_dump(mode="json") if refinement is not None else None
        ),
    }
    return payload, evidence_call_ids


def build_v38_metric_evaluation_rows(
    *,
    contract: V38SequenceExecutionContract,
    candidates: list[dict[str, Any]],
    metric_result: dict[str, Any],
) -> list[dict[str, Any]]:
    result = metric_result["result"]
    plugin = result["plugin"]
    plugin_name = str(plugin["name"])
    if plugin_name not in contract.metric_plugins:
        raise ValueError("v38 metric plugin is outside the execution contract")
    if result.get("status") != "complete":
        raise ValueError("v38 required metric plugin did not complete")
    expected_metrics = set(V38_METRIC_OBSERVATIONS[plugin_name])
    candidate_by_id = {str(item["id"]): item for item in candidates}
    if len(candidate_by_id) != len(candidates):
        raise ValueError("v38 metric candidate identities are duplicated")
    limitations = [
        f"handoff reliability: {result['contract']['reliability']}",
        f"configured trust: {result['contract']['default_trust']}",
        *result.get("limitations", []),
    ]
    rows: list[dict[str, Any]] = []
    for record in result["records"]:
        candidate = candidate_by_id.get(str(record["candidate_id"]))
        if candidate is None or candidate["sequence"] != record["sequence"]:
            raise ValueError("v38 metric candidate identity or sequence mismatch")
        if record.get("status") not in {"complete", "ok", "success"}:
            raise ValueError("v38 required metric contains a failed candidate record")
        for observation in _select_v37_declared_observations(
            record["observations"], expected_metrics
        ):
            rows.append(
                {
                    "candidate_id": str(record["candidate_id"]),
                    "metric_name": observation["metric_name"],
                    "numeric_value": observation["numeric_value"],
                    "text_value": observation["text_value"],
                    "unit": observation["unit"],
                    "out_of_domain": False,
                    "limitations": limitations,
                    "raw": {
                        "plugin": plugin,
                        "contract": result["contract"],
                        "adapter_version": result.get("adapter_version"),
                        "raw_row": record["raw"],
                    },
                }
            )
    expected_pairs = {
        (candidate_id, metric_name)
        for candidate_id in candidate_by_id
        for metric_name in expected_metrics
    }
    if {(row["candidate_id"], row["metric_name"]) for row in rows} != expected_pairs:
        raise ValueError("v38 metric plugin candidate coverage is incomplete")
    rows.sort(key=lambda item: (item["candidate_id"], item["metric_name"]))
    return rows


@activity.defn(name="persist_v38_score_all_generation")
async def persist_v38_score_all_generation(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(str(request["run_id"]))
    contract = V38SequenceExecutionContract.model_validate(request["execution_contract"])
    generated_cells = request["generated_cells"]
    if not isinstance(generated_cells, list):
        raise ValueError("v38 generated_cells must be a list")
    cohort = build_v38_score_all_cohort_from_results(contract, generated_cells)
    generated_by_identity = {
        (item["result"]["generator_id"], int(item["result"]["seed"])): item
        for item in generated_cells
    }
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        bindings: list[GeneratorCellToolBinding] = []
        for cell in contract.cells:
            generated = generated_by_identity[(cell.generator_id, cell.seed)]
            result = generated["result"]
            transition = generated.get("activity_transition_receipt")
            if not isinstance(transition, dict) or transition.get("schema_version") != (
                "v38.activity-transition-receipt.1"
            ):
                raise ValueError("v38 generator result lacks its transition receipt")
            weights = generated.get("weights_sha256")
            weights_sha256 = weights if isinstance(weights, str) else sha256_json(weights)
            call = await repository.record_completed_tool_call(
                run_id,
                f"v38-generate-{cell.generator_id}",
                str(result["adapter_version"]),
                str(generated["environment_sha256"]),
                {
                    "stage": "v38_score_all_generation",
                    "cell_ordinal": cell.ordinal,
                    "generator_id": cell.generator_id,
                    "seed": cell.seed,
                    "execution_contract_sha256": contract.sha256(),
                },
                {
                    "raw_proposal_budget": cell.requested_proposals,
                    "score_all_valid_unique_proposals": True,
                    "first_k_retention_forbidden": True,
                },
                result,
                weights_sha256=weights_sha256,
                random_seed=cell.seed,
                attempt=int(generated["attempt"]),
            )
            artifact = await _store_json(
                {
                    "result": result,
                    "runtime_identity": generated["runtime_identity"],
                    "stdout_tail": generated["stdout_tail"],
                    "live_launch_receipt": generated["launch_receipt"],
                    "materialization_receipt": generated.get("materialization_receipt"),
                    "activity_transition_receipt": transition,
                }
            )
            await _register_artifact(
                session,
                call.id,
                asdict(artifact),
                "v38_raw_generator_output",
                {
                    "cell_ordinal": cell.ordinal,
                    "generator_id": cell.generator_id,
                    "seed": cell.seed,
                },
            )
            bindings.append(
                GeneratorCellToolBinding(
                    cell_ordinal=cell.ordinal,
                    generator_id=cell.generator_id,
                    seed=cell.seed,
                    tool_call_id=call.id,
                    opaque_arm_label=f"v38-generator-cell-{cell.ordinal}",
                )
            )
        receipt = await persist_score_all_proposal_cohort(
            session,
            run_id=run_id,
            contract=contract,
            cohort=cohort,
            bindings=tuple(bindings),
        )
        cohort_artifact = await _store_json(cohort.model_dump(mode="json"))
        for binding in bindings:
            await _register_artifact(
                session,
                binding.tool_call_id,
                asdict(cohort_artifact),
                "v38_score_all_cohort",
                {
                    "cohort_sha256": cohort.sha256(),
                    "execution_contract_sha256": contract.sha256(),
                },
            )
        persisted_candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == run_id)
                .order_by(Candidate.proposal_rank, Candidate.id)
            )
        )
        if len(persisted_candidates) != cohort.promoted_unique_count:
            raise ValueError("v38 score-all candidate result count drifted")
    return {
        "persistence_receipt": receipt.model_dump(mode="json"),
        "score_all_cohort": cohort.model_dump(mode="json"),
        "candidate_count": cohort.promoted_unique_count,
        "candidates": [
            {
                "id": str(candidate.id),
                "sequence": candidate.sequence,
                "sequence_sha256": candidate.sequence_sha256,
                "proposal_rank": candidate.proposal_rank,
            }
            for candidate in persisted_candidates
        ],
    }


@activity.defn(name="persist_v38_sequence_metric")
async def persist_v38_sequence_metric(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(str(request["run_id"]))
    contract = V38SequenceExecutionContract.model_validate(request["execution_contract"])
    candidates = request["candidates"]
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("v38 metric persistence requires a non-empty candidate cohort")
    reference = request["metric_result"]
    metric_result = await _resolve_v38_metric_result(reference)
    rows = build_v38_metric_evaluation_rows(
        contract=contract,
        candidates=candidates,
        metric_result=metric_result,
    )
    result = metric_result["result"]
    provenance = metric_result["provenance"]
    plugin = result["plugin"]
    plugin_name = str(plugin["name"])
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        run_candidates = {
            str(item.id): item
            for item in await session.scalars(
                select(Candidate).where(Candidate.run_id == run_id)
            )
        }
        expected_candidate_ids = {str(item["id"]) for item in candidates}
        if not expected_candidate_ids <= set(run_candidates):
            raise ValueError("v38 metric persistence candidate cohort differs from database")
        db_candidates = {
            candidate_id: run_candidates[candidate_id]
            for candidate_id in expected_candidate_ids
        }
        call = await repository.record_completed_tool_call(
            run_id,
            f"v38-metric-{plugin_name}",
            str(provenance["tool_version"]),
            str(provenance["environment_sha256"]),
            {
                "stage": "v38_sequence_metrics",
                "plugin": plugin_name,
                "candidate_ids": sorted(expected_candidate_ids),
                "execution_contract_sha256": contract.sha256(),
            },
            {
                "plugin": plugin,
                "declared_observations": sorted(V38_METRIC_OBSERVATIONS[plugin_name]),
                "score_all_candidate_count": len(candidates),
            },
            reference,
            weights_sha256=provenance.get("weights_sha256"),
            model_uri=provenance.get("model_uri"),
            attempt=int(provenance["attempt"]),
        )
        await _register_artifact(
            session,
            call.id,
            reference["metric_result_artifact"],
            "v38_metric_result",
            {
                "plugin": plugin_name,
                "metric_result_sha256": reference["metric_result_sha256"],
            },
        )
        for role, artifact_key in (
            ("v38_metric_raw_output", "raw_output_artifact"),
            ("v38_metric_environment", "environment_artifact"),
        ):
            stored = provenance.get(artifact_key)
            if not isinstance(stored, dict):
                raise ValueError(f"v38 metric provenance lacks {artifact_key}")
            await _register_artifact(
                session,
                call.id,
                stored,
                role,
                {"plugin": plugin_name},
            )
        for row in rows:
            candidate = db_candidates[row["candidate_id"]]
            await repository.record_evaluation(
                candidate.id,
                call.id,
                row["metric_name"],
                row["numeric_value"],
                row["unit"],
                row["raw"],
                text_value=row["text_value"],
                out_of_domain=row["out_of_domain"],
                limitations=row["limitations"],
            )
        generator_call_ids = {
            candidate.generator_call_id for candidate in db_candidates.values()
        }
        for parent_id in sorted(generator_call_ids, key=str):
            if parent_id is not None:
                await repository.record_tool_dependency(
                    call.id,
                    parent_id,
                    "evaluates_v38_score_all_candidate",
                )
        await repository.append_event(
            "run",
            run_id,
            "v38.sequence_metric.persisted",
            "v38-sequence-metrics",
            {
                "plugin": plugin_name,
                "tool_call_id": str(call.id),
                "evaluation_count": len(rows),
                "candidate_count": len(candidates),
                "metric_result_sha256": reference["metric_result_sha256"],
            },
        )
    return {
        "plugin": plugin_name,
        "evaluation_count": len(rows),
        "tool_call_id": str(call.id),
    }


@activity.defn(name="persist_v38_refinement_children")
async def persist_v38_refinement_children(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(str(request["run_id"]))
    plan = SequenceRefinementPlan.model_validate(request["refinement_plan"])
    result = request["refinement_result"]
    if not isinstance(result, dict):
        raise ValueError("v38 refinement result is invalid")
    proposals = validate_v38_refinement_result(plan, result)
    provenance = result.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("v38 refinement result lacks provenance")
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        parents = {
            item.id: item
            for item in await session.scalars(
                select(Candidate).where(
                    Candidate.run_id == run_id,
                    Candidate.id.in_([task.parent_candidate_id for task in plan.tasks]),
                )
            )
        }
        if set(parents) != {task.parent_candidate_id for task in plan.tasks}:
            raise ValueError("v38 refinement parent cohort differs from database")
        for task in plan.tasks:
            parent = parents[task.parent_candidate_id]
            if (
                parent.sequence != task.parent_sequence
                or parent.sequence_sha256 != task.parent_sequence_sha256
            ):
                raise ValueError("v38 refinement parent sequence identity drifted")
        call = await repository.record_completed_tool_call(
            run_id,
            "v38-knowledge-traced-refinement",
            str(provenance["tool_version"]),
            str(provenance["environment_sha256"]),
            {
                "stage": "v38_sequence_refinement",
                "refinement_plan_sha256": plan.sha256(),
                "knowledge_context_pack_sha256": plan.tasks[0].knowledge_context_pack_sha256,
            },
            {
                "refinement_round": plan.refinement_round,
                "parent_controls_retained": True,
                "full_rescoring_required": True,
                "structure_dispatch_forbidden_until_readmission": True,
            },
            result,
            weights_sha256=provenance.get("weights_sha256"),
            model_uri=provenance.get("model_uri"),
            attempt=int(provenance["attempt"]),
        )
        artifact = await _store_json(
            {
                "refinement_plan": plan.model_dump(mode="json"),
                "refinement_result": result,
            }
        )
        await _register_artifact(
            session,
            call.id,
            asdict(artifact),
            "v38_refinement_children_and_parent_controls",
            {
                "refinement_plan_sha256": plan.sha256(),
                "refinement_round": plan.refinement_round,
            },
        )
        for parent in parents.values():
            if parent.generator_call_id is not None:
                await repository.record_tool_dependency(
                    call.id,
                    parent.generator_call_id,
                    "refines_v38_parent_candidate",
                )
        maximum_rank = int(
            await session.scalar(
                select(func.coalesce(func.max(Candidate.proposal_rank), 0)).where(
                    Candidate.run_id == run_id
                )
            )
            or 0
        )
        existing_by_sha = {
            item.sequence_sha256: item
            for item in await session.scalars(
                select(Candidate).where(Candidate.run_id == run_id)
            )
        }
        children: list[Candidate] = []
        duplicate_count = 0
        for ordinal, proposal in enumerate(proposals, start=1):
            sequence = "".join(proposal.child_sequence.split()).upper()
            sequence_sha256 = sha256_text(sequence)
            candidate = existing_by_sha.get(sequence_sha256)
            disposition = "duplicate" if candidate is not None else "promoted_for_scoring"
            if candidate is None:
                candidate = await repository.add_candidate(
                    run_id=run_id,
                    sequence=sequence,
                    generation=plan.refinement_round,
                    proposal_rank=maximum_rank + ordinal,
                    generator_call_id=call.id,
                    parent_id=proposal.parent_candidate_id,
                    metadata={
                        "schema_version": "v38.refinement-child.1",
                        "refinement_plan_sha256": plan.sha256(),
                        "mutation_rationale": proposal.mutation_rationale,
                        "knowledge_traces": [
                            trace.model_dump(mode="json")
                            for trace in proposal.knowledge_traces
                        ],
                        "unchanged_parent_control_sha256": (
                            proposal.unchanged_parent_control_sha256
                        ),
                        "score_all_sequence_metrics_required": True,
                    },
                    actor="v38-knowledge-traced-refinement",
                )
                existing_by_sha[sequence_sha256] = candidate
                children.append(candidate)
            else:
                duplicate_count += 1
            await repository.record_candidate_occurrence(
                run_id=run_id,
                tool_call_id=call.id,
                parent_candidate_id=proposal.parent_candidate_id,
                occurrence_rank=ordinal,
                occurrence_kind="refinement",
                opaque_arm_label=f"v38-refinement-round-{plan.refinement_round}",
                sequence=sequence,
                candidate_id=candidate.id,
                metadata={
                    "schema_version": "v38.refinement-occurrence.1",
                    "refinement_plan_sha256": plan.sha256(),
                    "disposition": disposition,
                    "mutation_rationale": proposal.mutation_rationale,
                    "knowledge_traces": [
                        trace.model_dump(mode="json") for trace in proposal.knowledge_traces
                    ],
                    "unchanged_parent_control_sha256": (
                        proposal.unchanged_parent_control_sha256
                    ),
                },
            )
        await repository.append_event(
            "run",
            run_id,
            "v38.sequence_refinement.persisted",
            "v38-knowledge-traced-refinement",
            {
                "tool_call_id": str(call.id),
                "refinement_round": plan.refinement_round,
                "raw_child_occurrence_count": len(proposals),
                "promoted_unique_child_count": len(children),
                "duplicate_child_count": duplicate_count,
                "full_rescoring_required": True,
            },
        )
    return {
        "tool_call_id": str(call.id),
        "raw_child_occurrence_count": len(proposals),
        "promoted_unique_child_count": len(children),
        "duplicate_child_count": duplicate_count,
        "candidates": [
            {
                "id": str(candidate.id),
                "sequence": candidate.sequence,
                "sequence_sha256": candidate.sequence_sha256,
                "proposal_rank": candidate.proposal_rank,
                "parent_candidate_id": str(candidate.parent_id),
            }
            for candidate in children
        ],
        "full_rescoring_required": True,
    }


@activity.defn(name="evaluate_v38_sequence_admission")
async def evaluate_v38_sequence_admission(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(str(request["run_id"]))
    refinement_round = int(request.get("refinement_round", 0))
    context_sha = str(request["knowledge_context_pack_sha256"])
    if len(context_sha) != 64:
        raise ValueError("v38 knowledge context-pack SHA is invalid")
    async with SessionFactory() as session:
        payload, _ = await _build_v38_sequence_admission_payload(
            session=session,
            run_id=run_id,
            refinement_round=refinement_round,
            knowledge_context_pack_sha256=context_sha,
        )
    artifact = await _store_json(payload)
    admission = payload["admission"]
    return {
        "schema_version": V38_ADMISSION_REFERENCE_SCHEMA,
        "admission_sha256": sha256_json(payload),
        "admission_artifact": asdict(artifact),
        "refinement_round": refinement_round,
        "mature_core_count": len(admission["mature_core_candidate_ids"]),
        "exploration_count": len(admission["exploration_candidate_ids"]),
        "rejected_count": len(admission["rejected_candidate_ids"]),
        "refinement_required": admission["refinement_required"],
        "structure_dispatch_allowed": admission["structure_dispatch_allowed"],
        "refinement_plan": payload["refinement_plan"],
    }


@activity.defn(name="persist_v38_sequence_admission")
async def persist_v38_sequence_admission(request: dict[str, Any]) -> dict[str, Any]:
    run_id = uuid.UUID(str(request["run_id"]))
    reference = request["admission_reference"]
    payload = await _resolve_v38_admission(reference)
    if payload.get("run_id") != str(run_id):
        raise ValueError("v38 sequence admission reference belongs to another run")
    refinement_round = int(reference["refinement_round"])
    context_sha = str(request["knowledge_context_pack_sha256"])
    environment_sha256 = str(request["environment_sha256"])
    worker_source_revision = str(request["worker_source_revision"])
    if len(context_sha) != 64 or len(environment_sha256) != 64:
        raise ValueError("v38 sequence admission persistence identity is invalid")
    async with SessionFactory() as verify_session:
        recomputed, _ = await _build_v38_sequence_admission_payload(
            session=verify_session,
            run_id=run_id,
            refinement_round=refinement_round,
            knowledge_context_pack_sha256=context_sha,
        )
    if sha256_json(recomputed) != reference["admission_sha256"] or recomputed != payload:
        raise ValueError("v38 sequence admission differs from authoritative database evidence")

    async with SessionFactory() as session, session.begin():
        _, evidence_call_ids = await _build_v38_sequence_admission_payload(
            session=session,
            run_id=run_id,
            refinement_round=refinement_round,
            knowledge_context_pack_sha256=context_sha,
        )
        repository = ExperimentRepository(session)
        call = await repository.record_completed_tool_call(
            run_id,
            "v38-sequence-maturity-admission",
            worker_source_revision,
            environment_sha256,
            {
                "stage": "v38_sequence_admission",
                "candidate_evidence_sha256": payload["candidate_evidence_sha256"],
                "knowledge_context_pack_sha256": context_sha,
                "refinement_round": refinement_round,
            },
            {
                "nonweighted_pareto": True,
                "absolute_mic_threshold_forbidden": True,
                "fixed_mic_agreement_cutoff_forbidden": True,
                "full_sequence_panel_required": True,
            },
            reference,
            model_uri="deterministic://v38-sequence-maturity-admission",
            attempt=activity.info().attempt,
        )
        for parent_id in sorted(evidence_call_ids, key=str):
            await repository.record_tool_dependency(
                call.id,
                parent_id,
                "v38_admission_uses_sequence_evidence",
            )
        artifact_row = await _register_artifact(
            session,
            call.id,
            reference["admission_artifact"],
            "v38_sequence_admission_evidence",
            {
                "admission_sha256": reference["admission_sha256"],
                "refinement_round": refinement_round,
            },
        )
        existing_decisions = list(
            await session.scalars(
                select(AgentDecision).where(
                    AgentDecision.run_id == run_id,
                    AgentDecision.generation == refinement_round,
                    AgentDecision.decision_type == "v38_sequence_maturity_admission",
                )
            )
        )
        if len(existing_decisions) > 1:
            raise ValueError("duplicate v38 sequence admission decisions detected")
        response = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if existing_decisions:
            decision = existing_decisions[0]
            if decision.structured_json != payload or decision.response_text != response:
                raise ValueError("existing v38 sequence admission decision drifted")
        else:
            decision = await repository.record_agent_decision(
                run_id,
                refinement_round,
                "v38_sequence_maturity_admission",
                "deterministic-v38-sequence-first-agent",
                worker_source_revision,
                (
                    "Apply the frozen validity and safety gates, independent MIC/activity and "
                    "developability Pareto axes, rank-stability check, and bounded knowledge "
                    "refinement policy to every persisted valid unique sequence."
                ),
                response,
                payload,
                model_name="deterministic://v38-sequence-first-agent",
                response_artifact_id=artifact_row.id,
            )
        for parent_id in sorted(evidence_call_ids, key=str):
            await repository.record_agent_tool_edge(
                decision.id,
                parent_id,
                "input",
                "observes_complete_sequence_evidence",
            )
        await repository.record_agent_tool_edge(
            decision.id,
            call.id,
            "output",
            "materializes_sequence_admission",
        )
    return {
        "tool_call_id": str(call.id),
        "decision_id": str(decision.id),
        "admission_sha256": reference["admission_sha256"],
        "mature_core_count": reference["mature_core_count"],
        "exploration_count": reference["exploration_count"],
        "rejected_count": reference["rejected_count"],
        "refinement_required": reference["refinement_required"],
        "structure_dispatch_allowed": reference["structure_dispatch_allowed"],
    }
