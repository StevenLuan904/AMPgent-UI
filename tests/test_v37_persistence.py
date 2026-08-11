from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_json, sha256_text
from pepagent.v37_evidence import build_v37_evidence_plan
from pepagent.v37_persistence import (
    build_v37_artifact_contract,
    validate_v37_database_object_replay,
)
from pepagent.v37_preregistration import load_v37_preregistration

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_rapid_champion_generation_v37.yaml"


def _fixture() -> tuple[dict, dict, dict, dict[str, dict]]:
    frozen = load_v37_preregistration(CONFIG)
    plan = build_v37_evidence_plan(frozen)
    manifest = frozen.model_dump(mode="python")
    manifest["generators"]["raw_proposals_per_generator_seed"] = 2
    manifest["generators"]["evaluated_valid_unique_per_generator_seed"] = 1
    manifest["stage_2_structure_confirmation"]["poses_per_candidate"] = 2
    manifest["stage_2_structure_confirmation"]["rosetta_decoys_per_pose"] = 2
    manifest["final_portfolio"]["total_quota"] = 1

    calls = []
    call_ids = {}
    artifacts = []
    links = []
    payloads: dict[str, dict] = {}
    candidates = []
    evaluations = []
    events = []
    contract = build_v37_artifact_contract(plan)
    all_calls = [
        *plan["generator_calls"],
        *plan["metric_calls"],
        *plan["global_calls"],
    ]
    for index, item in enumerate(all_calls, start=1):
        logical_id = item["logical_id"]
        call_id = f"call-{index}"
        call_ids[logical_id] = call_id
        calls.append(
            {
                "id": call_id,
                "status": "succeeded",
                "input_json": {"v37_logical_id": logical_id},
            }
        )

    for index, generator in enumerate(plan["generator_calls"], start=1):
        logical_id = generator["logical_id"]
        candidate_id = f"candidate-{index:02d}"
        sequence = f"KRW{'K' * index}"
        sequence_sha = sha256_text(sequence)
        candidates.append(
            {
                "id": candidate_id,
                "sequence_sha256": sequence_sha,
                "generator_id": generator["generator_id"],
                "seed": generator["seed"],
                "raw_rank": 1,
            }
        )
        occurrences = [
            {
                "v37_logical_id": logical_id,
                "raw_rank": 1,
                "sequence": sequence,
                "sequence_sha256": sequence_sha,
                "valid": True,
                "duplicate": False,
                "retained": True,
                "candidate_id": candidate_id,
                "reason": None,
            },
            {
                "v37_logical_id": logical_id,
                "raw_rank": 2,
                "sequence": None,
                "sequence_sha256": None,
                "valid": False,
                "duplicate": False,
                "retained": False,
                "candidate_id": None,
                "reason": "invalid_sequence",
            },
        ]
        events.extend(
            {"event_type": "v37.proposal_occurrence", "payload_json": row}
            for row in occurrences
        )
        payloads[(logical_id, "proposal_occurrences")] = {
            "schema_version": "1.0",
            "occurrences": occurrences,
        }

    for metric in plan["metric_calls"]:
        logical_id = metric["logical_id"]
        rows = []
        for candidate in sorted(candidates, key=lambda row: row["id"]):
            row = {
                "candidate_id": candidate["id"],
                "metric_name": metric["metric_name"],
                "numeric_value": 1.0,
                "text_value": None,
                "unit": None,
                "out_of_domain": False,
                "limitations": [],
                "raw": {"source": "synthetic-test"},
            }
            rows.append(row)
            evaluations.append({"tool_call_id": call_ids[logical_id], **row})
        payloads[(logical_id, "evaluation_vector")] = {"evaluations": rows}

    shortlist_ids = [candidate["id"] for candidate in candidates[:2]]
    payloads[("v37:stage1-shortlist", "shortlist_manifest")] = {
        "candidate_ids": shortlist_ids
    }
    poses = []
    for candidate_id in shortlist_ids:
        for pose_rank in range(1, 3):
            poses.append(
                {
                    "candidate_id": candidate_id,
                    "pose_id": f"{candidate_id}-pose-{pose_rank}",
                    "boltz_seed": 20270380 + pose_rank,
                    "structure_sha256": "a" * 64,
                    "coordinate_audit_sha256": "b" * 64,
                }
            )
    payloads[("v37:structure", "pose_manifest")] = {"poses": poses}
    decoys = []
    for pose in poses:
        for decoy_rank in range(1, 3):
            decoys.append(
                {
                    "pose_id": pose["pose_id"],
                    "decoy_id": f"{pose['pose_id']}-decoy-{decoy_rank}",
                    "interface_delta_g_reu": -float(decoy_rank),
                    "input_sha256": "c" * 64,
                    "output_sha256": "d" * 64,
                    "score_terms_sha256": "e" * 64,
                }
            )
    payloads[("v37:rosetta", "decoy_manifest")] = {"decoys": decoys}
    payloads[("v37:pepshot", "pepshot_evidence")] = {
        "reviews": [
            {
                "candidate_id": candidate_id,
                "pose_id": f"{candidate_id}-pose-1",
                "request_sha256": "1" * 64,
                "bundle_sha256": "2" * 64,
                "image_manifest_sha256": "3" * 64,
                "read_order_sha256": "4" * 64,
                "review_sha256": "5" * 64,
                "validation_sha256": "6" * 64,
                "status": "reviewed",
            }
            for candidate_id in shortlist_ids
        ]
    }
    payloads[("v37:final-portfolio", "final_portfolio")] = {
        "candidate_ids": shortlist_ids[:1]
    }
    payloads[("v37:knowledge", "knowledge_evidence")] = {
        "schema_version": "1.0",
        "query_sha256": "1" * 64,
        "query_pack_sha256": "2" * 64,
        "trace_sha256": "3" * 64,
        "policy_sha256": "4" * 64,
        "cards": [
            {
                "card_id": "card-1",
                "revision": "revision-1",
                "passage_manifest_sha256": "5" * 64,
            }
        ],
        "adoption_edges": [
            {
                "evidence_id": "card-1:passage-1",
                "disposition": "used",
                "reason": "applicable to frozen target context",
            }
        ],
    }

    for stage in plan["global_calls"]:
        events.append(
            {
                "event_type": "v37.stage_stopped",
                "payload_json": {
                    "v37_logical_id": stage["logical_id"],
                    "stop_reason": "completed_frozen_budget",
                },
            }
        )

    for logical_id, roles in contract.items():
        payloads[(logical_id, "attempt_ledger")] = {
            "schema_version": "1.0",
            "v37_logical_id": logical_id,
            "attempts": [{"attempt": 1, "status": "succeeded"}],
        }
        payloads[(logical_id, "failure_ledger")] = {
            "schema_version": "1.0",
            "v37_logical_id": logical_id,
            "failures": [],
        }
        for role in roles:
            payload = payloads.get(
                (logical_id, role), {"logical_id": logical_id, "role": role}
            )
            digest = sha256_json(payload)
            artifact_id = f"artifact-{len(artifacts) + 1}"
            artifacts.append({"id": artifact_id, "sha256": digest})
            links.append(
                {
                    "tool_call_id": call_ids[logical_id],
                    "artifact_id": artifact_id,
                    "role": role,
                }
            )
            payloads[digest] = payload

    decisions = []
    decision_edges = []
    for index, logical_id in enumerate(
        (
            "v37:knowledge",
            "v37:stage1-shortlist",
            "v37:pepshot",
            "v37:final-portfolio",
            "v37:replay",
        ),
        start=1,
    ):
        decision_id = f"decision-{index}"
        decisions.append(
            {
                "id": decision_id,
                "decision_type": "v37_stage_decision",
                "status": "succeeded",
                "structured_json": {"v37_logical_id": logical_id},
            }
        )
        decision_edges.extend(
            [
                {
                    "decision_id": decision_id,
                    "tool_call_id": call_ids[plan["generator_calls"][0]["logical_id"]],
                    "direction": "input",
                    "relation_type": "observes_v37_stage_evidence",
                },
                {
                    "decision_id": decision_id,
                    "tool_call_id": call_ids[logical_id],
                    "direction": "output",
                    "relation_type": "materializes_v37_stage_decision",
                },
            ]
        )

    graph = {
        "tool_calls": calls,
        "tool_call_dependencies": [
            {
                "parent_tool_call_id": call_ids[parent],
                "child_tool_call_id": call_ids[child],
            }
            for parent, child in plan["dependencies"]
        ],
        "artifacts": artifacts,
        "evidence_artifacts": links,
        "candidates": candidates,
        "evaluations": evaluations,
        "lifecycle_events": events,
        "agent_decisions": decisions,
        "agent_decision_tool_call_edges": decision_edges,
    }
    return manifest, plan, graph, {
        key: value for key, value in payloads.items() if isinstance(key, str)
    }


