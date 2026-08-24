from __future__ import annotations

from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.seven_branch_design import (
    SevenBranchDesignContract,
    build_seven_branch_round_execution_contract,
)
from pepagent.v39_preflight import _validate_v39_physicochemical_runtime

SEVEN_BRANCH_ROLE_QUEUES = {
    "v38-control": "pepagent-control-v38",
    "v38-generator": "pepagent-generator-v38",
    "v38-metrics": "pepagent-cpu-metrics-v38",
    "v39-target-sequence": "pepagent-gpu-target-sequence-v39",
}


def _require_sha(value: object, *, length: int, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"invalid {label}")
    return value


def _validate_worker_placement(
    placement: dict[str, Any], *, source_revision: str, release_sha256: str
) -> None:
    if placement.get("schema_version") != "ampgent.seven-branch-worker-placement.1":
        raise ValueError("seven-branch worker placement schema is invalid")
    workers = placement.get("workers")
    if not isinstance(workers, dict) or set(workers) != set(SEVEN_BRANCH_ROLE_QUEUES):
        raise ValueError("seven-branch worker placement must cover exactly four roles")
    serialized = str(placement)
    if "192.168.99.32:2" in serialized or "192.168.99.32:3" in serialized:
        raise ValueError("seven-branch worker placement references a prohibited GPU")
    for role, queue in SEVEN_BRANCH_ROLE_QUEUES.items():
        worker = workers[role]
        if (
            not isinstance(worker, dict)
            or worker.get("role") != role
            or worker.get("task_queue") != queue
            or worker.get("ampgent_owned") is not True
            or worker.get("foreign") is not False
            or not isinstance(worker.get("pid"), int)
            or worker["pid"] < 1
            or worker.get("source_revision") != source_revision
            or worker.get("release_sha256") != release_sha256
            or not worker.get("poller_identity")
        ):
            raise ValueError(f"seven-branch worker placement is invalid: {role}")
    target = workers["v39-target-sequence"]
    _require_sha(target.get("weights_sha256"), length=64, label="PepMLM weights")
    if target.get("physical_host") != "192.168.99.19" or target.get("gpu_index") != 2:
        raise ValueError("target-sequence worker is outside its frozen placement")


def _validate_target_smoke(
    smoke: dict[str, Any], *, source_revision: str, release_sha256: str
) -> None:
    targets = smoke.get("targets")
    if (
        smoke.get("schema_version") != "ampgent.six-target-conditional-smoke.1"
        or smoke.get("source_revision") != source_revision
        or smoke.get("release_sha256") != release_sha256
        or smoke.get("same_executable_subprocess") is not True
        or smoke.get("all_succeeded") is not True
        or not isinstance(targets, list)
        or len(targets) != 6
        or any(
            item.get("returncode") != 0
            or item.get("device") != "cuda"
            or item.get("conditional_ppl") is None
            or item.get("stderr_tail")
            for item in targets
        )
    ):
        raise ValueError("target-sequence smoke does not prove the deployable boundary")


def build_seven_branch_submission_preflight(
    *,
    request_template: dict[str, Any],
    design_contract: SevenBranchDesignContract,
    design_contract_path: Path,
    target_manifest_path: Path,
    model_selection_path: Path,
    worker_placement: dict[str, Any],
    target_smoke: dict[str, Any],
    source_revision: str,
    release_sha256: str,
    execution_authorized: bool,
) -> dict[str, Any]:
    forbidden = {
        "run_id",
        "controller_run_id",
        "execution_contract",
        "seven_branch_round",
        "submission_preflight",
        "multitarget_plan_template",
        "structure_runtime_by_target_key",
        "boltz_seeds",
    }
    if set(request_template) & forbidden:
        raise ValueError("seven-branch preflight request contains a run-time identity")
    _validate_v39_physicochemical_runtime(request_template)
    source_revision = _require_sha(source_revision, length=40, label="source")
    release_sha256 = _require_sha(release_sha256, length=64, label="release")
    on_disk_contract = SevenBranchDesignContract.model_validate_json(
        design_contract_path.read_text(encoding="utf-8")
    )
    if on_disk_contract != design_contract:
        raise ValueError("seven-branch design contract bytes drifted")
    design_contract_artifact_sha256 = sha256_file(design_contract_path)
    target_manifest_sha256 = sha256_file(target_manifest_path)
    if target_manifest_sha256 != design_contract.target_manifest_sha256:
        raise ValueError("seven-branch target manifest identity drifted")
    model_selection_sha256 = sha256_file(model_selection_path)
    if model_selection_sha256 != design_contract.model_selection_sha256:
        raise ValueError("seven-branch model selection identity drifted")
    _validate_worker_placement(
        worker_placement,
        source_revision=source_revision,
        release_sha256=release_sha256,
    )
    _validate_target_smoke(
        target_smoke,
        source_revision=source_revision,
        release_sha256=release_sha256,
    )
    raw_total = 0
    required_metrics: tuple[str, ...] | None = None
    branch_budgets: dict[str, int] = {}
    for branch in design_contract.branches:
        _, execution = build_seven_branch_round_execution_contract(
            design_contract,
            branch_key=branch.branch_key,
            round_ordinal=0,
        )
        if len(execution.required_sequence_metrics) != 12:
            raise ValueError("seven-branch execution contract must score all 12 metrics")
        if required_metrics is None:
            required_metrics = execution.required_sequence_metrics
        elif execution.required_sequence_metrics != required_metrics:
            raise ValueError("seven-branch metric contracts differ across branches")
        branch_budgets[branch.branch_key] = execution.expected_raw_occurrences
        raw_total += execution.expected_raw_occurrences
    if len(branch_budgets) != 7 or raw_total != 6600:
        raise ValueError("seven-branch initial epoch drifted from 7 branches / 6600 raw")
    identity = {
        "schema_version": "ampgent.seven-branch-submission-preflight.1",
        "request_template_sha256": sha256_json(request_template),
        "source_revision": source_revision,
        "release_sha256": release_sha256,
        "worker_placement_sha256": sha256_json(worker_placement),
        "target_smoke_sha256": sha256_json(target_smoke),
        "design_contract_sha256": design_contract.sha256(),
        "design_contract_artifact_sha256": design_contract_artifact_sha256,
        "target_manifest_sha256": target_manifest_sha256,
        "model_selection_sha256": model_selection_sha256,
        "branch_initial_raw_occurrences": branch_budgets,
        "initial_raw_occurrences": raw_total,
        "required_sequence_metrics": list(required_metrics or ()),
        "required_sequence_metric_count": len(required_metrics or ()),
        "delivery_quota": sum(
            branch.requested_delivery_count for branch in design_contract.branches
        ),
        "experiment_scope": "computational_peptide_design",
    }
    return {
        **identity,
        "formal_submission_key": sha256_json(identity),
        "status": (
            "ready_to_submit_unique_run"
            if execution_authorized
            else "blocked_execution_not_authorized"
        ),
        "execution_authorized": execution_authorized,
        "failed_gates": [] if execution_authorized else ["execution_not_authorized"],
    }
