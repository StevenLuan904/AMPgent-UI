from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from pepagent.v34_capacity import (
    V34CapacityContract,
    build_v34_fair_episode_queue,
    build_v34_static_capacity_preflight,
    load_v34_capacity_contract,
)
from pepagent.v34_evidence import build_v34_evidence_plan
from pepagent.v34_preregistration import load_v34_preregistration

ROOT = Path(__file__).resolve().parents[1]
CAPACITY_PATH = ROOT / "config" / "experiments" / "acea_v34_execution_capacity.yaml"
BENCHMARK_PATH = ROOT / "config" / "benchmarks" / "amp_knowledge_pepshot_ablation_v34.yaml"


def _plan() -> dict[str, object]:
    benchmark = load_v34_preregistration(BENCHMARK_PATH)
    return build_v34_evidence_plan(
        benchmark.parent_cohort["members"],
        order_salt=benchmark.factorial_design["arm_order_salt"],
        provider_governance=benchmark.provider_governance,
    )


def test_v34_capacity_contract_freezes_allowed_resources_and_budget() -> None:
    contract = load_v34_capacity_contract(CAPACITY_PATH)
    assert contract.scope["expected_episode_count"] == 96
    assert contract.scope["maximum_boltz_poses"] == 384
    assert contract.scope["maximum_rosetta_decoys"] == 3072
    assert contract.gpu_capacity["maximum_concurrent_workers"] == 3
    assert contract.cpu_capacity["fixed_concurrent_activity_slots"] == 16
    assert contract.formal_run.execution_authorized is False


def test_v34_capacity_queue_is_parent_balanced_and_arm_blind() -> None:
    queue = build_v34_fair_episode_queue(_plan()["episodes"])
    assert len(queue) == 96
    assert [item["arm_order"] for item in queue[:24]] == [1] * 24
    assert [item["parent_order"] for item in queue[:24]] == list(range(1, 25))
    assert all("arm_identity_sealed_until_reveal" not in item for item in queue)


def test_v34_static_capacity_preflight_cannot_authorize_or_touch_hosts() -> None:
    record = build_v34_static_capacity_preflight(
        contract_path=CAPACITY_PATH,
        episodes=_plan()["episodes"],
    )
    assert record["episode_count"] == 96
    assert record["host_or_process_observation_performed"] is False
    assert record["remote_process_started_or_stopped"] is False
    assert record["formal_run_authorized"] is False
    assert record["formal_run_submitted"] is False


@pytest.mark.parametrize(
    ("section", "key", "value"),
    [
        ("gpu_capacity", "maximum_concurrent_workers", 4),
        ("cpu_capacity", "fixed_concurrent_activity_slots", 8),
        ("fair_scheduler", "maximum_inflight_episodes_per_parent", 2),
        ("retry_contract", "maximum_attempts_per_logical_tool_call", 3),
        ("formal_run", "execution_authorized", True),
    ],
)
def test_v34_capacity_contract_rejects_drift(
    section: str, key: str, value: object
) -> None:
    payload = yaml.safe_load(CAPACITY_PATH.read_text(encoding="utf-8"))
    drifted = deepcopy(payload)
    drifted[section][key] = value
    with pytest.raises(ValueError):
        V34CapacityContract.model_validate(drifted)


def test_v34_capacity_queue_rejects_missing_episode() -> None:
    with pytest.raises(ValueError, match="96 episodes"):
        build_v34_fair_episode_queue(_plan()["episodes"][:-1])