def _validate(fixture: tuple[dict, dict, dict, dict[str, dict]]) -> dict:
    manifest, plan, graph, payloads = fixture
    return validate_v37_database_object_replay(
        manifest=manifest,
        plan=plan,
        graph=graph,
        artifact_payloads_by_sha256=payloads,
    )


def _pop_role_item(graph: dict, payloads: dict[str, dict], role: str, key: str) -> None:
    artifact = next(
        artifact
        for artifact in graph["artifacts"]
        for link in graph["evidence_artifacts"]
        if link["artifact_id"] == artifact["id"] and link["role"] == role
    )
    old_sha = artifact["sha256"]
    payload = payloads.pop(old_sha)
    payload[key].pop()
    new_sha = sha256_json(payload)
    artifact["sha256"] = new_sha
    payloads[new_sha] = payload


def _mutate_role_payload(graph: dict, payloads: dict[str, dict], role: str, mutation) -> None:
    artifact = next(
        artifact
        for artifact in graph["artifacts"]
        for link in graph["evidence_artifacts"]
        if link["artifact_id"] == artifact["id"] and link["role"] == role
    )
    payload = payloads.pop(artifact["sha256"])
    mutation(payload)
    artifact["sha256"] = sha256_json(payload)
    payloads[artifact["sha256"]] = payload


