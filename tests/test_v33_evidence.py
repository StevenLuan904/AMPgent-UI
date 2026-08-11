from __future__ import annotations

from copy import deepcopy

import pytest

from pepagent.charge_design import (
    ChargeEditContract,
    ChargeInterventionDose,
    build_charge_counterfactual_cohort,
)
from pepagent.provenance.hashing import sha256_json
from pepagent.v33_evidence import (
    build_v33_charge_persistence_plan,
    recover_v33_transform_identity,
    verify_v33_evidence_graph,
)


def _cohort():
    return build_charge_counterfactual_cohort(
        parents_in_stream_order=[
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "sequence": "AQNSTLQNSTA",
            }
        ],
        doses={
            "one_positive_residue": ChargeInterventionDose(
                name="one_positive_residue", edit_count=1, expected_formal_charge_delta=1
            ),
            "two_positive_residues": ChargeInterventionDose(
                name="two_positive_residues", edit_count=2, expected_formal_charge_delta=2
            ),
        },
        contract=ChargeEditContract(),
        maximum_parent_count=1,
    )


def test_v33_charge_persistence_plan_has_exact_seven_arm_family() -> None:
    plan = build_v33_charge_persistence_plan(_cohort())
    assert len(plan["candidate_records"]) == 7
    assert sum(item["is_baseline_parent"] for item in plan["candidate_records"]) == 1
    assert len({item["sequence_sha256"] for item in plan["candidate_records"]}) == 7
    assert plan == build_v33_charge_persistence_plan(_cohort())


def test_lost_response_recovers_exact_v33_arms_without_duplicate_or_advance() -> None:
    plan = build_v33_charge_persistence_plan(_cohort())
    persisted = [
        {
            "id": item["parent_candidate_id"] if item["is_baseline_parent"] else f"child-{index}",
            "sequence_sha256": item["sequence_sha256"],
            "parent_id": None if item["is_baseline_parent"] else item["parent_candidate_id"],
        }
        for index, item in enumerate(plan["candidate_records"])
    ]
    first = recover_v33_transform_identity(plan, persisted)
    second = recover_v33_transform_identity(plan, deepcopy(persisted))
    assert first == second
    assert len(first) == 7

    with pytest.raises(ValueError, match="incomplete"):
        recover_v33_transform_identity(plan, persisted[:-1])
    with pytest.raises(ValueError, match="duplicate persisted"):
        recover_v33_transform_identity(plan, [*persisted, deepcopy(persisted[-1])])


def _artifact(payload, role, call_id, index):
    digest = sha256_json(payload)
    return (
        {
            "id": f"artifact-{index}",
            "sha256": digest,
            "size_bytes": 1,
            "media_type": "application/json",
            "storage_uri": f"s3://bucket/{digest}",
            "metadata": {},
        },
        {"tool_call_id": call_id, "artifact_id": f"artifact-{index}", "role": role},
        digest,
    )


