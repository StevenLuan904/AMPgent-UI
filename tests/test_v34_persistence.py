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
        provider_governance=manifest.provider_governance,
    )


def _database_fixture(plan: dict) -> tuple[dict, dict[str, dict]]:
    tool_calls = []
    call_id_by_logical = {}
    artifacts = []
    links = []
    payloads = {}
    occurrences = []
    decisions = []
    decision_edges = []
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
            if role == "provider_change_request_ledger":
                payload = {
                    "schema_version": "1.0",
                    "provider_owner_tasks": plan["provider_governance_contract"][
                        "provider_owner_tasks"
                    ],
                    "formal_run_release_hot_swap_performed": False,
                    "database_parentage_verified": True,
                    "all_external_requests_have_receipts": True,
                    "change_requests": [],
                }
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
                            "candidate_id": None,
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
            if role == "holdout_endpoint_vector":
                payload = {"schema_version": "1.0", "evaluations": []}
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
        intervention_logical = episode["intervention_decision_tool_id"]
        intervention_id = f"decision-{len(decisions) + 1}"
        decisions.append(
            {
                "id": intervention_id,
                "decision_type": "v34_intervention",
                "status": "succeeded",
                "structured_json": {"v34_logical_id": intervention_logical},
            }
        )
        for parent_logical, child_logical in episode["dependencies"]:
            if child_logical == intervention_logical:
                decision_edges.append(
                    {
                        "decision_id": intervention_id,
                        "tool_call_id": call_id_by_logical[parent_logical],
                        "direction": "input",
                        "relation_type": "observes_episode_evidence",
                    }
                )
        decision_edges.append(
            {
                "decision_id": intervention_id,
                "tool_call_id": call_id_by_logical[intervention_logical],
                "direction": "output",
                "relation_type": "materializes_intervention_decision",
            }
        )
        adjudication_logical = episode["blinded_adjudication_tool_id"]
        evaluation_logical = next(
            tool["logical_id"]
            for tool in episode["tool_calls"]
            if tool["tool_name"] == "v34-independent-evaluation"
        )
        adjudication_id = f"decision-{len(decisions) + 1}"
        decisions.append(
            {
                "id": adjudication_id,
                "decision_type": "v34_blinded_adjudication",
                "status": "succeeded",
                "structured_json": {
                    "v34_logical_id": adjudication_logical,
                    "locked_before_assignment_reveal": True,
                },
            }
        )
        decision_edges.extend(
            [
                {
                    "decision_id": adjudication_id,
                    "tool_call_id": call_id_by_logical[evaluation_logical],
                    "direction": "input",
                    "relation_type": "observes_holdout_evaluation",
                },
                {
                    "decision_id": adjudication_id,
                    "tool_call_id": call_id_by_logical[adjudication_logical],
                    "direction": "output",
                    "relation_type": "materializes_blinded_adjudication",
                },
            ]
        )
    analysis_logical = "v34-global:v34-factorial-analysis"
    promotion_id = f"decision-{len(decisions) + 1}"
    decisions.append(
        {
            "id": promotion_id,
            "decision_type": "v34_factorial_promotion",
            "status": "succeeded",
            "structured_json": {"v34_logical_id": analysis_logical},
        }
    )
    for parent_logical, child_logical in plan["required_dependencies"]:
        if child_logical == analysis_logical:
            decision_edges.append(
                {
                    "decision_id": promotion_id,
                    "tool_call_id": call_id_by_logical[parent_logical],
                    "direction": "input",
                    "relation_type": "observes_locked_factorial_evidence",
                }
            )
    decision_edges.append(
        {
            "decision_id": promotion_id,
            "tool_call_id": call_id_by_logical[analysis_logical],
            "direction": "output",
            "relation_type": "materializes_promotion_verdict",
        }
    )
    graph = {
        "graph_sha256": "f" * 64,
        "tool_calls": tool_calls,
        "tool_call_dependencies": dependencies,
        "artifacts": artifacts,
        "evidence_artifacts": links,
        "agent_decisions": decisions,
        "agent_decision_tool_call_edges": decision_edges,
        "evaluations": [],
        "candidate_occurrences": occurrences,
        "v34_parent_candidates": [
            {
                "id": episode["parent_id"],
                "sequence_sha256": episode["parent_sequence_sha256"],
            }
            for episode in plan["episodes"][::4]
        ],
        "provider_governance_lineage": [],
        "provider_governance_artifacts": [],
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
    assert result["tool_call_count"] == 771
    assert result["candidate_occurrence_count"] == 768


def test_v34_database_object_replay_rejects_missing_occurrence_row() -> None:
    plan = _plan()
    graph, payloads = _database_fixture(plan)
    graph["candidate_occurrences"].pop()
    with pytest.raises(ValueError, match="database proposal occurrences differ"):
        verify_v34_database_object_replay(plan, graph, payloads)


def test_v34_database_object_replay_rejects_occurrence_materialization_drift() -> None:
    plan = _plan()
    graph, payloads = _database_fixture(plan)
    graph["candidate_occurrences"][0]["candidate_id"] = "candidate-drift"
    with pytest.raises(ValueError, match="database proposal occurrences differ"):
        verify_v34_database_object_replay(plan, graph, payloads)


def test_v34_database_object_replay_rejects_parent_order_drift() -> None:
    plan = _plan()
    graph, payloads = _database_fixture(plan)
    graph["v34_parent_candidates"].reverse()
    with pytest.raises(ValueError, match="parent identity or order drifted"):
        verify_v34_database_object_replay(plan, graph, payloads)


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ("agent_decisions", "AgentDecision set is incomplete"),
        ("agent_decision_tool_call_edges", "evidence edges drifted"),
    ],
)
def test_v34_database_object_replay_rejects_missing_decision_evidence(
    section: str, message: str
) -> None:
    plan = _plan()
    graph, payloads = _database_fixture(plan)
    graph[section].pop()
    with pytest.raises(ValueError, match=message):
        verify_v34_database_object_replay(plan, graph, payloads)


