from pathlib import Path

import pytest

from pepagent.v34_evidence import build_v34_evidence_plan, validate_v34_replay_graph
from pepagent.v34_preregistration import load_v34_preregistration

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_knowledge_pepshot_ablation_v34.yaml"


def _plan() -> dict:
    manifest = load_v34_preregistration(CONFIG)
    return build_v34_evidence_plan(
        manifest.parent_cohort["members"],
        order_salt=manifest.factorial_design["arm_order_salt"],
    )


def _complete_graph(plan: dict) -> dict:
    tools = [{"logical_id": item, "status": "succeeded"} for item in plan["required_tool_call_ids"]]
    dependencies = [
        {"parent_logical_id": parent, "child_logical_id": child}
        for parent, child in plan["required_dependencies"]
    ]
    artifacts = []
    for episode in plan["episodes"]:
        for tool in episode["tool_calls"]:
            artifacts.extend(
                {
                    "tool_call_logical_id": tool["logical_id"],
                    "role": role,
                    "sha256": "a" * 64,
                }
                for role in tool["required_artifact_roles"]
            )
    for tool in plan["global_tool_calls"]:
        artifacts.extend(
            {
                "tool_call_logical_id": tool["logical_id"],
                "role": role,
                "sha256": "b" * 64,
            }
            for role in tool["required_artifact_roles"]
        )
    adjudications = [
        {
            "tool_call_logical_id": episode["blinded_adjudication_tool_id"],
            "locked_before_assignment_reveal": True,
        }
        for episode in plan["episodes"]
    ]
    return {
        "tool_calls": tools,
        "dependencies": dependencies,
        "artifacts": artifacts,
        "adjudications": adjudications,
    }


def test_v34_evidence_plan_covers_every_parent_arm_and_global_analysis() -> None:
    plan = _plan()
    assert plan["episode_count"] == 96
    assert len(plan["required_tool_call_ids"]) == 96 * 8 + 2
    assert len({item["opaque_label"] for item in plan["episodes"]}) == 96
    assert plan["parent_manifest_sha256"] == (
        "f1955476cb761d9ca300a8fed00d9bb847e775ee5f4c1ef51d1346376a4f943e"
    )
    assert len(plan["plan_sha256"]) == 64


def test_v34_complete_replay_graph_passes() -> None:
    plan = _plan()
    validate_v34_replay_graph(plan, _complete_graph(plan))


@pytest.mark.parametrize("section", ["tool_calls", "dependencies", "artifacts", "adjudications"])
def test_v34_replay_fails_closed_on_missing_evidence(section: str) -> None:
    plan = _plan()
    graph = _complete_graph(plan)
    graph[section].pop()
    with pytest.raises(ValueError, match="v34 replay"):
        validate_v34_replay_graph(plan, graph)


def test_v34_replay_fails_if_assignment_is_revealed_before_lock() -> None:
    plan = _plan()
    graph = _complete_graph(plan)
    graph["adjudications"][0]["locked_before_assignment_reveal"] = False
    with pytest.raises(ValueError, match="before adjudication lock"):
        validate_v34_replay_graph(plan, graph)
