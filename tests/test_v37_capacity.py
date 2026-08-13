from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_capacity import (
    V37CapacityContract,
    build_v37_pipeline_manifest,
    build_v37_pipeline_queue_transition_ledger,
    build_v37_static_capacity_preflight,
    load_v37_capacity_contract,
    validate_v37_capacity_replay_artifacts,
    validate_v37_worker_placement_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "experiments" / "acea_v37_rapid_champion_capacity.yaml"


def _worker_snapshot() -> dict:
    roles = (
        ("v37-control", "pepagent-control-v37"),
        ("v37-generator", "pepagent-generator-v37"),
        ("v37-provider", "pepagent-provider-v37"),
        ("metrics", "pepagent-cpu-metrics"),
        ("boltz2", "pepagent-gpu-boltz2"),
        ("rosetta", "pepagent-cpu-rosetta"),
    )
    return {
        "schema_version": "v37.worker-placement-snapshot.1",
        "captured_at": "2026-08-12T00:00:00Z",
        "active_workflow_count": 0,
        "topology_frozen_for_run": True,
        "placements": [
            {
                "physical_host": "synth" if role == "boltz2" else "local",
                "gpu_index": 6 if role == "boltz2" else None,
                "pid": 2000 + index,
                "role": role,
                "task_queue": queue,
                "poller_identity": f"{2000 + index}@test",
                "poller_last_access_at": "2026-08-12T00:00:00Z",
                "source_revision": "a" * 40,
                "release_sha256": "b" * 64,
                "environment_sha256": "c" * 64,
                "weights_sha256": "d" * 64 if role == "boltz2" else None,
                "ampgent_owned": True,
                "foreign_process_present": False,
            }
            for index, (role, queue) in enumerate(roles)
        ],
    }


def _expected_task_queues() -> dict[str, str]:
    return {
        "workflow_and_control": "pepagent-control-v37",
        "generator": "pepagent-generator-v37",
        "provider": "pepagent-provider-v37",
        "sequence_metrics": "pepagent-cpu-metrics",
        "boltz": "pepagent-gpu-boltz2",
        "rosetta": "pepagent-cpu-rosetta",
    }


def _stage_receipts(stage: str, logical_id: str) -> list[dict]:
    activity_type, task_queue, count = {
        "proposal": ("generate_v37_batch", "pepagent-generator-v37", 1),
        "evaluation": ("evaluate_v37_sequence_metric", "pepagent-cpu-metrics", 5),
        "boltz": ("predict_v37_boltz2_complex", "pepagent-gpu-boltz2", 3),
        "rosetta": ("score_v37_rosetta_complex", "pepagent-cpu-rosetta", 3),
    }[stage]
    return [
        {
            "schema_version": "v37.activity-transition-receipt.1",
            "activity_id": f"{logical_id}:{index}",
            "activity_type": activity_type,
            "attempt": 1,
            "task_queue": task_queue,
            "scheduled_at": "2026-08-12T00:00:00+00:00",
            "started_at": "2026-08-12T00:00:01+00:00",
            "finished_at": "2026-08-12T00:00:02+00:00",
            "schedule_to_start_seconds": 1.0,
        }
        for index in range(count)
    ]


def test_v37_capacity_contract_freezes_rapid_pipeline_without_authorization() -> None:
    contract = load_v37_capacity_contract(CONTRACT_PATH)
    assert contract.pipeline_contract["order"] == [
        "proposal",
        "evaluation",
        "boltz",
        "rosetta",
    ]
    assert contract.resource_capacity["gpu"]["maximum_concurrent_workers"] == 4
    assert contract.resource_capacity["cpu"]["rosetta_activity_slots"] == 16
    assert contract.formal_run.execution_authorized is False
    assert contract.formal_run.submitted is False


def test_v37_pipeline_manifest_is_fifo_and_exactly_chained() -> None:
    proposals = [
        {"proposal_ordinal": ordinal, "occurrence_id": f"occurrence-{ordinal:03d}"}
        for ordinal in range(1, 9)
    ]
    manifest = build_v37_pipeline_manifest(proposals)
    assert [item["proposal_ordinal"] for item in manifest["items"]] == list(range(1, 9))
    assert len(manifest["dependencies"]) == 8 * 3
    first = manifest["items"][0]["stage_logical_ids"]
    assert [first["proposal"], first["evaluation"]] in manifest["dependencies"]
    assert [first["evaluation"], first["boltz"]] in manifest["dependencies"]
    assert [first["boltz"], first["rosetta"]] in manifest["dependencies"]


def test_v37_static_capacity_preflight_never_touches_or_authorizes_hosts() -> None:
    record = build_v37_static_capacity_preflight(contract_path=CONTRACT_PATH)
    assert record["stage_concurrency"] == {
        "proposal": 8,
        "evaluation": 16,
        "boltz": 4,
        "rosetta": 16,
    }
    assert record["host_or_process_observation_performed"] is False
    assert record["remote_process_started_or_stopped"] is False
    assert record["formal_run_authorized"] is False
    assert record["formal_run_submitted"] is False


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("resource_capacity", "gpu", "maximum_concurrent_workers"), 3),
        (("resource_capacity", "cpu", "rosetta_activity_slots"), 8),
        (("pipeline_contract", "order"), ["proposal", "boltz", "evaluation", "rosetta"]),
        (("retry_contract", "maximum_attempts_per_logical_stage_call"), 3),
        (("formal_run", "execution_authorized"), True),
    ],
)
def test_v37_capacity_contract_rejects_drift(path: tuple[str, ...], value: object) -> None:
    payload = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    drifted = deepcopy(payload)
    target = drifted
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        V37CapacityContract.model_validate(drifted)


