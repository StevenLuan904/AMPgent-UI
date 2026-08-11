from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from pepagent.provenance.hashing import sha256_json
from pepagent.v34_ablation import deterministic_arm_order

V34_EVIDENCE_VERSION = "v34-factorial-evidence-v1"

ARM_FACTORS = {
    "baseline": (False, False),
    "cards_only": (True, False),
    "pepshot_only": (False, True),
    "cards_and_pepshot": (True, True),
}


def _opaque_label(parent_id: str, arm: str, salt: str) -> str:
    digest = hashlib.sha256(f"{salt}\x00{parent_id}\x00{arm}".encode()).hexdigest()
    return f"v34-cell-{digest[:20]}"


def _tool(prefix: str, name: str, artifact_roles: Sequence[str]) -> dict[str, Any]:
    return {
        "logical_id": f"{prefix}:{name}",
        "tool_name": name,
        "required_artifact_roles": list(artifact_roles),
    }


def build_v34_evidence_plan(
    parents: Sequence[Mapping[str, Any]], *, order_salt: str
) -> dict[str, Any]:
    """Build the exact evidence graph shape before any v34 episode is executed."""
    if len(parents) != 24:
        raise ValueError("v34 requires exactly 24 frozen parents")
    candidate_ids = [str(item["candidate_id"]) for item in parents]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("v34 parent identities must be unique")

    episodes: list[dict[str, Any]] = []
    all_tool_ids: list[str] = []
    all_dependencies: list[tuple[str, str]] = []
    adjudication_ids: list[str] = []
    evaluation_ids: list[str] = []
    for expected_order, parent in enumerate(parents, start=1):
        if int(parent["order"]) != expected_order:
            raise ValueError("v34 parent order must be contiguous and frozen")
        parent_id = str(parent["candidate_id"])
        for arm_order, arm in enumerate(
            deterministic_arm_order(parent_id, order_salt), start=1
        ):
            knowledge_on, pepshot_on = ARM_FACTORS[arm]
            opaque_label = _opaque_label(parent_id, arm, order_salt)
            prefix = f"{parent_id}:{opaque_label}"
            base = _tool(prefix, "v34-base-brief", ("base_prompt", "input_manifest"))
            if knowledge_on:
                knowledge = _tool(
                    prefix,
                    "v34-knowledge-context",
                    ("context_pack", "retrieval_trace", "policy_snapshot", "passage_manifest"),
                )
            else:
                knowledge = _tool(prefix, "v34-knowledge-absent", ("tool_absent_marker",))
            proposal = _tool(
                prefix,
                "v34-proposal",
                ("raw_proposals", "proposal_occurrences", "agent_response"),
            )
            structure = _tool(
                prefix,
                "v34-structure-evaluation",
                ("structure_manifest", "coordinate_audit", "rosetta_manifest"),
            )
            if pepshot_on:
                pepshot = _tool(
                    prefix,
                    "v34-pepshot-review",
                    (
                        "agent_request",
                        "bundle_manifest",
                        "coordinate_audit",
                        "image_manifest",
                        "validated_review",
                    ),
                )
            else:
                pepshot = _tool(prefix, "v34-pepshot-absent", ("tool_absent_marker",))
            decision = _tool(
                prefix,
                "v34-intervention-decision",
                ("decision_response", "adoption_rejection_revision_manifest"),
            )
            evaluation = _tool(
                prefix,
                "v34-independent-evaluation",
                ("holdout_endpoint_vector", "cost_and_failure_vector"),
            )
            adjudication = _tool(
                prefix,
                "v34-blinded-adjudication",
                ("locked_blinded_adjudication",),
            )
            tools = [
                base,
                knowledge,
                proposal,
                structure,
                pepshot,
                decision,
                evaluation,
                adjudication,
            ]
            tool_ids = {item["tool_name"]: item["logical_id"] for item in tools}
            dependencies = [
                (base["logical_id"], knowledge["logical_id"]),
                (base["logical_id"], proposal["logical_id"]),
                (knowledge["logical_id"], proposal["logical_id"]),
                (proposal["logical_id"], structure["logical_id"]),
                (structure["logical_id"], pepshot["logical_id"]),
                (proposal["logical_id"], decision["logical_id"]),
                (pepshot["logical_id"], decision["logical_id"]),
                (decision["logical_id"], evaluation["logical_id"]),
                (structure["logical_id"], evaluation["logical_id"]),
                (evaluation["logical_id"], adjudication["logical_id"]),
            ]
            episodes.append(
                {
                    "parent_order": expected_order,
                    "parent_id": parent_id,
                    "parent_sequence_sha256": str(parent["sequence_sha256"]),
                    "arm_order": arm_order,
                    "opaque_label": opaque_label,
                    "arm_identity_sealed_until_reveal": arm,
                    "knowledge_on": knowledge_on,
                    "pepshot_on": pepshot_on,
                    "tool_calls": tools,
                    "dependencies": [list(item) for item in dependencies],
                    "intervention_decision_tool_id": tool_ids[
                        "v34-intervention-decision"
                    ],
                    "blinded_adjudication_tool_id": tool_ids[
                        "v34-blinded-adjudication"
                    ],
                }
            )
            all_tool_ids.extend(item["logical_id"] for item in tools)
            all_dependencies.extend(dependencies)
            adjudication_ids.append(adjudication["logical_id"])
            evaluation_ids.append(evaluation["logical_id"])

    if len(set(all_tool_ids)) != len(all_tool_ids):
        raise ValueError("v34 logical ToolCall identities collided")
    reveal = _tool(
        "v34-global",
        "v34-assignment-reveal",
        ("sealed_assignment_manifest", "reveal_event"),
    )
    analysis = _tool(
        "v34-global",
        "v34-factorial-analysis",
        ("endpoint_contrasts", "bootstrap_intervals", "promotion_verdict"),
    )
    all_tool_ids.extend((reveal["logical_id"], analysis["logical_id"]))
    all_dependencies.extend((item, reveal["logical_id"]) for item in adjudication_ids)
    all_dependencies.extend((item, analysis["logical_id"]) for item in evaluation_ids)
    all_dependencies.append((reveal["logical_id"], analysis["logical_id"]))
    plan = {
        "schema_version": "1.0",
        "evidence_version": V34_EVIDENCE_VERSION,
        "parent_manifest_sha256": sha256_json(list(parents)),
        "episode_count": len(episodes),
        "episodes": episodes,
        "global_tool_calls": [reveal, analysis],
        "required_tool_call_ids": all_tool_ids,
        "required_dependencies": [list(item) for item in all_dependencies],
        "adjudication_must_lock_before_reveal": True,
    }
    plan["plan_sha256"] = sha256_json(plan)
    return plan


