from __future__ import annotations

import argparse
import json
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
LOCAL_SEQUENCE_ROLE_QUEUES = {
    role: queue
    for role, queue in SEVEN_BRANCH_ROLE_QUEUES.items()
    if role != "v39-target-sequence"
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


def _validate_quality_worker_placement(
    placement: dict[str, Any],
    *,
    source_revision: str,
    release_sha256: str,
    target_sequence_required: bool,
) -> None:
    """Validate only the roles required by the frozen continuation scope.

    Target-agnostic AMP continuation never invokes target conditioning, so binding it
    to the target GPU would create an artificial deployment dependency.  A continuation
    containing any target-specific branch retains the complete four-role contract.
    """

    if target_sequence_required:
        _validate_worker_placement(
            placement,
            source_revision=source_revision,
            release_sha256=release_sha256,
        )
        return
    if placement.get("schema_version") != "ampgent.seven-branch-worker-placement.1":
        raise ValueError("seven-branch worker placement schema is invalid")
    workers = placement.get("workers")
    if not isinstance(workers, dict) or set(workers) != set(LOCAL_SEQUENCE_ROLE_QUEUES):
        raise ValueError("target-agnostic continuation must cover exactly three local roles")
    serialized = str(placement)
    if "192.168.99.32:2" in serialized or "192.168.99.32:3" in serialized:
        raise ValueError("seven-branch worker placement references a prohibited GPU")
    for role, queue in LOCAL_SEQUENCE_ROLE_QUEUES.items():
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


def _validate_quality_schedule_smoke(
    smoke: dict[str, Any], *, branch_keys: tuple[str, ...]
) -> None:
    if (
        smoke.get("schema_version")
        != "ampgent.seven-branch-quality-schedule-smoke.1"
        or smoke.get("status") != "schedule_frozen_not_submitted"
        or smoke.get("branch_key") not in branch_keys
        or smoke.get("schedule_schema_version")
        != "ampgent.seven_branch_top_up_schedule.v2"
        or not isinstance(smoke.get("expected_raw_occurrences"), int)
        or smoke["expected_raw_occurrences"] < 1
        or smoke.get("preflight_reused_for_submission") is not False
        or smoke.get("temporal_submitted") is not False
        or smoke.get("formal_runs_reserved") is not False
    ):
        raise ValueError("quality schedule smoke is not a frozen, unsubmitted v2 plan")
    for field in (
        "evidence_snapshot_sha256",
        "quality_progress_sha256",
        "schedule_sha256",
    ):
        _require_sha(smoke.get(field), length=64, label=field)


def _validate_quality_runtime_smoke(
    smoke: dict[str, Any],
    *,
    source_revision: str,
    release_sha256: str,
) -> None:
    metrics = smoke.get("required_sequence_metrics")
    if (
        smoke.get("schema_version")
        != "ampgent.seven-branch-quality-runtime-smoke.1"
        or smoke.get("source_revision") != source_revision
        or smoke.get("release_sha256") != release_sha256
        or smoke.get("same_release_executable") is not True
        or smoke.get("generator_succeeded") is not True
        or smoke.get("metrics_succeeded") is not True
        or smoke.get("database_persisted") is not False
        or not isinstance(metrics, list)
        or len(metrics) != 12
        or len(set(metrics)) != 12
    ):
        raise ValueError("quality runtime smoke does not prove generator + score-all boundary")


def build_seven_branch_quality_continuation_preflight(
    *,
    request_template: dict[str, Any],
    design_contract: SevenBranchDesignContract,
    design_contract_path: Path,
    target_manifest_path: Path,
    model_selection_path: Path,
    worker_placement: dict[str, Any],
    quality_schedule_smoke: dict[str, Any],
    quality_runtime_smoke: dict[str, Any],
    source_revision: str,
    release_sha256: str,
    branch_keys: tuple[str, ...],
    execution_authorized: bool,
    target_smoke: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fresh preflight for an immutable quality-successor round."""

    forbidden = {
        "run_id",
        "controller_run_id",
        "execution_contract",
        "seven_branch_round",
        "submission_preflight",
        "quality_continuation",
    }
    if set(request_template) & forbidden:
        raise ValueError("quality continuation request contains a run-time identity")
    _validate_v39_physicochemical_runtime(request_template)
    source_revision = _require_sha(source_revision, length=40, label="source")
    release_sha256 = _require_sha(release_sha256, length=64, label="release")
    if not branch_keys or len(branch_keys) != len(set(branch_keys)):
        raise ValueError("quality continuation branches must be non-empty and unique")
    branch_by_key = {branch.branch_key: branch for branch in design_contract.branches}
    if not set(branch_keys).issubset(branch_by_key):
        raise ValueError("quality continuation references an unknown branch")
    on_disk_contract = SevenBranchDesignContract.model_validate_json(
        design_contract_path.read_text(encoding="utf-8")
    )
    if on_disk_contract != design_contract:
        raise ValueError("seven-branch design contract bytes drifted")
    target_manifest_sha256 = sha256_file(target_manifest_path)
    model_selection_sha256 = sha256_file(model_selection_path)
    if target_manifest_sha256 != design_contract.target_manifest_sha256:
        raise ValueError("seven-branch target manifest identity drifted")
    if model_selection_sha256 != design_contract.model_selection_sha256:
        raise ValueError("seven-branch model selection identity drifted")
    target_sequence_required = any(
        branch_by_key[key].branch_kind == "target_specific" for key in branch_keys
    )
    _validate_quality_worker_placement(
        worker_placement,
        source_revision=source_revision,
        release_sha256=release_sha256,
        target_sequence_required=target_sequence_required,
    )
    _validate_quality_schedule_smoke(quality_schedule_smoke, branch_keys=branch_keys)
    _validate_quality_runtime_smoke(
        quality_runtime_smoke,
        source_revision=source_revision,
        release_sha256=release_sha256,
    )
    if set(quality_runtime_smoke["required_sequence_metrics"]) != set(
        design_contract.required_sequence_metrics
    ):
        raise ValueError("quality runtime smoke did not cover the frozen 12 metrics")
    if target_sequence_required:
        if target_smoke is None:
            raise ValueError("target-specific quality continuation requires target smoke")
        _validate_target_smoke(
            target_smoke,
            source_revision=source_revision,
            release_sha256=release_sha256,
        )
    elif target_smoke is not None:
        raise ValueError("target-agnostic quality continuation must not bind target smoke")
    identity = {
        "schema_version": "ampgent.seven-branch-quality-continuation-preflight.1",
        "request_template_sha256": sha256_json(request_template),
        "source_revision": source_revision,
        "release_sha256": release_sha256,
        "design_contract_sha256": design_contract.sha256(),
        "design_contract_artifact_sha256": sha256_file(design_contract_path),
        "target_manifest_sha256": target_manifest_sha256,
        "model_selection_sha256": model_selection_sha256,
        "branch_keys": list(branch_keys),
        "target_sequence_required": target_sequence_required,
        "worker_placement_sha256": sha256_json(worker_placement),
        "quality_schedule_smoke_sha256": sha256_json(quality_schedule_smoke),
        "quality_runtime_smoke_sha256": sha256_json(quality_runtime_smoke),
        "target_smoke_sha256": sha256_json(target_smoke) if target_smoke else None,
        "expected_raw_occurrences": quality_schedule_smoke["expected_raw_occurrences"],
        "required_sequence_metrics": sorted(design_contract.required_sequence_metrics),
        "required_sequence_metric_count": len(design_contract.required_sequence_metrics),
        "experiment_scope": "computational_peptide_quality_continuation",
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


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the immutable seven-branch submission preflight"
    )
    parser.add_argument("--request-template", type=Path, required=True)
    parser.add_argument("--design-contract", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--model-selection", type=Path, required=True)
    parser.add_argument("--worker-placement", type=Path, required=True)
    parser.add_argument("--target-smoke", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--release-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--authorize-execution", action="store_true")
    args = parser.parse_args()
    contract_path = args.design_contract.resolve()
    contract = SevenBranchDesignContract.model_validate(
        _load_json(contract_path)
    )
    result = build_seven_branch_submission_preflight(
        request_template=_load_json(args.request_template.resolve()),
        design_contract=contract,
        design_contract_path=contract_path,
        target_manifest_path=args.target_manifest.resolve(),
        model_selection_path=args.model_selection.resolve(),
        worker_placement=_load_json(args.worker_placement.resolve()),
        target_smoke=_load_json(args.target_smoke.resolve()),
        source_revision=args.source_revision,
        release_sha256=args.release_sha256,
        execution_authorized=args.authorize_execution,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