def test_v37_pipeline_manifest_rejects_noncontiguous_or_duplicate_occurrences() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        build_v37_pipeline_manifest(
            [
                {"proposal_ordinal": 1, "occurrence_id": "a"},
                {"proposal_ordinal": 3, "occurrence_id": "b"},
            ]
        )
    with pytest.raises(ValueError, match="unique"):
        build_v37_pipeline_manifest(
            [
                {"proposal_ordinal": 1, "occurrence_id": "a"},
                {"proposal_ordinal": 2, "occurrence_id": "a"},
            ]
        )


def test_v37_worker_snapshot_requires_every_frozen_role_and_queue() -> None:
    snapshot = _worker_snapshot()
    assert validate_v37_worker_placement_snapshot(snapshot) is snapshot
    snapshot["placements"] = snapshot["placements"][1:]
    with pytest.raises(ValueError, match="task queue coverage"):
        validate_v37_worker_placement_snapshot(snapshot)


def test_v37_worker_snapshot_rejects_nonexact_or_stale_identity() -> None:
    malformed = _worker_snapshot()
    malformed["placements"][0]["release_sha256"] = "x"
    with pytest.raises(ValueError, match="SHA"):
        validate_v37_worker_placement_snapshot(malformed)
    stale = _worker_snapshot()
    with pytest.raises(ValueError, match="stale"):
        validate_v37_worker_placement_snapshot(
            stale,
            reference_time=datetime(2026, 8, 12, 0, 10, tzinfo=UTC),
        )


def test_v37_capacity_replay_rejects_transition_tampering() -> None:
    manifest = build_v37_pipeline_manifest(
        [{"proposal_ordinal": 1, "occurrence_id": "candidate-1"}]
    )
    outcomes = {
        logical_id: {
            "outcome": "succeeded",
            "activity_receipts": _stage_receipts(stage, logical_id),
        }
        for stage, logical_id in manifest["items"][0]["stage_logical_ids"].items()
    }
    ledger = build_v37_pipeline_queue_transition_ledger(
        pipeline_manifest=manifest,
        stage_outcomes=outcomes,
    )
    validate_v37_capacity_replay_artifacts(
        expected_task_queues=_expected_task_queues(),
        expected_source_revision="a" * 40,
        worker_placement_snapshot=_worker_snapshot(),
        pipeline_manifest=manifest,
        queue_transition_ledger=ledger,
    )
    ledger["transitions"][0]["queue_position"] = 99
    ledger["ledger_sha256"] = sha256_json(
        {key: value for key, value in ledger.items() if key != "ledger_sha256"}
    )
    with pytest.raises(ValueError, match="semantics"):
        validate_v37_capacity_replay_artifacts(
            expected_task_queues=_expected_task_queues(),
            expected_source_revision="a" * 40,
            worker_placement_snapshot=_worker_snapshot(),
            pipeline_manifest=manifest,
            queue_transition_ledger=ledger,
        )


def test_capacity_replay_rejects_worker_source_and_queue_drift() -> None:
    manifest = build_v37_pipeline_manifest(
        [{"proposal_ordinal": 1, "occurrence_id": "occ-1"}]
    )
    outcomes = {
        logical_id: {
            "outcome": "succeeded",
            "activity_receipts": _stage_receipts(stage, logical_id),
        }
        for item in manifest["items"]
        for stage, logical_id in item["stage_logical_ids"].items()
    }
    ledger = build_v37_pipeline_queue_transition_ledger(
        pipeline_manifest=manifest,
        stage_outcomes=outcomes,
    )
    with pytest.raises(ValueError, match="source revision drifted"):
        validate_v37_capacity_replay_artifacts(
            expected_task_queues=_expected_task_queues(),
            expected_source_revision="f" * 40,
            worker_placement_snapshot=_worker_snapshot(),
            pipeline_manifest=manifest,
            queue_transition_ledger=ledger,
        )
    wrong_queues = _expected_task_queues()
    wrong_queues["boltz"] = "wrong-boltz-queue"
    with pytest.raises(ValueError, match="task queue .*drifted"):
        validate_v37_capacity_replay_artifacts(
            expected_task_queues=wrong_queues,
            expected_source_revision="a" * 40,
            worker_placement_snapshot=_worker_snapshot(),
            pipeline_manifest=manifest,
            queue_transition_ledger=ledger,
        )
