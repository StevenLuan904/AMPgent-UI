from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pepagent.db.base import Base
from pepagent.provenance.hashing import sha256_bytes
from pepagent.v36_harness_evolution import (
    SCHEMA_VERSION,
    canonical_json_bytes,
    validate_harness_replay_snapshot,
)


def _id(number: int) -> str:
    return f"00000000-0000-0000-0000-{number:012d}"


def _artifact(payloads: dict[str, bytes], payload: dict) -> str:
    raw = canonical_json_bytes(payload)
    digest = sha256_bytes(raw)
    payloads[digest] = raw
    return digest


def _release(
    release_id: str,
    harness_id: str,
    payloads: dict[str, bytes],
    *,
    rollback: str | None = None,
) -> dict:
    allowed = _artifact(payloads, {"episode_ids": [f"history-{harness_id}"]})
    holdout = _artifact(payloads, {"episode_ids": [f"holdout-{harness_id}"]})
    endpoint = _artifact(
        payloads, {"required_endpoint_families": ["discovery_quality"]}
    )
    footprints = {
        name: _artifact(payloads, {"harness": harness_id, "field": name})
        for name in (
            "config_sha256",
            "prompt_bundle_sha256",
            "tool_manifest_sha256",
            "model_manifest_sha256",
            "environment_manifest_sha256",
            "failure_taxonomy_sha256",
            "budget_contract_sha256",
        )
    }
    return {
        "id": release_id,
        "harness_id": harness_id,
        "scope_id": "acea-v36-fixture",
        "release_status": "registered",
        "change_hypothesis": f"minimal change for {harness_id}",
        "primary_changed_component": "tool_order",
        "source_revision": f"revision-{harness_id}",
        **footprints,
        "history_cutoff_at": "2026-08-01T00:00:00+00:00",
        "allowed_evidence_slice_sha256": allowed,
        "forbidden_holdout_manifest_sha256": holdout,
        "endpoint_contract_sha256": endpoint,
        "rollback_harness_release_id": rollback,
        "metadata": {},
    }


