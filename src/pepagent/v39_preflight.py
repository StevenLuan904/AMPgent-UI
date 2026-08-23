from __future__ import annotations

from copy import deepcopy
from typing import Any

from pepagent.provenance.hashing import sha256_json
from pepagent.sequence_space_exploration import (
    build_default_v39_exploration_contract,
    build_v39_round_execution_contract,
)
from pepagent.v38_preflight import V38_ROLE_QUEUES


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
    if placement.get("schema_version") != "v38.worker-placement.1":
        raise ValueError("v39 worker placement schema is invalid")
    workers = placement.get("workers")
    if not isinstance(workers, dict) or set(workers) != set(V38_ROLE_QUEUES):
        raise ValueError("v39 worker placement must cover exactly five roles")
    serialized = str(placement)
    if "192.168.99.32:2" in serialized or "192.168.99.32:3" in serialized:
        raise ValueError("v39 worker placement references a prohibited GPU")
    for role, queue in V38_ROLE_QUEUES.items():
        worker = workers[role]
        if (
            not isinstance(worker, dict)
            or worker.get("role") != role
            or worker.get("task_queue") != queue
            or worker.get("ampgent_owned") is not True
            or worker.get("foreign") is not False
            or not isinstance(worker.get("pid"), int)
            or worker["pid"] < 1
            or not worker.get("poller_identity")
        ):
            raise ValueError(f"v39 worker placement is invalid: {role}")
        _require_sha(worker.get("source_revision"), length=40, label="source")
        _require_sha(worker.get("release_sha256"), length=64, label="release")
    for role in ("v38-control", "v38-generator", "v38-metrics"):
        if (
            workers[role]["source_revision"] != source_revision
            or workers[role]["release_sha256"] != release_sha256
        ):
            raise ValueError("v39 sequence worker identity drifted from the release")


def _validate_release_smoke(
    smoke: dict[str, Any], *, source_revision: str, release_sha256: str
) -> None:
    if (
        smoke.get("schema_version") != "v39.release-smoke.1"
        or smoke.get("source_revision") != source_revision
        or smoke.get("release_sha256") != release_sha256
        or smoke.get("same_executable") is not True
        or smoke.get("guarded_launcher") is not True
        or smoke.get("release_bytes_loaded") is not True
        or int(smoke.get("guarded_metric_tests", 0)) < 5
        or int(smoke.get("workflow_tests", 0)) < 19
        or "guruprasad_instability_index" not in smoke.get("metric_names", [])
    ):
        raise ValueError("v39 release smoke does not prove the deployable boundary")


def build_v39_submission_preflight(
    *,
    request_template: dict[str, Any],
    worker_placement: dict[str, Any],
    release_smoke: dict[str, Any],
    source_revision: str,
    release_sha256: str,
    benchmark_sha256: str,
    target_panel_sha256: str,
    target_identity_witness_sha256: str,
    model_registry_audit_sha256: str,
    enterprise_registry_authorized: bool,
    exploratory_research_authorized: bool = False,
    disclosed_model_registry_gaps: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Bind v39's multi-round budget to deployable evidence before reservation."""

    if set(request_template) & {
        "run_id",
        "controller_run_id",
        "exploration_round",
        "submission_preflight",
    }:
        raise ValueError("v39 preflight request contains a run-time identity")
    source_revision = _require_sha(source_revision, length=40, label="source")
    release_sha256 = _require_sha(release_sha256, length=64, label="release")
    evidence = {
        "benchmark_sha256": _require_sha(benchmark_sha256, length=64, label="benchmark"),
        "target_panel_sha256": _require_sha(target_panel_sha256, length=64, label="target panel"),
        "target_identity_witness_sha256": _require_sha(
            target_identity_witness_sha256, length=64, label="target identity witness"
        ),
        "model_registry_audit_sha256": _require_sha(
            model_registry_audit_sha256, length=64, label="model registry audit"
        ),
    }
    _validate_worker_placement(
        worker_placement,
        source_revision=source_revision,
        release_sha256=release_sha256,
    )
    _validate_release_smoke(
        release_smoke,
        source_revision=source_revision,
        release_sha256=release_sha256,
    )
    contract = build_default_v39_exploration_contract()
    _, execution = build_v39_round_execution_contract(contract, round_ordinal=0)
    metrics = list(execution.required_sequence_metrics)
    if (
        len(execution.cells) != 18
        or execution.expected_raw_occurrences != 1800
        or len(metrics) != 12
        or "guruprasad_instability_index" not in metrics
    ):
        raise ValueError("v39 executable contract drifted from 18/1800/12")
    request = deepcopy(request_template)
    identity = {
        "schema_version": "v39.exploration-submission-preflight.1",
        "request_template_sha256": sha256_json(request),
        "source_revision": source_revision,
        "release_sha256": release_sha256,
        "worker_placement_sha256": sha256_json(worker_placement),
        "release_smoke_sha256": sha256_json(release_smoke),
        "exploration_contract_sha256": contract.sha256(),
        "maximum_rounds": contract.maximum_rounds,
        "cells_per_round": len(execution.cells),
        "raw_occurrences_per_round": execution.expected_raw_occurrences,
        "maximum_raw_occurrences": contract.expected_maximum_raw_occurrences,
        "required_sequence_metrics": metrics,
        "required_sequence_metric_count": len(metrics),
        "maximum_initial_sequence_evaluations": (
            contract.expected_maximum_raw_occurrences * len(metrics)
        ),
        **evidence,
    }
    enterprise_ready = enterprise_registry_authorized is True
    exploratory_ready = exploratory_research_authorized is True
    if exploratory_ready and not enterprise_ready and not disclosed_model_registry_gaps:
        raise ValueError("exploratory v39 authorization must disclose registry gaps")
    authorized = enterprise_ready or exploratory_ready
    execution_scope = (
        "enterprise_formal_science"
        if enterprise_ready
        else "exploratory_research"
        if exploratory_ready
        else "blocked"
    )
    identity = {
        **identity,
        "execution_scope": execution_scope,
        "enterprise_ready": enterprise_ready,
        "model_registry_gaps": list(disclosed_model_registry_gaps),
    }
    return {
        **identity,
        "formal_submission_key": sha256_json(identity),
        "status": (
            "ready_to_submit_unique_run"
            if authorized
            else "blocked_enterprise_registry_authorization"
        ),
        "execution_authorized": authorized,
        "failed_gates": [] if authorized else ["enterprise_registry_not_authorized"],
        "result_label_restrictions": (
            []
            if enterprise_ready
            else [
                "provisional_exploratory_evidence_only",
                "not_enterprise_ready",
                "not_experimental_validation",
            ]
        ),
    }
