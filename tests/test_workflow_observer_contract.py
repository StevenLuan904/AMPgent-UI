from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pepagent.v38_sequence_first_multitarget import MultiTargetStructureTask
from pepagent.workers.v38_activities import build_v38_structure_artifact_link
from pepagent.workflow_observer_contract import (
    OBSERVER_STAGES,
    ActivityLifecyclePayload,
    KnowledgeCardReadPayload,
    ObserverTransientSnapshot,
    build_candidate_decision_projection,
    build_formal_workflow_topology,
    write_transient_snapshot,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


def _request_template() -> dict[str, object]:
    cells = [
        {
            "ordinal": index,
            "generator_id": f"g{index // 3}",
            "seed": index,
            "requested_proposals": 100,
        }
        for index in range(9)
    ]
    branches = [
        {"target_key": key, "structure_budget": 48, "rosetta_decoys_per_pose": 16}
        for key in ("gyra", "pbp2a")
    ]
    return {
        "execution_contract": {
            "cells": cells,
            "expected_raw_occurrences": 900,
            "required_sequence_metrics": [f"m{index}" for index in range(11)],
        },
        "multitarget_plan_template": {"target_branches": branches},
        "boltz_seeds": [101, 202, 303],
        "knowledge_context_pack_sha256": SHA_A,
        "task_queues": {
            "workflow_and_control": "control",
            "generator": "generator",
            "sequence_metrics": "metrics",
            "structure_boltz": "boltz",
            "structure_rosetta": "rosetta",
        },
        "refinement_provider": {"maximum_rounds": 3},
    }


def test_topology_is_complete_before_any_activity_starts() -> None:
    topology = build_formal_workflow_topology(_request_template())
    assert [
        (item.stage_name, item.display_category) for item in topology.stages
    ] == list(OBSERVER_STAGES)
    assert [item.stage_order for item in topology.stages] == list(range(9))
    expected = {item.stage_name: item.expected_durable_count for item in topology.stages}
    assert expected == {
        "knowledge": 1,
        "generation": 900,
        "sequence_metrics": 9900,
        "admission": 1,
        "refinement": 3,
        "structure_boltz": 576,
        "structure_rosetta": 9216,
        "final_portfolio": 1,
        "replay": 1,
    }


def test_activity_lifecycle_is_typed_and_rejects_free_text_fields() -> None:
    payload = ActivityLifecyclePayload(
        run_id=uuid4(),
        activity_id="activity-1",
        activity_type="persist_v38_sequence_metric",
        logical_stage="sequence_metrics",
        display_category="evaluation",
        attempt=1,
        status="progress",
        completed=3,
        expected=10,
        worker_role="v38-control",
        task_queue="control",
    )
    assert payload.completed == 3
    with pytest.raises(ValidationError):
        ActivityLifecyclePayload.model_validate(
            {**payload.model_dump(mode="json"), "log": "unstructured output"}
        )


def test_knowledge_read_distinguishes_passage_from_card_content() -> None:
    read = KnowledgeCardReadPayload(
        run_id=uuid4(),
        card_key="card-1",
        card_version="release-7",
        content_sha256=SHA_B,
        content_kind="passage_evidence",
        source_uri="provider-task://task/cards/card-1/passages/hash",
        read_at=datetime.now(UTC),
        status="adopted",
    )
    assert read.content_kind == "passage_evidence"


def test_candidate_decision_projection_has_complete_auditable_sets() -> None:
    selected, rejected, deferred = uuid4(), uuid4(), uuid4()
    payload = {
        "policy": {"schema_version": "policy.1"},
        "candidate_evidence_sha256": SHA_A,
        "admission": {
            "decisions": [
                {"candidate_id": str(selected), "reasons": ["pareto"]},
                {"candidate_id": str(rejected), "reasons": ["toxicity"]},
                {"candidate_id": str(deferred), "reasons": ["outside_budget"]},
            ],
            "mature_core_candidate_ids": [str(selected)],
            "exploration_candidate_ids": [],
            "rejected_candidate_ids": [str(rejected)],
        },
    }
    projection = build_candidate_decision_projection(payload)
    assert projection["selected_candidate_ids"] == [str(selected)]
    assert projection["rejected_candidate_ids"] == [str(rejected)]
    assert projection["deferred_candidate_ids"] == [str(deferred)]
    assert projection["input_evidence_sha256"] == SHA_A


def test_structure_coordinate_role_preserves_molstar_join_keys() -> None:
    task = MultiTargetStructureTask(
        target_key="gyra",
        target_id=uuid4(),
        candidate_id=uuid4(),
        parallel_wave=0,
        control_lane="native",
        pocket_sha256=SHA_A,
        boltz_seed=101,
        rosetta_decoys_per_pose=16,
        evidence_namespace="target/gyra",
        ordinal=0,
    )
    role, metadata = build_v38_structure_artifact_link(
        task=task,
        tool="boltz2",
        artifact={"path": "predictions/model_0.cif", "media_type": "chemical/x-cif"},
        index=0,
    )
    assert role == "structure_coordinate"
    assert metadata["target_id"] == str(task.target_id)
    assert metadata["candidate_id"] == str(task.candidate_id)
    assert metadata["control_lane"] == "native"
    assert metadata["boltz_seed"] == 101


def test_transient_snapshot_is_atomic_and_rejects_credentials(tmp_path) -> None:
    snapshot = ObserverTransientSnapshot(
        run_id=uuid4(),
        updated_at=datetime.now(UTC),
        ttl_seconds=60,
        source="test",
        transient={"throughput": 2.5, "eta_seconds": 30},
    )
    path = write_transient_snapshot(snapshot, root=tmp_path)
    assert path.exists()
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(ValidationError):
        ObserverTransientSnapshot(
            run_id=uuid4(),
            updated_at=datetime.now(UTC),
            ttl_seconds=60,
            source="test",
            transient={"access_token": "forbidden"},
        )