def validate_v34_replay_graph(plan: Mapping[str, Any], graph: Mapping[str, Any]) -> None:
    """Fail closed when a database/object-store replay omits or rewires v34 evidence."""
    expected_tools = set(plan["required_tool_call_ids"])
    actual_tools = [str(item["logical_id"]) for item in graph.get("tool_calls", [])]
    if len(actual_tools) != len(set(actual_tools)):
        raise ValueError("v34 replay contains duplicate ToolCall identities")
    if set(actual_tools) != expected_tools:
        raise ValueError("v34 replay ToolCall set is incomplete or unexpected")
    if any(item.get("status") != "succeeded" for item in graph.get("tool_calls", [])):
        raise ValueError("v34 replay contains a non-succeeded ToolCall")

    expected_dependencies = {tuple(item) for item in plan["required_dependencies"]}
    actual_dependencies = {
        (str(item["parent_logical_id"]), str(item["child_logical_id"]))
        for item in graph.get("dependencies", [])
    }
    if actual_dependencies != expected_dependencies:
        raise ValueError("v34 replay dependency graph drifted")

    expected_roles: dict[str, set[str]] = {}
    for episode in plan["episodes"]:
        for tool in episode["tool_calls"]:
            expected_roles[tool["logical_id"]] = set(tool["required_artifact_roles"])
    for tool in plan["global_tool_calls"]:
        expected_roles[tool["logical_id"]] = set(tool["required_artifact_roles"])
    actual_roles: dict[str, list[str]] = {}
    for artifact in graph.get("artifacts", []):
        tool_id = str(artifact["tool_call_logical_id"])
        actual_roles.setdefault(tool_id, []).append(str(artifact["role"]))
        sha = str(artifact.get("sha256", ""))
        if len(sha) != 64 or any(character not in "0123456789abcdef" for character in sha):
            raise ValueError("v34 replay artifact has an invalid content hash")
    for tool_id, roles in expected_roles.items():
        if Counter(actual_roles.get(tool_id, [])) != Counter(roles):
            raise ValueError(f"v34 replay artifact roles drifted for {tool_id}")

    decisions = graph.get("adjudications", [])
    expected_adjudications = {
        episode["blinded_adjudication_tool_id"] for episode in plan["episodes"]
    }
    actual_adjudications = {str(item["tool_call_logical_id"]) for item in decisions}
    if actual_adjudications != expected_adjudications:
        raise ValueError("v34 replay blinded adjudication set is incomplete")
    if not all(item.get("locked_before_assignment_reveal") is True for item in decisions):
        raise ValueError("v34 replay reveals arm identity before adjudication lock")
