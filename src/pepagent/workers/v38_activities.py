from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Any

from temporalio import activity

from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_json
from pepagent.v38_persistence import (
    GeneratorCellToolBinding,
    persist_score_all_proposal_cohort,
)
from pepagent.v38_science_execution import (
    RawProposal,
    ScoreAllProposalCohort,
    V38SequenceExecutionContract,
    build_score_all_proposal_cohort,
)
from pepagent.workers.activities import _register_artifact, _store_json


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
