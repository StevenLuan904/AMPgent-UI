from __future__ import annotations

import copy
import uuid
from typing import Any
from uuid import UUID

from pepagent.provenance.hashing import sha256_json
from pepagent.seven_branch_design import (
    BranchProgress,
    BranchQualityProgress,
    SevenBranchDesignContract,
    SevenBranchDesignSchedule,
    SevenBranchRoundRequest,
    SevenBranchTopUpEpochBranch,
    SevenBranchTopUpSchedule,
    TargetSequenceRuntime,
    build_seven_branch_round_execution_contract,
    plan_branch_quality_top_up,
    plan_branch_top_up,
)

SEVEN_BRANCH_ID_NAMESPACE = UUID("11897b4e-2a41-44dd-b741-a978e77d48ed")
_RUNTIME_IDENTITY_FIELDS = {
    "run_id",
    "controller_run_id",
    "execution_contract",
    "exploration_round",
    "seven_branch_round",
    "quality_continuation",
    "submission_preflight",
    "multitarget_plan_template",
    "structure_runtime_by_target_key",
    "boltz_seeds",
}


def _validate_schedule_preflight(
    *,
    request_template: dict[str, Any],
    submission_preflight: dict[str, Any],
    design_contract: SevenBranchDesignContract,
) -> None:
    if set(request_template) & _RUNTIME_IDENTITY_FIELDS:
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

    _validate_schedule_preflight(
        request_template=request_template,
        submission_preflight=submission_preflight,
        design_contract=design_contract,
    )
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


def derive_top_up_seven_branch_run_ids(
    *,
    parent_controller_run_id: UUID,
    epoch_ordinal: int,
    branch_evidence_sha256_by_key: dict[str, str],
) -> tuple[UUID, dict[str, UUID]]:
    if epoch_ordinal < 1:
        raise ValueError("seven-branch top-up epoch must be positive")
    if not branch_evidence_sha256_by_key:
        raise ValueError("seven-branch top-up requires incomplete branches")
    identity = sha256_json(
        {
            "parent_controller_run_id": str(parent_controller_run_id),
            "epoch_ordinal": epoch_ordinal,
            "branch_evidence_sha256_by_key": branch_evidence_sha256_by_key,
        }
    )
    controller = uuid.uuid5(
        SEVEN_BRANCH_ID_NAMESPACE, f"{identity}:controller:epoch:{epoch_ordinal}"
    )
    children = {
        branch_key: uuid.uuid5(
            SEVEN_BRANCH_ID_NAMESPACE,
            f"{identity}:branch:{branch_key}:epoch:{epoch_ordinal}",
        )
        for branch_key in branch_evidence_sha256_by_key
    }
    return controller, children


def build_top_up_seven_branch_schedule(
    *,
    request_template: dict[str, Any],
    submission_preflight: dict[str, Any],
    design_contract: SevenBranchDesignContract,
    target_manifest: dict[str, Any],
    target_manifest_sha256: str,
    parent_controller_run_id: UUID,
    controller_run_id: UUID,
    epoch_ordinal: int,
    branch_evidence: dict[str, dict[str, Any]],
    child_run_ids_by_key: dict[str, UUID],
) -> SevenBranchTopUpSchedule:
    """Freeze one successor epoch from durable cumulative branch observations."""

    _validate_schedule_preflight(
        request_template=request_template,
        submission_preflight=submission_preflight,
        design_contract=design_contract,
    )
    if target_manifest_sha256 != design_contract.target_manifest_sha256:
        raise ValueError("target manifest file identity differs from design contract")
    if set(branch_evidence) != set(child_run_ids_by_key):
        raise ValueError("top-up child identities do not match incomplete branches")
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
    branch_by_key = {item.branch_key: item for item in design_contract.branches}
    epoch_branches: list[SevenBranchTopUpEpochBranch] = []
    for branch_key in branch_by_key:
        if branch_key not in branch_evidence:
            continue
        evidence = branch_evidence[branch_key]
        progress = BranchProgress.model_validate(evidence["progress"])
        quality_payload = evidence.get("quality_progress")
        if quality_payload is None:
            plan = plan_branch_top_up(
                branch_by_key[branch_key],
                progress,
                next_round_ordinal=int(evidence["next_round_ordinal"]),
            )
        else:
            quality = BranchQualityProgress.model_validate(quality_payload)
            plan = plan_branch_quality_top_up(
                branch_by_key[branch_key],
                progress,
                quality,
                next_round_ordinal=int(evidence["next_round_ordinal"]),
            )
        if plan.action not in {
            "freeze_successor_round",
            "freeze_quality_successor_round",
        }:
            raise ValueError("top-up evidence includes a completed branch")
        binding, execution = build_seven_branch_round_execution_contract(
            design_contract,
            branch_key=branch_key,
            round_ordinal=plan.next_round_ordinal,
            raw_budget=plan.recommended_raw_budget,
        )
        child_run_id = child_run_ids_by_key[branch_key]
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
        if quality_payload is not None:
            child_request["quality_continuation"] = {
                "schema_version": "ampgent.seven-branch-quality-continuation.1",
                "quality_progress": quality.model_dump(mode="json"),
                "quality_progress_sha256": quality.sha256(),
                "quality_top_up_plan": plan.model_dump(mode="json"),
                "preserve_overlapping_archives": True,
            }
        frozen_round = SevenBranchRoundRequest(
            run_id=child_run_id,
            workflow_id=(
                f"pepagent-seven-branch-{branch_key}-r{plan.next_round_ordinal}-"
                f"{child_run_id.hex}"
            ),
            request=child_request,
        )
        epoch_branches.append(
            SevenBranchTopUpEpochBranch(
                branch_key=branch_key,
                prior_source_run_ids=tuple(
                    UUID(str(item)) for item in evidence["source_run_ids"]
                ),
                prior_evidence_snapshot_sha256=str(evidence["snapshot_sha256"]),
                top_up_plan=plan,
                frozen_round=frozen_round,
            )
        )
    return SevenBranchTopUpSchedule(
        schema_version=(
            "ampgent.seven_branch_top_up_schedule.v2"
            if any("quality_progress" in item for item in branch_evidence.values())
            else "ampgent.seven_branch_top_up_schedule.v1"
        ),
        controller_run_id=controller_run_id,
        parent_controller_run_id=parent_controller_run_id,
        epoch_ordinal=epoch_ordinal,
        design_contract=design_contract,
        target_runtime_by_key=runtime_by_key,
        branches=tuple(epoch_branches),
    )
