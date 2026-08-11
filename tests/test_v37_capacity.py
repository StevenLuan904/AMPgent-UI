from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from pepagent.v37_capacity import (
    V37CapacityContract,
    build_v37_pipeline_manifest,
    build_v37_static_capacity_preflight,
    load_v37_capacity_contract,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "experiments" / "acea_v37_rapid_champion_capacity.yaml"


def test_v37_capacity_contract_freezes_rapid_pipeline_without_authorization() -> None:
    contract = load_v37_capacity_contract(CONTRACT_PATH)
    assert contract.pipeline_contract["order"] == [
        "proposal",
        "evaluation",
        "boltz",
        "rosetta",
    ]
    assert contract.resource_capacity["gpu"]["maximum_concurrent_workers"] == 3
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
        "boltz": 3,
        "rosetta": 16,
    }
    assert record["host_or_process_observation_performed"] is False
    assert record["remote_process_started_or_stopped"] is False
    assert record["formal_run_authorized"] is False
    assert record["formal_run_submitted"] is False


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("resource_capacity", "gpu", "maximum_concurrent_workers"), 4),
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
