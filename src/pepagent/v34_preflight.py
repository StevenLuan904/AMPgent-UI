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
from pepagent.v34_provider_releases import (
    verify_knowledge_provider_release,
    verify_pepshot_provider_release,
)


def _required_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise ValueError(f"v34 external contract file is missing: {path}")
    return path.read_bytes()


def build_external_source_manifest(
    *, root: Path, provider: str, include_patterns: tuple[str, ...]
) -> dict[str, object]:
    """Hash an allowlisted external source tree without machine-specific paths."""
    files = {
        path.resolve()
        for pattern in include_patterns
        for path in root.glob(pattern)
        if path.is_file()
    }
    if not files:
        raise ValueError(f"v34 {provider} source manifest is empty")
    resolved_root = root.resolve()
    relative_files: list[tuple[str, Path]] = []
    for path in files:
        try:
            relative = path.relative_to(resolved_root).as_posix()
        except ValueError as error:
            raise ValueError(f"v34 {provider} source path escaped its root") from error
        relative_files.append((relative, path))

    entries = []
    for relative, path in sorted(relative_files):
        payload = _required_bytes(path)
        entries.append(
            {
                "path": relative,
                "sha256": sha256_bytes(payload),
                "size_bytes": len(payload),
            }
        )
    result: dict[str, object] = {
        "schema_version": "1.0",
        "provider": provider,
        "include_patterns": list(include_patterns),
        "entries": entries,
    }
    result["manifest_sha256"] = sha256_json(result)
    return result


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
    source_manifests = {
        "knowledge": build_external_source_manifest(
            root=knowledge_root,
            provider="amp-system-kb",
            include_patterns=(
                "kbctl.py",
                "manifest.json",
                "requirements.txt",
                "src/amp_kb/**/*.py",
                "pipelines/**/*.py",
                "schemas/**/*.json",
                "policies/**/*.json",
            ),
        ),
        "pepshot": build_external_source_manifest(
            root=pepshot_root,
            provider="pepshot",
            include_patterns=(
                "AGENT_TOOL.md",
                "pyproject.toml",
                "uv.lock",
                "environment.renderer.yml",
                "src/pepshot/**/*.py",
                "src/pepshot/schemas/**/*.json",
            ),
        ),
    }
    provider_releases = {
        "knowledge": verify_knowledge_provider_release(
            knowledge_root / "exports" / "ampgent-readonly" / "LATEST.json",
            expected_latest_sha256=knowledge_contract.latest_sha256,
            expected_revision=knowledge_contract.release_revision,
            expected_release_manifest_sha256=(
                knowledge_contract.release_manifest_sha256
            ),
            expected_runtime_manifest_sha256=(
                knowledge_contract.runtime_manifest_sha256
            ),
            expected_policy_snapshot_sha256=knowledge_contract.active_policy_sha256,
            expected_policy_selection_receipt_sha256=(
                knowledge_contract.policy_selection_receipt_sha256
            ),
            expected_policy_roles_sha256=knowledge_contract.policy_roles_sha256,
            expected_policy_record_content_sha256=(
                knowledge_contract.policy_record_content_sha256
            ),
            expected_policy_specification_sha256=(
                knowledge_contract.policy_specification_sha256
            ),
        ),
        "pepshot": verify_pepshot_provider_release(
            pepshot_root
            / "evidence"
            / "releases"
            / "pepshot-runtime-v1"
            / "LATEST.json",
            expected_latest_sha256=pepshot_contract.latest_sha256,
            expected_source_revision=pepshot_contract.source_revision,
            expected_release_id=pepshot_contract.release_id,
            expected_release_manifest_sha256=(
                pepshot_contract.release_manifest_sha256
            ),
            expected_runtime_manifest_sha256=pepshot_contract.runtime_manifest_sha256,
            expected_bundle_id=pepshot_contract.fixture_bundle_id,
        ),
    }
    result: dict[str, object] = {
        "schema_version": "1.0",
        "frozen_contract_hashes": observed,
        "observed_entrypoint_hashes": entrypoint_hashes,
        "source_manifest_sha256": {
            provider: manifest["manifest_sha256"]
            for provider, manifest in source_manifests.items()
        },
        "source_manifests": source_manifests,
        "provider_release_receipts": provider_releases,
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
        provider_governance=manifest.provider_governance,
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
            "verify_deployed_executable_environment_matches_frozen_source_manifests",
            "run_isolated_adapter_shadow_fixture",
            "obtain_separate_formal_run_authorization",
            "verify_allowed_worker_identity_and_release",
            "register_exact_activities_only_after_authorization",
        ],
    }
    result["preflight_sha256"] = sha256_json(result)
    return result
