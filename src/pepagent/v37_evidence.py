from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_preregistration import V37Manifest

V37_GLOBAL_STAGES = (
    "knowledge",
    "stage1-shortlist",
    "structure",
    "rosetta",
    "pepshot",
    "final-portfolio",
    "replay",
)


def build_v37_evidence_plan(manifest: V37Manifest) -> dict[str, Any]:
    generator_calls = []
    for engine in manifest.generators["engines"]:
        for seed in engine["seeds"]:
            generator_calls.append(
                {
                    "logical_id": f"v37:generate:{engine['generator_id']}:{seed}",
                    "tool_name": f"v37-generate-{engine['generator_id']}",
                    "generator_id": engine["generator_id"],
                    "seed": seed,
                }
            )
    metric_calls = [
        {
            "logical_id": f"v37:metric:{plugin['name']}",
            "tool_name": "v37-sequence-metric",
            "plugin_name": plugin["name"],
            "metric_names": list(plugin["observation_names"]),
        }
        for plugin in manifest.stage_1_sequence_evaluation["metric_plugins"]
    ]
    global_calls = [
        {"logical_id": f"v37:{stage}", "tool_name": f"v37-{stage}"} for stage in V37_GLOBAL_STAGES
    ]
    generation_ids = [item["logical_id"] for item in generator_calls]
    metric_ids = [item["logical_id"] for item in metric_calls]
    dependencies = [
        *[[parent, child] for parent in generation_ids for child in metric_ids],
        *[[item, "v37:stage1-shortlist"] for item in metric_ids],
        ["v37:knowledge", "v37:stage1-shortlist"],
        ["v37:stage1-shortlist", "v37:structure"],
        ["v37:structure", "v37:rosetta"],
        ["v37:structure", "v37:pepshot"],
        ["v37:rosetta", "v37:final-portfolio"],
        ["v37:pepshot", "v37:final-portfolio"],
        ["v37:final-portfolio", "v37:replay"],
    ]
    result: dict[str, Any] = {
        "schema_version": "1.0",
        "benchmark_id": manifest.benchmark_id,
        "generator_calls": generator_calls,
        "metric_calls": metric_calls,
        "global_calls": global_calls,
        "dependencies": dependencies,
        "required_tool_call_ids": [
            *generation_ids,
            *metric_ids,
            *[item["logical_id"] for item in global_calls],
        ],
        "expected_candidate_count": manifest.generators["expected_candidate_count"],
        "expected_structure_shortlist": manifest.stage_2_structure_confirmation[
            "expected_maximum_candidates"
        ],
    }
    result["plan_sha256"] = sha256_json(result)
    return result


def validate_v37_replay_graph(graph: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    observed_calls = {
        str(item["input_json"]["v37_logical_id"])
        for item in graph.get("tool_calls", [])
        if "v37_logical_id" in item.get("input_json", {})
    }
    expected_calls = set(plan["required_tool_call_ids"])
    if observed_calls != expected_calls:
        raise ValueError("v37 replay ToolCall identity set differs from frozen plan")
    observed_dependencies = {
        (str(item["parent_logical_id"]), str(item["child_logical_id"]))
        for item in graph.get("logical_dependencies", [])
    }
    expected_dependencies = {tuple(item) for item in plan["dependencies"]}
    if observed_dependencies != expected_dependencies:
        raise ValueError("v37 replay dependency graph differs from frozen plan")
    result = {
        "exact_replay": True,
        "tool_call_count": len(observed_calls),
        "dependency_count": len(observed_dependencies),
        "plan_sha256": plan["plan_sha256"],
    }
    result["validation_sha256"] = sha256_json(result)
    return result