def _fixture() -> tuple[dict, dict[str, bytes]]:
    payloads: dict[str, bytes] = {}
    root_id, champion_id, challenger_id = _id(1), _id(2), _id(3)
    releases = [
        _release(root_id, "harness-root", payloads),
        _release(champion_id, "harness-champion", payloads, rollback=root_id),
        _release(challenger_id, "harness-challenger", payloads, rollback=champion_id),
    ]
    history_sha = _artifact(
        payloads,
        {
            "partitions": {
                "proposal_history": ["p1"],
                "counterfactual_replay": ["r1"],
                "shadow": ["s1"],
                "prospective_holdout": ["h1"],
            }
        },
    )
    endpoint_sha = _artifact(
        payloads, {"required_endpoint_families": ["discovery_quality"]}
    )
    budget_sha = _artifact(payloads, {"candidate_budget": 2, "early_stop": False})
    blinding_sha = _artifact(payloads, {"opaque_labels": ["arm-x", "arm-y"]})
    phases = ["counterfactual_replay", "shadow", "prospective_equal_budget"]
    trials: list[dict] = []
    assignments: list[dict] = []
    outcomes: list[dict] = []
    run_ids: list[str] = []
    tool_ids: list[str] = []
    tool_call_run_ids: dict[str, str] = {}
    parent_trial_id = None
    assignment_number = 100
    for phase_index, phase in enumerate(phases, start=1):
        trial_id = _id(10 + phase_index)
        phase_assignments: list[dict] = []
        for rank, (release_id, harness_id, label) in enumerate(
            (
                (champion_id, "harness-champion", "arm-x"),
                (challenger_id, "harness-challenger", "arm-y"),
            ),
            start=1,
        ):
            assignment_id = _id(assignment_number)
            run_id = _id(assignment_number + 100)
            tool_id = _id(assignment_number + 200)
            assignment_number += 1
            row = {
                "id": assignment_id,
                "trial_id": trial_id,
                "experiment_run_id": run_id,
                "episode_key": f"episode-{phase}",
                "pair_key": f"pair-{phase}",
                "assigned_release_id": release_id,
                "assigned_harness_id": harness_id,
                "opaque_arm_label": label,
                "assignment_rank": rank,
                "random_seed": 20260811,
                "resource_class": "local-cpu-small",
                "controls_formal_action": phase == "shadow" and release_id == champion_id,
                "metadata": {},
            }
            phase_assignments.append(row)
            assignments.append(row)
            run_ids.append(run_id)
            tool_ids.append(tool_id)
            tool_call_run_ids[tool_id] = run_id
            outcomes.append(
                {
                    "id": _id(assignment_number + 300),
                    "assignment_id": assignment_id,
                    "endpoint_family": "discovery_quality",
                    "endpoint_name": "valid_novel_non_dominated_discovery_yield",
                    "tool_call_id": tool_id,
                    "artifact_sha256": None,
                    "numeric_value": float(rank),
                    "text_value": None,
                    "unit": "count",
                    "status": "succeeded",
                    "limitations": ["synthetic_fixture"],
                    "metadata": {},
                }
            )
        assignment_sha = _artifact(
            payloads,
            {
                "assignments": [
                    {
                        key: row[key]
                        for key in (
                            "assignment_rank",
                            "episode_key",
                            "pair_key",
                            "assigned_harness_id",
                            "opaque_arm_label",
                            "experiment_run_id",
                            "random_seed",
                            "resource_class",
                            "controls_formal_action",
                        )
                    }
                    for row in phase_assignments
                ]
            },
        )
        trials.append(
            {
                "id": trial_id,
                "trial_key": f"fixture-{phase}",
                "phase": phase,
                "status": "succeeded",
                "scope_id": "acea-v36-fixture",
                "champion_release_id": champion_id,
                "challenger_release_id": challenger_id,
                "parent_trial_id": parent_trial_id,
                "history_partition_manifest_sha256": history_sha,
                "assignment_manifest_sha256": assignment_sha,
                "blinding_manifest_sha256": blinding_sha,
                "endpoint_contract_sha256": endpoint_sha,
                "budget_contract_sha256": budget_sha,
                "adjudication_run_id": (
                    _id(900) if phase == "prospective_equal_budget" else None
                ),
                "blinded": True,
                "adjudication_locked_at": (
                    "2026-08-11T10:00:00+00:00"
                    if phase == "prospective_equal_budget"
                    else None
                ),
                "unblinded_at": (
                    "2026-08-11T11:00:00+00:00"
                    if phase == "prospective_equal_budget"
                    else None
                ),
                "started_at": "2026-08-11T08:00:00+00:00",
                "finished_at": "2026-08-11T12:00:00+00:00",
                "metadata": {},
            }
        )
        parent_trial_id = trial_id
    decision_payload = {
        "decision": "promote_for_declared_scope",
        "scope_id": "acea-v36-fixture",
        "prospective_trial_id": trials[2]["id"],
        "counterfactual_trial_id": trials[0]["id"],
        "shadow_trial_id": trials[1]["id"],
        "promoted_release_id": challenger_id,
        "rollback_release_id": None,
    }
    decision_artifact = _artifact(payloads, decision_payload)
    artifacts = [
        {
            "id": _id(500 + index),
            "sha256": digest,
            "size_bytes": len(raw),
            "media_type": "application/json",
        }
        for index, (digest, raw) in enumerate(sorted(payloads.items()))
    ]
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "terminal_trial_id": trials[-1]["id"],
        "releases": releases,
        "lineage_edges": [
            {
                "child_release_id": champion_id,
                "parent_release_id": root_id,
                "relation_type": "derived_from",
            },
            {
                "child_release_id": challenger_id,
                "parent_release_id": champion_id,
                "relation_type": "derived_from",
            },
        ],
        "trials": trials,
        "assignments": assignments,
        "outcomes": outcomes,
        "promotion_decision": {
            "id": _id(700),
            "prospective_trial_id": trials[2]["id"],
            "counterfactual_trial_id": trials[0]["id"],
            "shadow_trial_id": trials[1]["id"],
            "agent_decision_id": _id(701),
            "decision": "promote_for_declared_scope",
            "scope_id": "acea-v36-fixture",
            "promoted_release_id": challenger_id,
            "rollback_release_id": None,
            "decision_artifact_sha256": decision_artifact,
            "effective_at": "2026-08-11T12:00:00+00:00",
            "metadata": {},
        },
        "artifacts": artifacts,
        "existing_graph_refs": {
            "experiment_run_ids": sorted(set(run_ids) | {_id(900)}),
            "tool_call_ids": sorted(tool_ids),
            "agent_decision_ids": [_id(701)],
            "tool_call_run_ids": tool_call_run_ids,
            "agent_decision_run_ids": {_id(701): _id(900)},
        },
    }
    return snapshot, payloads


def _replace_artifact(snapshot: dict, payloads: dict[str, bytes], old: str, payload: dict) -> str:
    raw = canonical_json_bytes(payload)
    new = sha256_bytes(raw)
    payloads.pop(old)
    payloads[new] = raw
    for artifact in snapshot["artifacts"]:
        if artifact["sha256"] == old:
            artifact["sha256"] = new
            artifact["size_bytes"] = len(raw)
    return new