def test_v37_replay_requires_complete_typed_database_object_graph() -> None:
    result = _validate(_fixture())
    assert result["exact_replay"] is True
    assert result["candidate_count"] == 9
    assert result["raw_proposal_occurrence_count"] == 18
    assert result["pose_count"] == 4
    assert result["rosetta_decoy_count"] == 8
    assert result["agent_decision_count"] == 5


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (
            lambda graph, _payloads: graph["candidates"].clear(),
            "Candidate evidence",
        ),
        (
            lambda graph, _payloads: graph["tool_call_dependencies"].pop(),
            "dependency graph",
        ),
        (
            lambda graph, payloads: _pop_role_item(
                graph, payloads, "pose_manifest", "poses"
            ),
            "pose coverage",
        ),
        (
            lambda graph, payloads: _pop_role_item(
                graph, payloads, "decoy_manifest", "decoys"
            ),
            "decoy coverage",
        ),
        (
            lambda graph, _payloads: graph["agent_decisions"].pop(),
            "AgentDecision set",
        ),
        (
            lambda graph, _payloads: graph["lifecycle_events"].pop(),
            "stop/failure evidence",
        ),
    ],
)
def test_v37_replay_fails_closed_on_missing_evidence(mutation, match: str) -> None:
    manifest, plan, graph, payloads = _fixture()
    mutation(graph, payloads)
    with pytest.raises(ValueError, match=match):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_fails_closed_on_corrupt_object_payload() -> None:
    manifest, plan, graph, payloads = _fixture()
    payloads[next(iter(payloads))] = {"corrupt": True}
    with pytest.raises(ValueError, match="missing or corrupt"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


def test_v37_replay_fails_closed_on_missing_pepshot_review() -> None:
    manifest, plan, graph, payloads = _fixture()
    _pop_role_item(graph, payloads, "pepshot_evidence", "reviews")
    with pytest.raises(ValueError, match="PepShot candidate review coverage"):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )


@pytest.mark.parametrize(
    ("role", "mutation", "match"),
    [
        (
            "attempt_ledger",
            lambda payload: payload["attempts"].clear(),
            "attempt/failure ledger schema",
        ),
        (
            "knowledge_evidence",
            lambda payload: payload["cards"].clear(),
            "knowledge query/trace/card evidence",
        ),
        (
            "pepshot_evidence",
            lambda payload: payload["reviews"][0].update({"review_sha256": "bad"}),
            "PepShot review hash chain",
        ),
    ],
)
def test_v37_replay_rejects_semantically_empty_artifacts(
    role: str, mutation, match: str
) -> None:
    manifest, plan, graph, payloads = _fixture()
    _mutate_role_payload(graph, payloads, role, mutation)
    with pytest.raises(ValueError, match=match):
        validate_v37_database_object_replay(
            manifest=manifest,
            plan=plan,
            graph=graph,
            artifact_payloads_by_sha256=payloads,
        )