def test_v34_database_object_replay_rejects_holdout_evaluation_drift() -> None:
    plan = _plan()
    graph, payloads = _database_fixture(plan)
    evaluation_link = next(
        item
        for item in graph["evidence_artifacts"]
        if item["role"] == "holdout_endpoint_vector"
    )
    graph["evaluations"] = [
        {
            "tool_call_id": evaluation_link["tool_call_id"],
            "candidate_id": "missing-candidate",
            "metric_name": "missing-metric",
            "numeric_value": 1.0,
            "text_value": None,
            "unit": None,
            "out_of_domain": False,
            "limitations": [],
            "raw": {},
        }
    ]
    with pytest.raises(ValueError, match="database evaluations differ"):
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


def test_v34_database_object_replay_rejects_provider_governance_drift() -> None:
    plan = _plan()
    graph, payloads = _database_fixture(plan)
    governance_link = next(
        item
        for item in graph["evidence_artifacts"]
        if item["role"] == "provider_change_request_ledger"
    )
    artifact = next(
        item for item in graph["artifacts"] if item["id"] == governance_link["artifact_id"]
    )
    payload = payloads.pop(artifact["sha256"])
    payload["formal_run_release_hot_swap_performed"] = True
    artifact["sha256"] = sha256_json(payload)
    payloads[artifact["sha256"]] = payload
    with pytest.raises(ValueError, match="release hot swap"):
        verify_v34_database_object_replay(plan, graph, payloads)


def test_v34_database_object_replay_requires_provider_child_run_lineage() -> None:
    plan = _plan()
    graph, payloads = _database_fixture(plan)
    governance_link = next(
        item
        for item in graph["evidence_artifacts"]
        if item["role"] == "provider_change_request_ledger"
    )
    artifact = next(
        item for item in graph["artifacts"] if item["id"] == governance_link["artifact_id"]
    )
    payload = payloads.pop(artifact["sha256"])
    payload["change_requests"] = [
        {
            "request_id": "pepshot-request-001",
            "provider": "pepshot",
            "owner_task_id": "019fb910-f2dd-7be1-a7e6-bfe381512c25",
            "rejecting_run_id": "11111111-1111-4111-8111-111111111111",
            "change_request_run_id": "22222222-2222-4222-8222-222222222222",
            "rejected_release_identity": (
                "pepshot-34487cf9667a64c3-fe1e5382de8cab09"
            ),
            "trigger_category": "scientific_review_inadequacy",
            "reproducible_input_artifact_sha256": "1" * 64,
            "violated_contract_artifact_sha256": "2" * 64,
            "acceptance_criteria_artifact_sha256": "3" * 64,
            "external_request_receipt_artifact_sha256": "4" * 64,
            "lifecycle_state": "change_request_sent",
            "consumer_adaptation_performed": False,
        }
    ]
    artifact["sha256"] = sha256_json(payload)
    payloads[artifact["sha256"]] = payload
    graph["provider_governance_artifacts"] = [
        {"sha256": value * 64, "content_verified": True} for value in "1234"
    ]
    with pytest.raises(ValueError, match="child-run lineage"):
        verify_v34_database_object_replay(plan, graph, payloads)
    graph["provider_governance_lineage"] = [
        {
            "request_id": "pepshot-request-001",
            "rejecting_run_id": "11111111-1111-4111-8111-111111111111",
            "change_request_run_id": "22222222-2222-4222-8222-222222222222",
            "parentage_verified": True,
        }
    ]
    assert verify_v34_database_object_replay(plan, graph, payloads)["exact_replay"]
