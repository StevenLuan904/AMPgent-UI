from __future__ import annotations

from pathlib import Path

from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.v34_evidence import build_v34_evidence_plan
from pepagent.v34_external_adapters import (
    DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT,
    DEFAULT_PEPSHOT_ADAPTER_CONTRACT,
    KnowledgeAdapterContract,
    PepShotAdapterContract,
)
from pepagent.v34_preregistration import load_v34_preregistration


def _required_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise ValueError(f"v34 external contract file is missing: {path}")
    return path.read_bytes()


def verify_v34_external_contract_files(
    *,
    knowledge_root: Path,
    pepshot_root: Path,
    knowledge_contract: KnowledgeAdapterContract = DEFAULT_KNOWLEDGE_ADAPTER_CONTRACT,
    pepshot_contract: PepShotAdapterContract = DEFAULT_PEPSHOT_ADAPTER_CONTRACT,
) -> dict[str, object]:
    """Verify immutable provider contracts without invoking either external tool."""
    frozen_files = {
        "knowledge_context_schema": (
            knowledge_root / "schemas" / "design_context.schema.json",
            knowledge_contract.context_schema_sha256,
        ),
        "knowledge_active_policy": (
            knowledge_root / "policies" / "agent_context_defaults.json",
            knowledge_contract.active_policy_sha256,
        ),
        "pepshot_agent_contract": (
            pepshot_root / "AGENT_TOOL.md",
            pepshot_contract.contract_sha256,
        ),
        "pepshot_request_schema": (
            pepshot_root / "src" / "pepshot" / "schemas" / "agent-request.schema.json",
            pepshot_contract.request_schema_sha256,
        ),
        "pepshot_review_schema": (
            pepshot_root / "src" / "pepshot" / "schemas" / "review.schema.json",
            pepshot_contract.review_schema_sha256,
        ),
    }
    observed: dict[str, str] = {}
    for role, (path, expected_sha256) in frozen_files.items():
        observed[role] = sha256_bytes(_required_bytes(path))
        if observed[role] != expected_sha256:
            raise ValueError(f"v34 external contract drifted: {role}")

    entrypoints = {
        "knowledge_cli": knowledge_root / "kbctl.py",
        "knowledge_context_service": (
            knowledge_root / "src" / "amp_kb" / "context_service.py"
        ),
        "pepshot_cli": pepshot_root / "src" / "pepshot" / "cli.py",
        "pepshot_bundle": pepshot_root / "src" / "pepshot" / "bundle.py",
        "pepshot_review": pepshot_root / "src" / "pepshot" / "review.py",
    }
    entrypoint_hashes = {
        role: sha256_bytes(_required_bytes(path)) for role, path in entrypoints.items()
    }
    result: dict[str, object] = {
        "schema_version": "1.0",
        "frozen_contract_hashes": observed,
        "observed_entrypoint_hashes": entrypoint_hashes,
        "external_commands_executed": False,
    }
    result["footprint_sha256"] = sha256_json(result)
    return result


def build_v34_offline_preflight(
    *,
    config_path: Path,
    knowledge_root: Path,
    pepshot_root: Path,
) -> dict[str, object]:
    """Build a deterministic shadow-readiness record that cannot authorize a run."""
    manifest = load_v34_preregistration(config_path)
    footprints = verify_v34_external_contract_files(
        knowledge_root=knowledge_root,
        pepshot_root=pepshot_root,
    )
    plan = build_v34_evidence_plan(
        manifest.parent_cohort["members"],
        order_salt=manifest.factorial_design["arm_order_salt"],
    )
    result: dict[str, object] = {
        "schema_version": "1.0",
        "benchmark_id": manifest.benchmark_id,
        "config_sha256": sha256_bytes(_required_bytes(config_path)),
        "implementation_revision": manifest.formal_run.implementation_revision,
        "parent_manifest_sha256": plan["parent_manifest_sha256"],
        "evidence_plan_sha256": plan["plan_sha256"],
        "episode_count": plan["episode_count"],
        "tool_call_count": len(plan["required_tool_call_ids"]),
        "raw_proposal_occurrence_count": (
            plan["episode_count"] * plan["raw_proposals_per_episode"]
        ),
        "external_footprint_sha256": footprints["footprint_sha256"],
        "offline_contracts_verified": True,
        "temporal_activities_registered": False,
        "formal_run_authorized": False,
        "formal_run_submitted": False,
        "status": "ready_for_isolated_shadow_fixture_not_formal_execution",
        "remaining_gates": [
            "freeze_executable_environment_and_source_manifest",
            "run_isolated_adapter_shadow_fixture",
            "obtain_separate_formal_run_authorization",
            "verify_allowed_worker_identity_and_release",
            "register_exact_activities_only_after_authorization",
        ],
    }
    result["preflight_sha256"] = sha256_json(result)
    return result
