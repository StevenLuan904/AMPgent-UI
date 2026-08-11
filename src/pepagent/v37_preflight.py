from __future__ import annotations

from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_bytes, sha256_file, sha256_json
from pepagent.v37_evidence import build_v37_evidence_plan
from pepagent.v37_preregistration import load_v37_preregistration


def build_v37_static_preflight(config_path: Path) -> dict[str, Any]:
    manifest = load_v37_preregistration(config_path)
    source_contracts = manifest.generators["frozen_source_contracts"]
    verified_sources = {}
    for prefix in ("v23", "v24", "v32"):
        path = config_path.parent / source_contracts[f"{prefix}_path"]
        observed = sha256_file(path)
        expected = source_contracts[f"{prefix}_sha256"]
        if observed != expected:
            raise ValueError(f"v37 frozen {prefix} source contract drifted")
        verified_sources[prefix] = observed
    plan = build_v37_evidence_plan(manifest)
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "benchmark_id": manifest.benchmark_id,
        "config_sha256": sha256_bytes(config_path.read_bytes()),
        "evidence_plan_sha256": plan["plan_sha256"],
        "source_contract_sha256": verified_sources,
        "direction_authorized": True,
        "execution_authorized": False,
        "formal_run_submitted": False,
        "host_or_service_probe_performed": False,
        "status": "direction_authorized_pending_dynamic_preexecution_gates",
    }
    result["preflight_sha256"] = sha256_json(result)
    return result


def authorize_v37_submission_preflight(
    static_record: dict[str, Any], *, dynamic_gates: dict[str, bool]
) -> dict[str, Any]:
    required = {
        "implementation_committed_pushed_archived",
        "database_schema_exact",
        "services_healthy_zero_active_user_workflows",
        "provider_releases_exact",
        "worker_host_gpu_pid_role_queue_release_exact",
        "forbidden_resources_absent",
        "no_existing_v37_run_or_workflow",
    }
    if set(dynamic_gates) != required:
        raise ValueError("v37 dynamic gate set differs from submission contract")
    failed = sorted(key for key, passed in dynamic_gates.items() if not passed)
    result = {
        **static_record,
        "dynamic_gates": dict(sorted(dynamic_gates.items())),
        "execution_authorized": not failed,
        "formal_run_submitted": False,
        "status": "ready_to_submit_unique_run" if not failed else "blocked",
        "failed_gates": failed,
    }
    result["submission_preflight_sha256"] = sha256_json(result)
    return result