def test_v36_typed_models_and_migration_cover_all_lineage_entities() -> None:
    expected = {
        "harness_releases",
        "harness_lineage_edges",
        "harness_trials",
        "harness_assignments",
        "harness_outcomes",
        "harness_promotion_decisions",
    }
    assert expected.issubset(Base.metadata.tables)
    migration = (
        Path(__file__).parents[1]
        / "migrations"
        / "versions"
        / "0010_harness_evolution_lineage.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision = "0009_candidate_occurrences"' in migration
    for table in expected:
        assert f'"{table}"' in migration


def test_v36_valid_typed_snapshot_replays_exactly() -> None:
    snapshot, payloads = _fixture()
    receipt = validate_harness_replay_snapshot(snapshot, payloads)
    assert receipt["exact_replay"] is True
    assert receipt["release_count"] == 3
    assert receipt["trial_count"] == 3
    assert receipt["assignment_count"] == 6
    assert receipt["promotion_decision_present"] is True


def test_v36_replay_rejects_holdout_leakage() -> None:
    snapshot, payloads = _fixture()
    release = snapshot["releases"][0]
    old = release["allowed_evidence_slice_sha256"]
    release["allowed_evidence_slice_sha256"] = _replace_artifact(
        snapshot,
        payloads,
        old,
        {"episode_ids": ["holdout-harness-root"]},
    )
    with pytest.raises(ValueError, match="overlaps forbidden holdout"):
        validate_harness_replay_snapshot(snapshot, payloads)


def test_v36_replay_rejects_lineage_cycle() -> None:
    snapshot, payloads = _fixture()
    snapshot["lineage_edges"].append(
        {
            "child_release_id": _id(1),
            "parent_release_id": _id(3),
            "relation_type": "derived_from",
        }
    )
    with pytest.raises(ValueError, match="cycle"):
        validate_harness_replay_snapshot(snapshot, payloads)


def test_v36_replay_rejects_shadow_challenger_controlling_actions() -> None:
    snapshot, payloads = _fixture()
    shadow = snapshot["trials"][1]
    shadow_assignments = [
        row for row in snapshot["assignments"] if row["trial_id"] == shadow["id"]
    ]
    shadow_assignments[1]["controls_formal_action"] = True
    old = shadow["assignment_manifest_sha256"]
    shadow["assignment_manifest_sha256"] = _replace_artifact(
        snapshot,
        payloads,
        old,
        {"assignments": [{k: v for k, v in row.items() if k in {
            "assignment_rank", "episode_key", "pair_key", "assigned_harness_id",
            "opaque_arm_label", "experiment_run_id", "random_seed", "resource_class",
            "controls_formal_action",
        }} for row in shadow_assignments]},
    )
    with pytest.raises(ValueError, match="only the champion"):
        validate_harness_replay_snapshot(snapshot, payloads)


def test_v36_replay_rejects_manifest_or_graph_detachment() -> None:
    snapshot, payloads = _fixture()
    detached = copy.deepcopy(snapshot)
    detached["existing_graph_refs"]["tool_call_ids"].pop()
    with pytest.raises(ValueError, match="detached from ToolCall"):
        validate_harness_replay_snapshot(detached, payloads)

    drifted = copy.deepcopy(snapshot)
    trial = drifted["trials"][0]
    old = trial["assignment_manifest_sha256"]
    raw = json.loads(payloads[old])
    raw["assignments"][0]["random_seed"] += 1
    trial["assignment_manifest_sha256"] = _replace_artifact(drifted, payloads, old, raw)
    with pytest.raises(ValueError, match="differ from frozen manifest"):
        validate_harness_replay_snapshot(drifted, payloads)


def test_v36_replay_rejects_promotion_before_locked_blind_adjudication() -> None:
    snapshot, payloads = _fixture()
    snapshot["trials"][2]["unblinded_at"] = "2026-08-11T09:00:00+00:00"
    with pytest.raises(ValueError, match="lock before unblinding"):
        validate_harness_replay_snapshot(snapshot, payloads)


def test_v36_replay_rejects_promotion_artifact_or_adjudication_detachment() -> None:
    snapshot, payloads = _fixture()
    promotion = snapshot["promotion_decision"]
    old = promotion["decision_artifact_sha256"]
    promotion["decision_artifact_sha256"] = _replace_artifact(
        snapshot,
        payloads,
        old,
        {"decision": "retain_champion"},
    )
    with pytest.raises(ValueError, match="differs from its immutable artifact"):
        validate_harness_replay_snapshot(snapshot, payloads)

    snapshot, payloads = _fixture()
    snapshot["existing_graph_refs"]["agent_decision_run_ids"][_id(701)] = _id(901)
    with pytest.raises(ValueError, match="detached from adjudication run"):
        validate_harness_replay_snapshot(snapshot, payloads)


def test_v36_replay_rejects_non_finite_outcome() -> None:
    snapshot, payloads = _fixture()
    snapshot["outcomes"][0]["numeric_value"] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        validate_harness_replay_snapshot(snapshot, payloads)
