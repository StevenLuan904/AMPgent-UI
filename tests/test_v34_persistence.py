from pathlib import Path

import pytest

from pepagent.db.models import CandidateOccurrence
from pepagent.provenance.hashing import sha256_json, sha256_text
from pepagent.v34_evidence import build_v34_evidence_plan
from pepagent.v34_persistence import verify_v34_database_object_replay
from pepagent.v34_preregistration import load_v34_preregistration

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_knowledge_pepshot_ablation_v34.yaml"


def _plan() -> dict:
    manifest = load_v34_preregistration(CONFIG)
    return build_v34_evidence_plan(
        manifest.parent_cohort["members"],
        order_salt=manifest.factorial_design["arm_order_salt"],
    )


def _database_fixture(plan: dict) -> tuple[dict, dict[str, dict]]:
    tool_calls = []
    call_id_by_logical = {}
    artifacts = []
    links = []
    payloads = {}
    occurrences = []
    decisions = []
    tools = [
        tool for episode in plan["episodes"] for tool in episode["tool_calls"]
    ] + list(plan["global_tool_calls"])
    proposal_logical_ids = {
        episode["tool_calls"][2]["logical_id"] for episode in plan["episodes"]
    }
    for index, tool in enumerate(tools, start=1):
        call_id = f"call-{index}"
        logical_id = tool["logical_id"]
        call_id_by_logical[logical_id] = call_id
        tool_calls.append(
            {
                "id": call_id,
                "status": "succeeded",
                "input_json": {"v34_logical_id": logical_id},
            }
        )
        for role in tool["required_artifact_roles"]:
            payload = {"logical_id": logical_id, "role": role}
            if role == "proposal_occurrences":
                occurrence_payloads = []
                for occurrence_rank in range(1, 9):
                    sequence = f"KRW{'K' * occurrence_rank}"
                    occurrence_payloads.append(
                        {
                            "occurrence_rank": occurrence_rank,
                            "occurrence_kind": "raw",
                            "sequence": sequence,
                            "sequence_sha256": sha256_text(sequence),
                            "metadata": {},
                        }
                    )
                payload = {
                    "schema_version": "1.0",
                    "occurrences": occurrence_payloads,
                }
                episode = next(
                    item
                    for item in plan["episodes"]
                    if item["tool_calls"][2]["logical_id"] == logical_id
                )
                occurrences.extend(
                    {
                        **occurrence,
                        "id": f"occurrence-{index}-{occurrence['occurrence_rank']}",
                        "tool_call_id": call_id,
                        "parent_candidate_id": episode["parent_id"],
                        "opaque_arm_label": episode["opaque_label"],
                    }
                    for occurrence in occurrence_payloads
                )
            digest = sha256_json(payload)
            artifact_id = f"artifact-{len(artifacts) + 1}"
            artifacts.append({"id": artifact_id, "sha256": digest})
            links.append(
                {"tool_call_id": call_id, "artifact_id": artifact_id, "role": role}
            )
            payloads[digest] = payload
    dependencies = [
        {
            "parent_tool_call_id": call_id_by_logical[parent],
            "child_tool_call_id": call_id_by_logical[child],
            "relation_type": "v34_preregistered_dependency",
        }
        for parent, child in plan["required_dependencies"]
    ]
    for episode in plan["episodes"]:
        decisions.append(
            {
                "decision_type": "v34_blinded_adjudication",
                "structured_json": {
                    "v34_logical_id": episode["blinded_adjudication_tool_id"],
                    "locked_before_assignment_reveal": True,
                },
            }
        )
    graph = {
        "graph_sha256": "f" * 64,
        "tool_calls": tool_calls,
        "tool_call_dependencies": dependencies,
        "artifacts": artifacts,
        "evidence_artifacts": links,
        "agent_decisions": decisions,
        "candidate_occurrences": occurrences,
    }
    assert len(proposal_logical_ids) * 8 == len(occurrences)
    return graph, payloads


def test_candidate_occurrence_schema_preserves_duplicate_proposal_events() -> None:
    table = CandidateOccurrence.__table__
    assert {"tool_call_id", "occurrence_rank", "opaque_arm_label"}.issubset(
        table.c.keys()
    )
    constraint_names = {item.name for item in table.constraints}
    assert "uq_candidate_occurrence_call_rank" in constraint_names


def test_v34_database_object_replay_covers_every_proposal_occurrence() -> None:
    plan = _plan()
    graph, payloads = _database_fixture(plan)
    result = verify_v34_database_object_replay(plan, graph, payloads)
    assert result["exact_replay"] is True
    assert result["tool_call_count"] == 770
    assert result["candidate_occurrence_count"] == 768


def test_v34_database_object_replay_rejects_missing_occurrence_row() -> None:
    plan = _plan()
    graph, payloads = _database_fixture(plan)
    graph["candidate_occurrences"].pop()
    with pytest.raises(ValueError, match="database proposal occurrences differ"):
        verify_v34_database_object_replay(plan, graph, payloads)


def test_v34_database_object_replay_rejects_corrupt_occurrence_artifact() -> None:
    plan = _plan()
    graph, payloads = _database_fixture(plan)
    proposal_link = next(
        item for item in graph["evidence_artifacts"] if item["role"] == "proposal_occurrences"
    )
    artifact = next(
        item for item in graph["artifacts"] if item["id"] == proposal_link["artifact_id"]
    )
    payloads[artifact["sha256"]]["occurrences"][0]["sequence"] = "DRIFT"
    with pytest.raises(ValueError, match="missing or corrupt"):
        verify_v34_database_object_replay(plan, graph, payloads)
