from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from temporalio import activity

from pepagent.db.models import Candidate
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.storage.object_store import ContentAddressedObjectStore
from pepagent.v38_persistence import (
    GeneratorCellToolBinding,
    persist_score_all_proposal_cohort,
)
from pepagent.v38_science_execution import (
    V38_METRIC_OBSERVATIONS,
    RawProposal,
    ScoreAllProposalCohort,
    V38SequenceExecutionContract,
    build_score_all_proposal_cohort,
)
from pepagent.workers.activities import _register_artifact, _store_json
from pepagent.workers.v37_activities import _select_v37_declared_observations

V38_METRIC_RESULT_REFERENCE_SCHEMA = "v38.metric-result-reference.1"


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
    return {
        "persistence_receipt": receipt.model_dump(mode="json"),
        "score_all_cohort": cohort.model_dump(mode="json"),
        "candidate_count": cohort.promoted_unique_count,
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
        db_candidates = {
            str(item.id): item
            for item in await session.scalars(
                select(Candidate).where(Candidate.run_id == run_id)
            )
        }
        expected_candidate_ids = {str(item["id"]) for item in candidates}
        if set(db_candidates) != expected_candidate_ids:
            raise ValueError("v38 metric persistence candidate cohort differs from database")
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
