from __future__ import annotations

import copy
import uuid
from typing import Any
from uuid import UUID

from pepagent.provenance.hashing import sha256_json
from pepagent.seven_branch_design import (
    SevenBranchDesignContract,
    SevenBranchDesignSchedule,
    SevenBranchRoundRequest,
    TargetSequenceRuntime,
    build_seven_branch_round_execution_contract,
)

SEVEN_BRANCH_ID_NAMESPACE = UUID("11897b4e-2a41-44dd-b741-a978e77d48ed")


def derive_initial_seven_branch_run_ids(
    submission_preflight: dict[str, Any],
) -> tuple[UUID, tuple[UUID, ...]]:
    formal_key = str(submission_preflight.get("formal_submission_key", ""))
    if len(formal_key) != 64 or any(c not in "0123456789abcdef" for c in formal_key):
        raise ValueError("seven-branch preflight formal submission key is invalid")
    controller = uuid.uuid5(SEVEN_BRANCH_ID_NAMESPACE, f"{formal_key}:controller:epoch:0")
    children = tuple(
        uuid.uuid5(SEVEN_BRANCH_ID_NAMESPACE, f"{formal_key}:branch:{index}:round:0")
        for index in range(7)
    )
    return controller, children


def build_initial_seven_branch_schedule(
    *,
    request_template: dict[str, Any],
    submission_preflight: dict[str, Any],
    design_contract: SevenBranchDesignContract,
    target_manifest: dict[str, Any],
    target_manifest_sha256: str,
    controller_run_id: UUID,
    child_run_ids: tuple[UUID, ...],
) -> SevenBranchDesignSchedule:
    """Freeze the initial seven child runs before any Temporal history exists."""

    forbidden = {
        "run_id",
        "controller_run_id",
        "execution_contract",
        "exploration_round",
        "seven_branch_round",
        "submission_preflight",
        "multitarget_plan_template",
        "structure_runtime_by_target_key",
        "boltz_seeds",
    }
    if set(request_template) & forbidden:
        raise ValueError("seven-branch request template contains a run-time identity")
    if (
        submission_preflight.get("schema_version")
        != "ampgent.seven-branch-submission-preflight.1"
        or submission_preflight.get("status") != "ready_to_submit_unique_run"
        or submission_preflight.get("execution_authorized") is not True
        or submission_preflight.get("failed_gates") != []
        or submission_preflight.get("request_template_sha256")
        != sha256_json(request_template)
        or submission_preflight.get("design_contract_sha256")
        != design_contract.sha256()
    ):
        raise ValueError("seven-branch schedule requires a passed submission preflight")
    if target_manifest_sha256 != design_contract.target_manifest_sha256:
        raise ValueError("target manifest file identity differs from design contract")
    if len(child_run_ids) != 7 or len(set(child_run_ids)) != 7:
        raise ValueError("seven-branch schedule requires seven unique child identities")
    targets = target_manifest.get("targets")
    if not isinstance(targets, list) or len(targets) != 6:
        raise ValueError("seven-branch target manifest must contain six targets")
    runtime_by_key = {
        str(item["target_key"]): TargetSequenceRuntime(
            target_key=str(item["target_key"]),
            accession=str(item["protein_accession"]),
            sequence=str(item["sequence"]),
            sequence_sha256=str(item["sequence_sha256"]),
        )
        for item in targets
    }
    rounds: list[SevenBranchRoundRequest] = []
    for branch, child_run_id in zip(
        design_contract.branches, child_run_ids, strict=True
    ):
        binding, execution = build_seven_branch_round_execution_contract(
            design_contract,
            branch_key=branch.branch_key,
            round_ordinal=0,
        )
        child_request = copy.deepcopy(request_template)
        child_request.update(
            {
                "submission_preflight": copy.deepcopy(submission_preflight),
                "run_id": str(child_run_id),
                "controller_run_id": str(controller_run_id),
                "execution_contract": execution.model_dump(mode="json"),
                "seven_branch_round": binding.model_dump(mode="json"),
            }
        )
        rounds.append(
            SevenBranchRoundRequest(
                run_id=child_run_id,
                workflow_id=(
                    f"pepagent-seven-branch-{branch.branch_key}-r0-{child_run_id.hex}"
                ),
                request=child_request,
            )
        )
    return SevenBranchDesignSchedule(
        controller_run_id=controller_run_id,
        design_contract=design_contract,
        target_runtime_by_key=runtime_by_key,
        rounds=tuple(rounds),
    )
