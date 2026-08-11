from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_file, sha256_json

V37_ACTIVITY_CONTRACT: dict[str, dict[str, Any]] = {
    "generate_v37_batch": {
        "queue": "pepagent-cpu-portfolio",
        "stage": "proposal",
        "persists_attempt_lifecycle": True,
    },
    "persist_v37_generation_batch": {
        "queue": "pepagent-control",
        "stage": "proposal",
        "persists": ["ToolCall", "CandidateOccurrence", "Candidate", "Artifact"],
    },
    "evaluate_v37_metric": {
        "queue": "pepagent-cpu-metrics",
        "stage": "evaluation",
        "persists_attempt_lifecycle": True,
    },
    "persist_v37_metric": {
        "queue": "pepagent-control",
        "stage": "evaluation",
        "persists": ["ToolCall", "Evaluation", "ToolCallDependency", "Artifact"],
    },
    "consume_and_persist_v37_knowledge": {
        "queue": "pepagent-control",
        "stage": "evaluation",
        "persists": ["ToolCall", "ToolCallDependency", "Artifact", "LifecycleEvent"],
    },
    "persist_v37_stage1_shortlist": {
        "queue": "pepagent-control",
        "stage": "evaluation",
        "persists": [
            "ToolCall",
            "ToolCallDependency",
            "AgentDecision",
            "AgentDecisionToolCallEdge",
            "Artifact",
        ],
    },
    "predict_v37_boltz_pose": {
        "queue": "pepagent-gpu-boltz2",
        "stage": "boltz",
        "persists_attempt_lifecycle": True,
    },
    "persist_v37_boltz_pose": {
        "queue": "pepagent-control",
        "stage": "boltz",
        "persists": ["ToolCall", "Evaluation", "ToolCallDependency", "Artifact"],
    },
    "score_v37_rosetta_pose": {
        "queue": "pepagent-cpu-rosetta",
        "stage": "rosetta",
        "persists_attempt_lifecycle": True,
    },
    "persist_v37_rosetta_pose": {
        "queue": "pepagent-control",
        "stage": "rosetta",
        "persists": ["ToolCall", "Evaluation", "ToolCallDependency", "Artifact"],
    },
    "consume_and_persist_v37_pepshot": {
        "queue": "pepagent-control",
        "stage": "rosetta",
        "persists": [
            "ToolCall",
            "Evaluation",
            "ToolCallDependency",
            "AgentDecision",
            "AgentDecisionToolCallEdge",
            "Artifact",
        ],
    },
    "persist_v37_final_portfolio": {
        "queue": "pepagent-control",
        "stage": "rosetta",
        "persists": [
            "ToolCall",
            "ToolCallDependency",
            "AgentDecision",
            "AgentDecisionToolCallEdge",
            "Artifact",
        ],
    },
    "persist_v37_replay": {
        "queue": "pepagent-control",
        "stage": "rosetta",
        "persists": ["ToolCall", "ToolCallDependency", "Artifact", "LifecycleEvent"],
    },
}


def _literal_assignment(path: Path, name: str) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(statement.value)
    raise ValueError(f"missing literal assignment {name} in {path}")


def build_v37_worker_bridge_audit(
    *, repository_root: Path, raw_proposals_per_seed: int
) -> dict[str, Any]:
    """Audit reusable primitives without importing runtimes or touching workers."""
    workers = repository_root / "src" / "pepagent" / "model_workers"
    generator_files = {
        "hydramp": workers / "hydramp_generator_cli.py",
        "ampgan_v2": workers / "ampgan_v2_generator_cli.py",
        "amp_designer": workers / "amp_designer_generator_cli.py",
    }
    for path in generator_files.values():
        if not path.is_file():
            raise ValueError(f"v37 generator CLI is missing: {path}")
    amp_designer_budget = int(
        _literal_assignment(generator_files["amp_designer"], "EXPECTED_RAW_BUDGET")
    )
    activity_source = repository_root / "src" / "pepagent" / "workers" / "activities.py"
    portfolio_source = (
        repository_root / "src" / "pepagent" / "workers" / "portfolio_activities.py"
    )
    temporal_worker_source = (
        repository_root / "src" / "pepagent" / "workers" / "temporal_worker.py"
    )
    for path in (activity_source, portfolio_source, temporal_worker_source):
        if not path.is_file():
            raise ValueError(f"v37 worker source is missing: {path}")

    blockers = []
    if amp_designer_budget != raw_proposals_per_seed:
        blockers.append(
            {
                "code": "amp_designer_raw_budget_incompatible",
                "required": raw_proposals_per_seed,
                "observed": amp_designer_budget,
                "consumer_truncation_forbidden": True,
            }
        )
    blockers.extend(
        [
            {
                "code": "v37_activity_registry_incomplete",
                "missing_activity_names": sorted(V37_ACTIVITY_CONTRACT),
            },
            {
                "code": "v37_generator_runtime_manifests_not_frozen",
                "required_generators": ["hydramp", "ampgan_v2", "amp_designer"],
            },
            {
                "code": "v37_knowledge_and_pepshot_temporal_consumers_missing",
                "consumer_adaptation_forbidden": True,
            },
        ]
    )
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "activity_contract": V37_ACTIVITY_CONTRACT,
        "generator_cli_sha256": {
            generator: sha256_file(path) for generator, path in generator_files.items()
        },
        "existing_primitive_sha256": {
            "activities": sha256_file(activity_source),
            "portfolio_activities": sha256_file(portfolio_source),
            "temporal_worker": sha256_file(temporal_worker_source),
        },
        "raw_proposals_per_seed": raw_proposals_per_seed,
        "amp_designer_cli_fixed_raw_budget": amp_designer_budget,
        "all_attempts_must_persist_typed_lifecycle_events": True,
        "all_successes_require_typed_database_evidence": True,
        "database_object_store_only_replay_required": True,
        "remote_host_or_process_probe_performed": False,
        "activity_or_workflow_registered": False,
        "formal_run_authorized": False,
        "formal_run_submitted": False,
        "blockers": blockers,
        "status": "blocked_missing_executable_activity_bridge",
    }
    result["audit_sha256"] = sha256_json(result)
    return result


def build_v37_worker_role_config(
    registry: Mapping[str, Callable[..., Any]], *, workflow: type[Any]
) -> dict[str, tuple[str, list[Callable[..., Any]], list[type[Any]]]]:
    """Build exact Temporal role registration only for a complete callable registry."""
    expected = set(V37_ACTIVITY_CONTRACT)
    if set(registry) != expected:
        missing = sorted(expected - set(registry))
        unexpected = sorted(set(registry) - expected)
        raise ValueError(
            f"v37 activity registry drifted; missing={missing}, unexpected={unexpected}"
        )
    if any(not callable(registry[name]) for name in expected):
        raise ValueError("v37 activity registry contains a non-callable binding")
    roles: dict[str, tuple[str, list[Callable[..., Any]], list[type[Any]]]] = {}
    for role, queue in (
        ("v37-control", "pepagent-control"),
        ("v37-portfolio", "pepagent-cpu-portfolio"),
        ("v37-metrics", "pepagent-cpu-metrics"),
        ("v37-boltz2", "pepagent-gpu-boltz2"),
        ("v37-rosetta", "pepagent-cpu-rosetta"),
    ):
        activities = [
            registry[name]
            for name, contract in V37_ACTIVITY_CONTRACT.items()
            if contract["queue"] == queue
        ]
        roles[role] = (queue, activities, [workflow] if role == "v37-control" else [])
    return roles