def _replay_fixture():
    cohort = _cohort()
    plan = build_v33_charge_persistence_plan(cohort)
    transform_id = "call-transform"
    literature_id = "call-literature"
    archive_id = "call-archive"
    decision_id = "decision-1"
    candidates = []
    id_by_logical = {}
    for index, item in enumerate(plan["candidate_records"]):
        candidate_id = (
            item["parent_candidate_id"]
            if item["is_baseline_parent"]
            else f"child-{index}"
        )
        id_by_logical[item["logical_id"]] = candidate_id
        candidates.append(
            {
                "id": candidate_id,
                "sequence": item["sequence"],
                "sequence_sha256": item["sequence_sha256"],
                "generation": 0 if item["is_baseline_parent"] else 1,
                "parent_id": None if item["is_baseline_parent"] else item["parent_candidate_id"],
                "proposal_rank": index,
                "status": "generated",
                "generator_call_id": "call-source" if item["is_baseline_parent"] else transform_id,
                "metadata": {},
            }
        )
    evaluations = [
        {
            "id": f"eval-{index}",
            "candidate_id": id_by_logical[item["logical_id"]],
            "tool_call_id": transform_id,
            "metric_name": item["metric_name"],
            "numeric_value": item["numeric_value"],
            "text_value": None,
            "unit": "descriptor",
            "status": "succeeded",
            "out_of_domain": False,
            "limitations": [],
            "raw": {},
        }
        for index, item in enumerate(plan["descriptor_evaluations"])
    ]
    payloads = {
        "literature_basis_manifest": {"primary_studies": ["study-a"]},
        "submitted_manifest": {"benchmark_id": "amp_charge_search_sufficiency_v33"},
        "charge_counterfactual_cohort": cohort.model_dump(mode="json"),
        "checkpoint_archive_snapshots": {
            "snapshots": [
                {
                    "schema_version": "1.0",
                    "method_version": "v33-search-sufficiency-v1",
                    "seed": 20261201,
                    "family": "membrane",
                    "checkpoint": 50,
                    "previous_checkpoint": 25,
                    "input_candidate_ids": ["child-1", "child-2"],
                    "archive_candidate_ids": ["child-2"],
                    "epsilon_cells": [[0, 0]],
                    "added_candidate_ids": ["child-2"],
                    "removed_candidate_ids": ["child-1"],
                    "added_candidate_reasons": {
                        "child-2": "nondominated_at_checkpoint"
                    },
                    "removed_candidate_dominance_witnesses": {
                        "child-1": ["child-2"]
                    },
                    "new_epsilon_cells": [[0, 0]],
                    "archive_turnover_fraction": 1.0,
                    "new_nondominated_candidate_rate": 0.04,
                    "new_family_local_epsilon_cells_per_candidate": 0.04,
                }
            ]
        },
        "saturation_assessment": {
            "verdict": "not_saturated_within_protocol_and_budget",
            "assessed_seed_count": 1,
            "assessed_family_count": 1,
            "failing_seed_family_checkpoints": [
                "seed=20261201;family=membrane;checkpoint=50"
            ],
            "missing_seed_family_checkpoints": [],
        },
    }
    artifacts, links, by_sha = [], [], {}
    role_call = {
        "literature_basis_manifest": literature_id,
        "submitted_manifest": transform_id,
        "charge_counterfactual_cohort": transform_id,
        "checkpoint_archive_snapshots": archive_id,
        "saturation_assessment": archive_id,
    }
    for index, (role, payload) in enumerate(payloads.items()):
        artifact, link, digest = _artifact(payload, role, role_call[role], index)
        artifacts.append(artifact)
        links.append(link)
        by_sha[digest] = payload
    graph = {
        "candidates": candidates,
        "evaluations": evaluations,
        "tool_calls": [
            {"id": "call-source", "tool_name": "generator"},
            {"id": literature_id, "tool_name": "v33-literature-basis-freezer"},
            {"id": transform_id, "tool_name": "v33-matched-charge-transformer"},
            {"id": archive_id, "tool_name": "v33-checkpoint-archive"},
        ],
        "tool_call_dependencies": [
            {
                "child_tool_call_id": transform_id,
                "parent_tool_call_id": literature_id,
                "relation_type": "uses_literature_basis",
            },
            {
                "child_tool_call_id": archive_id,
                "parent_tool_call_id": transform_id,
                "relation_type": "archives_transformed_candidates",
            },
        ],
        "agent_decisions": [{"id": decision_id, "decision_type": "v33_saturation"}],
        "agent_decision_tool_call_edges": [
            {
                "decision_id": decision_id,
                "tool_call_id": archive_id,
                "direction": "input",
                "relation_type": "observes_complete_archive",
            },
            {
                "decision_id": decision_id,
                "tool_call_id": transform_id,
                "direction": "input",
                "relation_type": "observes_transform_contract",
            },
        ],
        "artifacts": artifacts,
        "evidence_artifacts": links,
    }
    return graph, by_sha


def test_v33_replay_requires_complete_database_object_evidence_graph() -> None:
    graph, payloads = _replay_fixture()
    replay = verify_v33_evidence_graph(graph, payloads)
    assert replay["exact_replay"] is True
    assert replay["archive_snapshot_count"] == 1


def test_v33_replay_fails_on_missing_dependency_or_dominance_witness() -> None:
    graph, payloads = _replay_fixture()
    missing_edge = deepcopy(graph)
    missing_edge["tool_call_dependencies"] = missing_edge["tool_call_dependencies"][1:]
    with pytest.raises(ValueError, match="literature dependency"):
        verify_v33_evidence_graph(missing_edge, payloads)

    missing_witness_graph = deepcopy(graph)
    missing_witness_payloads = deepcopy(payloads)
    archive_artifact = next(
        artifact
        for artifact in missing_witness_graph["artifacts"]
        if any(
            link["artifact_id"] == artifact["id"]
            and link["role"] == "checkpoint_archive_snapshots"
            for link in missing_witness_graph["evidence_artifacts"]
        )
    )
    archive_payload = missing_witness_payloads.pop(archive_artifact["sha256"])
    archive_payload["snapshots"][0]["removed_candidate_dominance_witnesses"] = {}
    archive_artifact["sha256"] = sha256_json(archive_payload)
    missing_witness_payloads[archive_artifact["sha256"]] = archive_payload
    with pytest.raises(ValueError, match="dominance witnesses"):
        verify_v33_evidence_graph(missing_witness_graph, missing_witness_payloads)


def test_v33_replay_rejects_artifact_role_attached_to_wrong_call() -> None:
    graph, payloads = _replay_fixture()
    wrong = deepcopy(graph)
    link = next(
        item for item in wrong["evidence_artifacts"] if item["role"] == "submitted_manifest"
    )
    link["tool_call_id"] = "call-literature"
    with pytest.raises(ValueError, match="linked to wrong call"):
        verify_v33_evidence_graph(wrong, payloads)
