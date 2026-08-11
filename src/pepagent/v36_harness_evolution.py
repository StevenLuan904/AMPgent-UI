from __future__ import annotations

import json
import math
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pepagent.db.models import (
    AgentDecision,
    Artifact,
    ExperimentRun,
    HarnessAssignment,
    HarnessLineageEdge,
    HarnessOutcome,
    HarnessPromotionDecision,
    HarnessRelease,
    HarnessTrial,
    ToolCall,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.provenance.hashing import sha256_bytes, sha256_json

SCHEMA_VERSION = "v36.harness-evolution-replay.1"
PHASE_ORDER = {
    "counterfactual_replay": 0,
    "shadow": 1,
    "prospective_equal_budget": 2,
}
PROMOTION_DECISIONS = {
    "promote_for_declared_scope",
    "retain_as_context_specific_specialist",
    "retain_champion",
    "reject_challenger",
    "rollback_to_registered_ancestor",
}
SHA_FIELDS = (
    "config_sha256",
    "prompt_bundle_sha256",
    "tool_manifest_sha256",
    "model_manifest_sha256",
    "environment_manifest_sha256",
    "failure_taxonomy_sha256",
    "budget_contract_sha256",
)


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _require_sha256(value: str, field: str) -> None:
    if not _is_sha256(value):
        raise ValueError(f"{field} must be a lowercase SHA-256")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _uuid(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def _json_artifact(payloads: dict[str, bytes], digest: str, role: str) -> Any:
    _require_sha256(digest, role)
    payload = payloads.get(digest)
    if payload is None:
        raise ValueError(f"missing content-addressed artifact bytes: {role}")
    if sha256_bytes(payload) != digest:
        raise ValueError(f"artifact checksum mismatch: {role}")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise ValueError(f"artifact is not valid JSON: {role}") from error


def _require_artifact_bytes(payloads: dict[str, bytes], digest: str, role: str) -> None:
    _require_sha256(digest, role)
    payload = payloads.get(digest)
    if payload is None:
        raise ValueError(f"missing content-addressed artifact bytes: {role}")
    if sha256_bytes(payload) != digest:
        raise ValueError(f"artifact checksum mismatch: {role}")


def _lineage_ancestors(
    release_id: str, edges: list[dict[str, Any]]
) -> set[str]:
    parents: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        child = edge["child_release_id"]
        parent = edge["parent_release_id"]
        if child == parent:
            raise ValueError("harness lineage cannot contain a self edge")
        parents[child].add(parent)
    ancestors: set[str] = set()
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError("harness lineage contains a cycle")
        if node in visited:
            return
        visiting.add(node)
        for parent in parents.get(node, set()):
            ancestors.add(parent)
            visit(parent)
        visiting.remove(node)
        visited.add(node)

    visit(release_id)
    return ancestors


def _validate_release_graph(snapshot: dict[str, Any], payloads: dict[str, bytes]) -> None:
    releases = snapshot.get("releases", [])
    edges = snapshot.get("lineage_edges", [])
    release_ids = [row["id"] for row in releases]
    harness_ids = [row["harness_id"] for row in releases]
    if len(release_ids) != len(set(release_ids)) or len(harness_ids) != len(set(harness_ids)):
        raise ValueError("harness release identities must be unique")
    known = set(release_ids)
    for release in releases:
        for field in SHA_FIELDS:
            _require_artifact_bytes(payloads, release[field], f"release.{field}")
        allowed = _json_artifact(
            payloads,
            release["allowed_evidence_slice_sha256"],
            "allowed_evidence_slice",
        )
        holdout = _json_artifact(
            payloads,
            release["forbidden_holdout_manifest_sha256"],
            "forbidden_holdout_manifest",
        )
        _json_artifact(
            payloads,
            release["endpoint_contract_sha256"],
            "release_endpoint_contract",
        )
        allowed_ids = set(allowed.get("episode_ids", []))
        holdout_ids = set(holdout.get("episode_ids", []))
        if allowed_ids & holdout_ids:
            raise ValueError("allowed evidence slice overlaps forbidden holdout")
    for edge in edges:
        if edge["child_release_id"] not in known or edge["parent_release_id"] not in known:
            raise ValueError("harness lineage edge references an unknown release")
    for release_id in known:
        _lineage_ancestors(release_id, edges)
    for release in releases:
        rollback = release.get("rollback_harness_release_id")
        if rollback is not None and rollback not in _lineage_ancestors(release["id"], edges):
            raise ValueError("rollback target must be a registered harness ancestor")


def _validate_history_partition(payload: Any) -> None:
    required = {
        "proposal_history",
        "counterfactual_replay",
        "shadow",
        "prospective_holdout",
    }
    partitions = payload.get("partitions", {})
    if set(partitions) != required:
        raise ValueError("history partition manifest must contain exactly four frozen partitions")
    observed: set[str] = set()
    for name in required:
        values = partitions[name]
        if len(values) != len(set(values)):
            raise ValueError(f"history partition contains duplicate episodes: {name}")
        overlap = observed & set(values)
        if overlap:
            raise ValueError("an episode crosses frozen history partitions")
        observed.update(values)


def _assignment_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "assignment_rank": row["assignment_rank"],
        "episode_key": row["episode_key"],
        "pair_key": row["pair_key"],
        "assigned_harness_id": row["assigned_harness_id"],
        "opaque_arm_label": row["opaque_arm_label"],
        "experiment_run_id": row["experiment_run_id"],
        "random_seed": row.get("random_seed"),
        "resource_class": row["resource_class"],
        "controls_formal_action": row["controls_formal_action"],
    }


def _validate_trial_chain(snapshot: dict[str, Any], payloads: dict[str, bytes]) -> None:
    trials = snapshot.get("trials", [])
    if not trials:
        raise ValueError("harness replay requires at least one trial")
    trials_by_id = {row["id"]: row for row in trials}
    if len(trials_by_id) != len(trials):
        raise ValueError("harness trial identities must be unique")
    terminal_id = snapshot.get("terminal_trial_id")
    if terminal_id not in trials_by_id:
        raise ValueError("terminal harness trial is missing")
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor = trials_by_id[terminal_id]
    while cursor is not None:
        if cursor["id"] in seen:
            raise ValueError("harness trial lineage contains a cycle")
        seen.add(cursor["id"])
        chain.append(cursor)
        parent_id = cursor.get("parent_trial_id")
        if parent_id is None:
            cursor = None
        elif parent_id not in trials_by_id:
            raise ValueError("harness trial parent is missing from replay")
        else:
            cursor = trials_by_id[parent_id]
    chain.reverse()
    phase_indices = [PHASE_ORDER.get(row["phase"], -1) for row in chain]
    if phase_indices != list(range(len(chain))):
        raise ValueError("harness trial chain must advance replay -> shadow -> prospective")
    champion = chain[0]["champion_release_id"]
    challenger = chain[0]["challenger_release_id"]
    scope = chain[0]["scope_id"]
    for trial in chain:
        if trial["champion_release_id"] not in {
            release["id"] for release in snapshot["releases"]
        } or trial["challenger_release_id"] not in {
            release["id"] for release in snapshot["releases"]
        }:
            raise ValueError("harness trial references an unknown release")
        if (
            trial["champion_release_id"] != champion
            or trial["challenger_release_id"] != challenger
            or trial["scope_id"] != scope
        ):
            raise ValueError("harness trial chain changed releases or scope")
        history = _json_artifact(
            payloads,
            trial["history_partition_manifest_sha256"],
            "history_partition_manifest",
        )
        _validate_history_partition(history)
        blinding = _json_artifact(
            payloads, trial["blinding_manifest_sha256"], "blinding_manifest"
        )
        _json_artifact(payloads, trial["budget_contract_sha256"], "trial_budget_contract")
        endpoint_contract = _json_artifact(
            payloads, trial["endpoint_contract_sha256"], "trial_endpoint_contract"
        )
        assignment_manifest = _json_artifact(
            payloads,
            trial["assignment_manifest_sha256"],
            "assignment_manifest",
        )
        assignments = sorted(
            [row for row in snapshot.get("assignments", []) if row["trial_id"] == trial["id"]],
            key=lambda row: row["assignment_rank"],
        )
        if [row["assignment_rank"] for row in assignments] != list(
            range(1, len(assignments) + 1)
        ):
            raise ValueError("harness assignment ranks must be contiguous and one-based")
        projected = [_assignment_projection(row) for row in assignments]
        if assignment_manifest.get("assignments") != projected:
            raise ValueError("database harness assignments differ from frozen manifest")
        labels = [row["opaque_arm_label"] for row in assignments]
        if blinding.get("opaque_labels") != labels or len(labels) != len(set(labels)):
            raise ValueError("harness assignments differ from frozen blinding manifest")
        if trial["phase"] == "prospective_equal_budget" and trial.get("blinded") is not True:
            raise ValueError("prospective harness trial must remain blinded until adjudication")
        by_pair: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in assignments:
            by_pair[row["pair_key"]].append(row)
        if not by_pair:
            raise ValueError("harness trial has no paired assignments")
        for pair in by_pair.values():
            if len(pair) != 2 or {row["assigned_release_id"] for row in pair} != {
                champion,
                challenger,
            }:
                raise ValueError("each harness pair must contain champion and challenger exactly")
            if len({row["episode_key"] for row in pair}) != 1:
                raise ValueError("paired harness assignments must use the same episode")
            if len({row.get("random_seed") for row in pair}) != 1:
                raise ValueError("paired harness assignments must use the same seed")
            if len({row["resource_class"] for row in pair}) != 1:
                raise ValueError("paired harness assignments must use the same resource class")
            controls = {
                row["assigned_release_id"]: row["controls_formal_action"] for row in pair
            }
            if trial["phase"] == "shadow":
                if controls != {champion: True, challenger: False}:
                    raise ValueError("only the champion may control formal actions during shadow")
            elif any(controls.values()):
                raise ValueError("replay and prospective assignments cannot control formal actions")
        if trial["status"] == "succeeded":
            outcomes = snapshot.get("outcomes", [])
            required_families = set(endpoint_contract.get("required_endpoint_families", []))
            if not required_families:
                raise ValueError("succeeded harness trial lacks required endpoint families")
            for assignment in assignments:
                assignment_outcomes = [
                    row for row in outcomes if row["assignment_id"] == assignment["id"]
                ]
                observed_families = {row["endpoint_family"] for row in assignment_outcomes}
                if not required_families.issubset(observed_families):
                    raise ValueError(
                        "succeeded harness assignment lacks required endpoint families"
                    )
                for outcome in assignment_outcomes:
                    if outcome.get("status") != "succeeded":
                        raise ValueError("succeeded harness trial contains a failed outcome")
                    value = outcome.get("numeric_value")
                    if value is not None and not math.isfinite(value):
                        raise ValueError("harness outcome contains a non-finite value")


def _validate_promotion(snapshot: dict[str, Any], payloads: dict[str, bytes]) -> None:
    promotion = snapshot.get("promotion_decision")
    if promotion is None:
        return
    if promotion["decision"] not in PROMOTION_DECISIONS:
        raise ValueError("unknown harness promotion decision")
    decision_artifact = _json_artifact(
        payloads,
        promotion["decision_artifact_sha256"],
        "promotion_decision_artifact",
    )
    decision_projection = {
        key: promotion.get(key)
        for key in (
            "decision",
            "scope_id",
            "prospective_trial_id",
            "counterfactual_trial_id",
            "shadow_trial_id",
            "promoted_release_id",
            "rollback_release_id",
        )
    }
    if decision_artifact != decision_projection:
        raise ValueError("promotion decision differs from its immutable artifact")
    trials = {row["id"]: row for row in snapshot["trials"]}
    counterfactual = trials.get(promotion["counterfactual_trial_id"])
    shadow = trials.get(promotion["shadow_trial_id"])
    prospective = trials.get(promotion["prospective_trial_id"])
    if not counterfactual or not shadow or not prospective:
        raise ValueError("promotion lacks one or more required gate trials")
    if (
        counterfactual["phase"] != "counterfactual_replay"
        or shadow["phase"] != "shadow"
        or prospective["phase"] != "prospective_equal_budget"
        or shadow.get("parent_trial_id") != counterfactual["id"]
        or prospective.get("parent_trial_id") != shadow["id"]
    ):
        raise ValueError("promotion trial chain does not prove all three comparison gates")
    if {counterfactual["status"], shadow["status"], prospective["status"]} != {"succeeded"}:
        raise ValueError("promotion requires all comparison gates to succeed")
    locked_text = prospective.get("adjudication_locked_at")
    unblinded_text = prospective.get("unblinded_at")
    if locked_text is None or unblinded_text is None:
        raise ValueError("prospective adjudication must lock before unblinding")
    locked = datetime.fromisoformat(locked_text)
    unblinded = datetime.fromisoformat(unblinded_text)
    if locked > unblinded:
        raise ValueError("prospective adjudication must lock before unblinding")
    if promotion["scope_id"] != prospective["scope_id"]:
        raise ValueError("promotion scope differs from prospective trial scope")
    if prospective.get("adjudication_run_id") is None:
        raise ValueError("promotion requires a typed independent adjudication run")
    challenger = prospective["challenger_release_id"]
    if promotion["decision"] == "promote_for_declared_scope":
        if promotion.get("promoted_release_id") != challenger:
            raise ValueError("scoped promotion must name the tested challenger")
        if promotion.get("rollback_release_id") is not None:
            raise ValueError("promotion cannot simultaneously be a rollback")
    elif promotion["decision"] == "rollback_to_registered_ancestor":
        rollback = promotion.get("rollback_release_id")
        edges = snapshot["lineage_edges"]
        champion = prospective["champion_release_id"]
        if rollback not in _lineage_ancestors(champion, edges):
            raise ValueError("rollback decision must name an ancestor of the champion")
        if promotion.get("promoted_release_id") is not None:
            raise ValueError("rollback cannot simultaneously promote a release")
    elif promotion.get("promoted_release_id") is not None:
        raise ValueError("non-promotion decisions cannot name a promoted release")
    elif promotion.get("rollback_release_id") is not None:
        raise ValueError("non-rollback decisions cannot name a rollback release")
    effective_text = promotion.get("effective_at")
    if effective_text is not None and datetime.fromisoformat(effective_text) < unblinded:
        raise ValueError("promotion cannot take effect before unblinding")


def validate_harness_replay_snapshot(
    snapshot: dict[str, Any], artifact_payloads: dict[str, bytes]
) -> dict[str, Any]:
    """Fail closed on a database/object-store harness replay snapshot."""
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unexpected harness replay schema version")
    _validate_release_graph(snapshot, artifact_payloads)
    _validate_trial_chain(snapshot, artifact_payloads)
    _validate_promotion(snapshot, artifact_payloads)
    run_ids = set(snapshot.get("existing_graph_refs", {}).get("experiment_run_ids", []))
    tool_call_ids = set(snapshot.get("existing_graph_refs", {}).get("tool_call_ids", []))
    agent_decision_ids = set(
        snapshot.get("existing_graph_refs", {}).get("agent_decision_ids", [])
    )
    tool_call_run_ids = snapshot.get("existing_graph_refs", {}).get(
        "tool_call_run_ids", {}
    )
    agent_decision_run_ids = snapshot.get("existing_graph_refs", {}).get(
        "agent_decision_run_ids", {}
    )
    assignments_by_id = {
        row["id"]: row for row in snapshot.get("assignments", [])
    }
    for assignment in snapshot.get("assignments", []):
        if assignment["experiment_run_id"] not in run_ids:
            raise ValueError("harness assignment is detached from ExperimentRun evidence")
    for outcome in snapshot.get("outcomes", []):
        if outcome["tool_call_id"] not in tool_call_ids:
            raise ValueError("harness outcome is detached from ToolCall evidence")
        assignment = assignments_by_id.get(outcome["assignment_id"])
        if assignment is None:
            raise ValueError("harness outcome is detached from its assignment")
        if tool_call_run_ids.get(outcome["tool_call_id"]) != assignment["experiment_run_id"]:
            raise ValueError("harness outcome ToolCall is detached from assignment run")
    promotion = snapshot.get("promotion_decision")
    if promotion is not None and promotion["agent_decision_id"] not in agent_decision_ids:
        raise ValueError("harness promotion is detached from AgentDecision evidence")
    if promotion is not None:
        prospective = next(
            row
            for row in snapshot["trials"]
            if row["id"] == promotion["prospective_trial_id"]
        )
        if prospective.get("adjudication_run_id") not in run_ids:
            raise ValueError("harness adjudication is detached from ExperimentRun evidence")
        if (
            agent_decision_run_ids.get(promotion["agent_decision_id"])
            != prospective["adjudication_run_id"]
        ):
            raise ValueError("promotion AgentDecision is detached from adjudication run")
    artifact_shas = {row["sha256"] for row in snapshot.get("artifacts", [])}
    if set(artifact_payloads) != artifact_shas:
        raise ValueError("replay artifact byte set differs from typed artifact references")
    for artifact in snapshot.get("artifacts", []):
        payload = artifact_payloads[artifact["sha256"]]
        if sha256_bytes(payload) != artifact["sha256"] or len(payload) != artifact["size_bytes"]:
            raise ValueError("replay artifact identity or size drifted")
    replay_body = {
        key: value
        for key, value in snapshot.items()
        if key not in {"replay_sha256", "exact_replay"}
    }
    replay_sha = sha256_json(replay_body)
    claimed = snapshot.get("replay_sha256")
    if claimed is not None and claimed != replay_sha:
        raise ValueError("harness replay SHA drifted")
    return {
        "schema_version": SCHEMA_VERSION,
        "exact_replay": True,
        "replay_sha256": replay_sha,
        "release_count": len(snapshot["releases"]),
        "trial_count": len(snapshot["trials"]),
        "assignment_count": len(snapshot.get("assignments", [])),
        "outcome_count": len(snapshot.get("outcomes", [])),
        "promotion_decision_present": promotion is not None,
    }


def _release_row(row: HarnessRelease, artifacts: dict[uuid.UUID, Artifact]) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "harness_id": row.harness_id,
        "scope_id": row.scope_id,
        "release_status": row.release_status,
        "change_hypothesis": row.change_hypothesis,
        "primary_changed_component": row.primary_changed_component,
        "source_revision": row.source_revision,
        **{field: getattr(row, field) for field in SHA_FIELDS},
        "history_cutoff_at": _iso(row.history_cutoff_at),
        "allowed_evidence_slice_sha256": artifacts[
            row.allowed_evidence_slice_artifact_id
        ].sha256,
        "forbidden_holdout_manifest_sha256": artifacts[
            row.forbidden_holdout_manifest_artifact_id
        ].sha256,
        "endpoint_contract_sha256": artifacts[row.endpoint_contract_artifact_id].sha256,
        "rollback_harness_release_id": _uuid(row.rollback_harness_release_id),
        "metadata": row.metadata_json,
    }


def _trial_row(row: HarnessTrial, artifacts: dict[uuid.UUID, Artifact]) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "trial_key": row.trial_key,
        "phase": row.phase,
        "status": row.status,
        "scope_id": row.scope_id,
        "champion_release_id": str(row.champion_release_id),
        "challenger_release_id": str(row.challenger_release_id),
        "parent_trial_id": _uuid(row.parent_trial_id),
        "history_partition_manifest_sha256": artifacts[
            row.history_partition_manifest_artifact_id
        ].sha256,
        "assignment_manifest_sha256": artifacts[row.assignment_manifest_artifact_id].sha256,
        "blinding_manifest_sha256": artifacts[row.blinding_manifest_artifact_id].sha256,
        "endpoint_contract_sha256": artifacts[row.endpoint_contract_artifact_id].sha256,
        "budget_contract_sha256": artifacts[row.budget_contract_artifact_id].sha256,
        "adjudication_run_id": _uuid(row.adjudication_run_id),
        "blinded": row.blinded,
        "adjudication_locked_at": _iso(row.adjudication_locked_at),
        "unblinded_at": _iso(row.unblinded_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "metadata": row.metadata_json,
    }


async def build_harness_replay_from_database(
    session: AsyncSession,
    terminal_trial_id: uuid.UUID,
    artifact_loader: Callable[[str], bytes],
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any]]:
    """Load a complete v36 snapshot only from typed DB rows and object-store bytes."""
    all_trials = list(await session.scalars(select(HarnessTrial)))
    trials_by_id = {row.id: row for row in all_trials}
    if terminal_trial_id not in trials_by_id:
        raise KeyError(f"harness trial not found: {terminal_trial_id}")
    chain_ids: set[uuid.UUID] = set()
    cursor = trials_by_id[terminal_trial_id]
    while cursor is not None:
        if cursor.id in chain_ids:
            raise ValueError("harness trial lineage contains a cycle")
        chain_ids.add(cursor.id)
        cursor = trials_by_id.get(cursor.parent_trial_id) if cursor.parent_trial_id else None
    trials = [row for row in all_trials if row.id in chain_ids]
    release_ids = {
        release_id
        for trial in trials
        for release_id in (trial.champion_release_id, trial.challenger_release_id)
    }
    all_edges = list(await session.scalars(select(HarnessLineageEdge)))
    all_releases = list(await session.scalars(select(HarnessRelease)))
    releases_by_id = {row.id: row for row in all_releases}
    frontier = list(release_ids)
    relevant_edges: list[HarnessLineageEdge] = []
    while frontier:
        child = frontier.pop()
        for edge in all_edges:
            if edge.child_release_id == child and edge not in relevant_edges:
                relevant_edges.append(edge)
                if edge.parent_release_id not in release_ids:
                    release_ids.add(edge.parent_release_id)
                    frontier.append(edge.parent_release_id)
    releases = [releases_by_id[item] for item in release_ids]
    assignments = list(
        await session.scalars(
            select(HarnessAssignment)
            .where(HarnessAssignment.trial_id.in_(chain_ids))
            .order_by(HarnessAssignment.assignment_rank)
        )
    )
    assignments.sort(
        key=lambda row: (PHASE_ORDER[trials_by_id[row.trial_id].phase], row.assignment_rank)
    )
    assignment_ids = {row.id for row in assignments}
    outcomes = list(
        await session.scalars(
            select(HarnessOutcome).where(HarnessOutcome.assignment_id.in_(assignment_ids))
        )
    )
    promotion = await session.scalar(
        select(HarnessPromotionDecision).where(
            HarnessPromotionDecision.prospective_trial_id == terminal_trial_id
        )
    )
    artifact_ids: set[uuid.UUID] = set()
    for release in releases:
        artifact_ids.update(
            {
                release.allowed_evidence_slice_artifact_id,
                release.forbidden_holdout_manifest_artifact_id,
                release.endpoint_contract_artifact_id,
            }
        )
    footprint_shas = {
        getattr(release, field) for release in releases for field in SHA_FIELDS
    }
    footprint_artifacts = list(
        await session.scalars(select(Artifact).where(Artifact.sha256.in_(footprint_shas)))
    )
    if {row.sha256 for row in footprint_artifacts} != footprint_shas:
        raise ValueError("one or more harness release footprints lack Artifact evidence")
    artifact_ids.update(row.id for row in footprint_artifacts)
    for trial in trials:
        artifact_ids.update(
            {
                trial.history_partition_manifest_artifact_id,
                trial.assignment_manifest_artifact_id,
                trial.blinding_manifest_artifact_id,
                trial.endpoint_contract_artifact_id,
                trial.budget_contract_artifact_id,
            }
        )
    artifact_ids.update(row.artifact_id for row in outcomes if row.artifact_id is not None)
    if promotion is not None:
        artifact_ids.add(promotion.decision_artifact_id)
    artifacts = list(
        await session.scalars(select(Artifact).where(Artifact.id.in_(artifact_ids)))
    )
    artifacts_by_id = {row.id: row for row in artifacts}
    if set(artifacts_by_id) != artifact_ids:
        raise ValueError("one or more typed harness artifacts are missing")
    artifact_payloads = {
        row.sha256: artifact_loader(row.storage_uri)
        for row in sorted(artifacts, key=lambda item: item.sha256)
    }
    run_ids = {row.experiment_run_id for row in assignments}
    run_ids.update(row.adjudication_run_id for row in trials if row.adjudication_run_id)
    existing_runs = set(
        await session.scalars(select(ExperimentRun.id).where(ExperimentRun.id.in_(run_ids)))
    )
    tool_call_ids = {row.tool_call_id for row in outcomes}
    existing_call_rows = list(
        await session.scalars(select(ToolCall).where(ToolCall.id.in_(tool_call_ids)))
    )
    existing_calls = {row.id for row in existing_call_rows}
    decision_ids = {promotion.agent_decision_id} if promotion is not None else set()
    existing_decision_rows = list(
        await session.scalars(
            select(AgentDecision).where(AgentDecision.id.in_(decision_ids))
        )
    )
    existing_decisions = {row.id for row in existing_decision_rows}
    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "terminal_trial_id": str(terminal_trial_id),
        "releases": sorted(
            [_release_row(row, artifacts_by_id) for row in releases],
            key=lambda row: row["harness_id"],
        ),
        "lineage_edges": sorted(
            [
                {
                    "child_release_id": str(row.child_release_id),
                    "parent_release_id": str(row.parent_release_id),
                    "relation_type": row.relation_type,
                }
                for row in relevant_edges
            ],
            key=lambda row: (
                row["child_release_id"],
                row["parent_release_id"],
                row["relation_type"],
            ),
        ),
        "trials": sorted(
            [_trial_row(row, artifacts_by_id) for row in trials],
            key=lambda row: PHASE_ORDER[row["phase"]],
        ),
        "assignments": [
            {
                "id": str(row.id),
                "trial_id": str(row.trial_id),
                "experiment_run_id": str(row.experiment_run_id),
                "episode_key": row.episode_key,
                "pair_key": row.pair_key,
                "assigned_release_id": str(row.assigned_release_id),
                "assigned_harness_id": releases_by_id[row.assigned_release_id].harness_id,
                "opaque_arm_label": row.opaque_arm_label,
                "assignment_rank": row.assignment_rank,
                "random_seed": row.random_seed,
                "resource_class": row.resource_class,
                "controls_formal_action": row.controls_formal_action,
                "metadata": row.metadata_json,
            }
            for row in assignments
        ],
        "outcomes": sorted(
            [
                {
                    "id": str(row.id),
                    "assignment_id": str(row.assignment_id),
                    "endpoint_family": row.endpoint_family,
                    "endpoint_name": row.endpoint_name,
                    "tool_call_id": str(row.tool_call_id),
                    "artifact_sha256": (
                        artifacts_by_id[row.artifact_id].sha256
                        if row.artifact_id is not None
                        else None
                    ),
                    "numeric_value": row.numeric_value,
                    "text_value": row.text_value,
                    "unit": row.unit,
                    "status": row.status,
                    "limitations": row.limitations_json,
                    "metadata": row.metadata_json,
                }
                for row in outcomes
            ],
            key=lambda row: (
                row["assignment_id"],
                row["endpoint_family"],
                row["endpoint_name"],
                row["tool_call_id"],
            ),
        ),
        "promotion_decision": (
            {
                "id": str(promotion.id),
                "prospective_trial_id": str(promotion.prospective_trial_id),
                "counterfactual_trial_id": str(promotion.counterfactual_trial_id),
                "shadow_trial_id": str(promotion.shadow_trial_id),
                "agent_decision_id": str(promotion.agent_decision_id),
                "decision": promotion.decision,
                "scope_id": promotion.scope_id,
                "promoted_release_id": _uuid(promotion.promoted_release_id),
                "rollback_release_id": _uuid(promotion.rollback_release_id),
                "decision_artifact_sha256": artifacts_by_id[
                    promotion.decision_artifact_id
                ].sha256,
                "effective_at": _iso(promotion.effective_at),
                "metadata": promotion.metadata_json,
            }
            if promotion is not None
            else None
        ),
        "artifacts": sorted(
            [
                {
                    "id": str(row.id),
                    "sha256": row.sha256,
                    "size_bytes": row.size_bytes,
                    "media_type": row.media_type,
                }
                for row in artifacts
            ],
            key=lambda row: row["sha256"],
        ),
        "existing_graph_refs": {
            "experiment_run_ids": sorted(str(item) for item in existing_runs),
            "tool_call_ids": sorted(str(item) for item in existing_calls),
            "agent_decision_ids": sorted(str(item) for item in existing_decisions),
            "tool_call_run_ids": {
                str(row.id): str(row.run_id)
                for row in sorted(existing_call_rows, key=lambda item: str(item.id))
            },
            "agent_decision_run_ids": {
                str(row.id): str(row.run_id)
                for row in sorted(existing_decision_rows, key=lambda item: str(item.id))
            },
        },
    }
    receipt = validate_harness_replay_snapshot(snapshot, artifact_payloads)
    snapshot["exact_replay"] = receipt["exact_replay"]
    snapshot["replay_sha256"] = receipt["replay_sha256"]
    return snapshot, artifact_payloads, receipt


class HarnessEvolutionRepository:
    """Retry-safe typed persistence primitives for v36 harness governance."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.events = ExperimentRepository(session)

    async def _require_artifacts(self, artifact_ids: Iterable[uuid.UUID]) -> None:
        for artifact_id in set(artifact_ids):
            if await self.session.get(Artifact, artifact_id) is None:
                raise KeyError(f"harness artifact not found: {artifact_id}")

    async def _require_artifact_shas(self, digests: Iterable[str]) -> None:
        required = set(digests)
        observed = set(
            await self.session.scalars(
                select(Artifact.sha256).where(Artifact.sha256.in_(required))
            )
        )
        if observed != required:
            raise KeyError("one or more harness footprint artifacts do not exist")

    async def register_release(
        self,
        *,
        harness_id: str,
        scope_id: str,
        release_status: str,
        change_hypothesis: str,
        primary_changed_component: str,
        source_revision: str,
        footprint_sha256: dict[str, str],
        history_cutoff_at: datetime,
        allowed_evidence_slice_artifact_id: uuid.UUID,
        forbidden_holdout_manifest_artifact_id: uuid.UUID,
        endpoint_contract_artifact_id: uuid.UUID,
        parent_release_id: uuid.UUID | None = None,
        rollback_harness_release_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HarnessRelease:
        if set(footprint_sha256) != set(SHA_FIELDS):
            raise ValueError("harness release footprint fields are incomplete")
        for field, value in footprint_sha256.items():
            _require_sha256(value, field)
        await self._require_artifact_shas(footprint_sha256.values())
        if history_cutoff_at.tzinfo is None:
            raise ValueError("harness history cutoff must be timezone-aware")
        await self._require_artifacts(
            {
                allowed_evidence_slice_artifact_id,
                forbidden_holdout_manifest_artifact_id,
                endpoint_contract_artifact_id,
            }
        )
        parent = (
            await self.session.get(HarnessRelease, parent_release_id)
            if parent_release_id is not None
            else None
        )
        if parent_release_id is not None and parent is None:
            raise KeyError("harness parent release does not exist")
        if parent is not None and parent.scope_id != scope_id:
            raise ValueError("harness parent release scope differs from child scope")
        rollback = (
            await self.session.get(HarnessRelease, rollback_harness_release_id)
            if rollback_harness_release_id is not None
            else None
        )
        if rollback_harness_release_id is not None and rollback is None:
            raise KeyError("harness rollback release does not exist")
        if rollback is not None:
            if parent is None:
                raise ValueError("a root harness release cannot declare a rollback target")
            if rollback.scope_id != scope_id:
                raise ValueError("harness rollback release scope differs from child scope")
            valid_rollback_targets = await self._release_ancestors(parent.id) | {parent.id}
            if rollback.id not in valid_rollback_targets:
                raise ValueError("harness rollback target is not in the parent ancestry")
        identity = {
            "harness_id": harness_id,
            "scope_id": scope_id,
            "release_status": release_status,
            "change_hypothesis": change_hypothesis,
            "primary_changed_component": primary_changed_component,
            "source_revision": source_revision,
            **footprint_sha256,
            "history_cutoff_at": history_cutoff_at.astimezone(UTC),
            "allowed_evidence_slice_artifact_id": allowed_evidence_slice_artifact_id,
            "forbidden_holdout_manifest_artifact_id": forbidden_holdout_manifest_artifact_id,
            "endpoint_contract_artifact_id": endpoint_contract_artifact_id,
            "rollback_harness_release_id": rollback_harness_release_id,
            "metadata_json": metadata or {},
        }
        existing = await self.session.scalar(
            select(HarnessRelease).where(HarnessRelease.harness_id == harness_id)
        )
        if existing is not None:
            if any(getattr(existing, field) != value for field, value in identity.items()):
                raise ValueError("harness release retry payload drifted")
            return existing
        release = HarnessRelease(**identity)
        self.session.add(release)
        await self.session.flush()
        if parent_release_id is not None:
            self.session.add(
                HarnessLineageEdge(
                    child_release_id=release.id,
                    parent_release_id=parent_release_id,
                    relation_type="derived_from",
                )
            )
            await self.session.flush()
        await self.events.append_event(
            "harness_release",
            release.id,
            "harness_release.registered",
            "v36-harness-governance",
            {
                "harness_id": harness_id,
                "parent_release_id": _uuid(parent_release_id),
                "source_revision": source_revision,
            },
        )
        return release

    async def create_trial(
        self,
        *,
        trial_key: str,
        phase: str,
        scope_id: str,
        champion_release_id: uuid.UUID,
        challenger_release_id: uuid.UUID,
        artifact_ids: dict[str, uuid.UUID],
        parent_trial_id: uuid.UUID | None = None,
        adjudication_run_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HarnessTrial:
        if phase not in PHASE_ORDER:
            raise ValueError("unknown harness trial phase")
        if champion_release_id == challenger_release_id:
            raise ValueError("champion and challenger must differ")
        required_artifacts = {
            "history_partition_manifest_artifact_id",
            "assignment_manifest_artifact_id",
            "blinding_manifest_artifact_id",
            "endpoint_contract_artifact_id",
            "budget_contract_artifact_id",
        }
        if set(artifact_ids) != required_artifacts:
            raise ValueError("harness trial artifact fields are incomplete")
        if phase == "prospective_equal_budget" and adjudication_run_id is None:
            raise ValueError("prospective trial requires an independent adjudication run")
        if phase != "prospective_equal_budget" and adjudication_run_id is not None:
            raise ValueError("only prospective trials may define an adjudication run")
        if adjudication_run_id is not None and await self.session.get(
            ExperimentRun, adjudication_run_id
        ) is None:
            raise KeyError("harness adjudication ExperimentRun does not exist")
        await self._require_artifacts(artifact_ids.values())
        champion = await self.session.get(HarnessRelease, champion_release_id)
        challenger = await self.session.get(HarnessRelease, challenger_release_id)
        if champion is None or challenger is None:
            raise KeyError("champion and challenger releases must exist")
        if champion.scope_id != scope_id or challenger.scope_id != scope_id:
            raise ValueError("harness trial scope differs from a release scope")
        parent = (
            await self.session.get(HarnessTrial, parent_trial_id)
            if parent_trial_id is not None
            else None
        )
        expected_parent_phase = PHASE_ORDER[phase] - 1
        if expected_parent_phase < 0 and parent is not None:
            raise ValueError("counterfactual replay cannot have a parent trial")
        if expected_parent_phase >= 0 and (
            parent is None
            or PHASE_ORDER.get(parent.phase) != expected_parent_phase
            or parent.status != "succeeded"
            or parent.champion_release_id != champion_release_id
            or parent.challenger_release_id != challenger_release_id
            or parent.scope_id != scope_id
        ):
            raise ValueError("harness trial parent does not prove the previous gate")
        existing = await self.session.scalar(
            select(HarnessTrial).where(HarnessTrial.trial_key == trial_key)
        )
        identity = {
            "trial_key": trial_key,
            "phase": phase,
            "status": "created",
            "scope_id": scope_id,
            "champion_release_id": champion_release_id,
            "challenger_release_id": challenger_release_id,
            "parent_trial_id": parent_trial_id,
            **artifact_ids,
            "adjudication_run_id": adjudication_run_id,
            "blinded": True,
            "metadata_json": metadata or {},
        }
        if existing is not None:
            if any(getattr(existing, field) != value for field, value in identity.items()):
                raise ValueError("harness trial retry payload drifted")
            return existing
        trial = HarnessTrial(**identity)
        self.session.add(trial)
        await self.session.flush()
        await self.events.append_event(
            "harness_trial",
            trial.id,
            "harness_trial.created",
            "v36-harness-governance",
            {"trial_key": trial_key, "phase": phase, "parent_trial_id": _uuid(parent_trial_id)},
        )
        return trial

    async def record_assignment(
        self,
        *,
        trial_id: uuid.UUID,
        experiment_run_id: uuid.UUID,
        episode_key: str,
        pair_key: str,
        assigned_release_id: uuid.UUID,
        opaque_arm_label: str,
        assignment_rank: int,
        random_seed: int | None,
        resource_class: str,
        controls_formal_action: bool,
        metadata: dict[str, Any] | None = None,
    ) -> HarnessAssignment:
        if assignment_rank < 1:
            raise ValueError("harness assignment rank must be positive")
        trial = await self.session.get(HarnessTrial, trial_id)
        run = await self.session.get(ExperimentRun, experiment_run_id)
        if trial is None or run is None:
            raise KeyError("harness assignment trial and ExperimentRun must exist")
        if trial.status != "created":
            raise ValueError("harness assignments are immutable after trial completion")
        if assigned_release_id not in {
            trial.champion_release_id,
            trial.challenger_release_id,
        }:
            raise ValueError("assignment release is outside the frozen trial pair")
        expected_control = (
            trial.phase == "shadow" and assigned_release_id == trial.champion_release_id
        )
        if controls_formal_action != expected_control:
            raise ValueError("formal-action control differs from the frozen trial phase")
        identity = {
            "trial_id": trial_id,
            "experiment_run_id": experiment_run_id,
            "episode_key": episode_key,
            "pair_key": pair_key,
            "assigned_release_id": assigned_release_id,
            "opaque_arm_label": opaque_arm_label,
            "assignment_rank": assignment_rank,
            "random_seed": random_seed,
            "resource_class": resource_class,
            "controls_formal_action": controls_formal_action,
            "metadata_json": metadata or {},
        }
        existing = await self.session.scalar(
            select(HarnessAssignment).where(
                HarnessAssignment.trial_id == trial_id,
                HarnessAssignment.assignment_rank == assignment_rank,
            )
        )
        if existing is not None:
            if any(getattr(existing, field) != value for field, value in identity.items()):
                raise ValueError("harness assignment retry payload drifted")
            return existing
        assignment = HarnessAssignment(**identity)
        self.session.add(assignment)
        await self.session.flush()
        await self.events.append_event(
            "harness_trial",
            trial_id,
            "harness_assignment.recorded",
            "v36-harness-governance",
            {
                "assignment_id": str(assignment.id),
                "experiment_run_id": str(experiment_run_id),
                "pair_key": pair_key,
                "assignment_rank": assignment_rank,
            },
        )
        return assignment

    async def record_outcome(
        self,
        *,
        assignment_id: uuid.UUID,
        endpoint_family: str,
        endpoint_name: str,
        tool_call_id: uuid.UUID,
        status: str,
        numeric_value: float | None = None,
        text_value: str | None = None,
        unit: str | None = None,
        artifact_id: uuid.UUID | None = None,
        limitations: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HarnessOutcome:
        if numeric_value is None and text_value is None:
            raise ValueError("harness outcome requires a numeric or text value")
        if numeric_value is not None and not math.isfinite(numeric_value):
            raise ValueError("harness outcome must be finite")
        assignment = await self.session.get(HarnessAssignment, assignment_id)
        call = await self.session.get(ToolCall, tool_call_id)
        if assignment is None or call is None:
            raise KeyError("harness outcome assignment and ToolCall must exist")
        trial = await self.session.get(HarnessTrial, assignment.trial_id)
        if trial is None or trial.status != "created":
            raise ValueError("harness outcomes are immutable after trial completion")
        if call.run_id != assignment.experiment_run_id:
            raise ValueError("harness outcome ToolCall is detached from assignment run")
        if artifact_id is not None:
            await self._require_artifacts({artifact_id})
        identity = {
            "assignment_id": assignment_id,
            "endpoint_family": endpoint_family,
            "endpoint_name": endpoint_name,
            "tool_call_id": tool_call_id,
            "artifact_id": artifact_id,
            "numeric_value": numeric_value,
            "text_value": text_value,
            "unit": unit,
            "status": status,
            "limitations_json": limitations or [],
            "metadata_json": metadata or {},
        }
        existing = await self.session.scalar(
            select(HarnessOutcome).where(
                HarnessOutcome.assignment_id == assignment_id,
                HarnessOutcome.endpoint_family == endpoint_family,
                HarnessOutcome.endpoint_name == endpoint_name,
                HarnessOutcome.tool_call_id == tool_call_id,
            )
        )
        if existing is not None:
            if any(getattr(existing, field) != value for field, value in identity.items()):
                raise ValueError("harness outcome retry payload drifted")
            return existing
        outcome = HarnessOutcome(**identity)
        self.session.add(outcome)
        await self.session.flush()
        return outcome

    async def complete_trial(
        self,
        trial_id: uuid.UUID,
        *,
        adjudication_locked_at: datetime | None = None,
        unblinded_at: datetime | None = None,
    ) -> HarnessTrial:
        trial = await self.session.get(HarnessTrial, trial_id, with_for_update=True)
        if trial is None:
            raise KeyError(f"harness trial not found: {trial_id}")
        if trial.status == "succeeded":
            return trial
        assignments = list(
            await self.session.scalars(
                select(HarnessAssignment)
                .where(HarnessAssignment.trial_id == trial_id)
                .order_by(HarnessAssignment.assignment_rank)
            )
        )
        by_pair: dict[str, list[HarnessAssignment]] = defaultdict(list)
        for assignment in assignments:
            by_pair[assignment.pair_key].append(assignment)
        if not by_pair:
            raise ValueError("harness trial has no paired assignments")
        for pair in by_pair.values():
            if len(pair) != 2 or {row.assigned_release_id for row in pair} != {
                trial.champion_release_id,
                trial.challenger_release_id,
            }:
                raise ValueError("each harness pair must contain champion and challenger exactly")
            if len({row.episode_key for row in pair}) != 1:
                raise ValueError("paired harness assignments must use the same episode")
            if len({row.random_seed for row in pair}) != 1:
                raise ValueError("paired harness assignments must use the same seed")
            if len({row.resource_class for row in pair}) != 1:
                raise ValueError("paired harness assignments must use the same resource class")
            expected_controls = (
                {trial.champion_release_id}
                if trial.phase == "shadow"
                else set()
            )
            observed_controls = {
                row.assigned_release_id for row in pair if row.controls_formal_action
            }
            if observed_controls != expected_controls:
                raise ValueError("formal-action control differs from the frozen trial phase")
        runs = {
            run.id: run
            for run in await self.session.scalars(
                select(ExperimentRun).where(
                    ExperimentRun.id.in_({row.experiment_run_id for row in assignments})
                )
            )
        }
        if len(runs) != len({row.experiment_run_id for row in assignments}) or any(
            run.status != "succeeded" for run in runs.values()
        ):
            raise ValueError("harness trial assignment runs are missing or incomplete")
        outcomes = list(
            await self.session.scalars(
                select(HarnessOutcome).where(
                    HarnessOutcome.assignment_id.in_({row.id for row in assignments})
                )
            )
        )
        outcomes_by_assignment: dict[uuid.UUID, list[HarnessOutcome]] = defaultdict(list)
        for outcome in outcomes:
            outcomes_by_assignment[outcome.assignment_id].append(outcome)
        if any(not outcomes_by_assignment[row.id] for row in assignments):
            raise ValueError("harness trial assignment lacks persisted outcomes")
        if any(outcome.status != "succeeded" for outcome in outcomes):
            raise ValueError("harness trial contains a failed outcome")
        if trial.phase == "prospective_equal_budget":
            if adjudication_locked_at is None or unblinded_at is None:
                raise ValueError("prospective trial must lock adjudication before unblinding")
            if adjudication_locked_at > unblinded_at:
                raise ValueError("prospective adjudication lock occurs after unblinding")
            adjudication_run = await self.session.get(ExperimentRun, trial.adjudication_run_id)
            if adjudication_run is None or adjudication_run.status != "succeeded":
                raise ValueError("prospective adjudication run is missing or incomplete")
            assignment_run_ids = {row.experiment_run_id for row in assignments}
            if adjudication_run.id in assignment_run_ids:
                raise ValueError("prospective adjudication run must be independent")
        elif adjudication_locked_at is not None or unblinded_at is not None:
            raise ValueError("only a prospective trial may record unblinding")
        trial.status = "succeeded"
        trial.finished_at = datetime.now(UTC)
        trial.adjudication_locked_at = adjudication_locked_at
        trial.unblinded_at = unblinded_at
        await self.events.append_event(
            "harness_trial",
            trial.id,
            "harness_trial.succeeded",
            "v36-harness-governance",
            {
                "phase": trial.phase,
                "adjudication_locked_at": _iso(adjudication_locked_at),
                "unblinded_at": _iso(unblinded_at),
            },
        )
        return trial

    async def _release_ancestors(self, release_id: uuid.UUID) -> set[uuid.UUID]:
        edges = list(await self.session.scalars(select(HarnessLineageEdge)))
        parents: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
        for edge in edges:
            parents[edge.child_release_id].add(edge.parent_release_id)
        ancestors: set[uuid.UUID] = set()
        visiting: set[uuid.UUID] = set()
        visited: set[uuid.UUID] = set()

        def visit(node: uuid.UUID) -> None:
            if node in visiting:
                raise ValueError("harness lineage contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for parent in parents.get(node, set()):
                ancestors.add(parent)
                visit(parent)
            visiting.remove(node)
            visited.add(node)

        visit(release_id)
        return ancestors

    async def record_promotion_decision(
        self,
        *,
        prospective_trial_id: uuid.UUID,
        counterfactual_trial_id: uuid.UUID,
        shadow_trial_id: uuid.UUID,
        agent_decision_id: uuid.UUID,
        decision: str,
        scope_id: str,
        decision_artifact_id: uuid.UUID,
        promoted_release_id: uuid.UUID | None = None,
        rollback_release_id: uuid.UUID | None = None,
        effective_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HarnessPromotionDecision:
        if decision not in PROMOTION_DECISIONS:
            raise ValueError("unknown harness promotion decision")
        counterfactual = await self.session.get(HarnessTrial, counterfactual_trial_id)
        shadow = await self.session.get(HarnessTrial, shadow_trial_id)
        prospective = await self.session.get(HarnessTrial, prospective_trial_id)
        agent_decision = await self.session.get(AgentDecision, agent_decision_id)
        if any(
            item is None
            for item in (counterfactual, shadow, prospective, agent_decision)
        ):
            raise KeyError("promotion decision is missing a gate trial or AgentDecision")
        if (
            counterfactual.phase != "counterfactual_replay"
            or shadow.phase != "shadow"
            or prospective.phase != "prospective_equal_budget"
            or shadow.parent_trial_id != counterfactual.id
            or prospective.parent_trial_id != shadow.id
        ):
            raise ValueError("promotion trials do not form the required three-gate chain")
        if {counterfactual.status, shadow.status, prospective.status} != {"succeeded"}:
            raise ValueError("promotion requires succeeded replay, shadow, and prospective trials")
        if prospective.scope_id != scope_id:
            raise ValueError("promotion scope differs from prospective trial")
        if prospective.adjudication_locked_at is None or prospective.unblinded_at is None:
            raise ValueError("promotion requires locked adjudication and unblinding evidence")
        if prospective.adjudication_locked_at > prospective.unblinded_at:
            raise ValueError("prospective adjudication was not locked before unblinding")
        if prospective.adjudication_run_id is None:
            raise ValueError("promotion requires a typed independent adjudication run")
        if agent_decision.run_id != prospective.adjudication_run_id:
            raise ValueError("promotion AgentDecision is detached from adjudication run")
        await self._require_artifacts({decision_artifact_id})
        if decision == "promote_for_declared_scope":
            if promoted_release_id != prospective.challenger_release_id:
                raise ValueError("promotion must name the tested challenger")
            if rollback_release_id is not None:
                raise ValueError("promotion cannot also be rollback")
        elif decision == "rollback_to_registered_ancestor":
            if rollback_release_id not in await self._release_ancestors(
                prospective.champion_release_id
            ):
                raise ValueError("rollback target is not an ancestor of the champion")
            if promoted_release_id is not None:
                raise ValueError("rollback cannot also promote a release")
        elif promoted_release_id is not None or rollback_release_id is not None:
            raise ValueError("retention or rejection cannot change active release")
        identity = {
            "prospective_trial_id": prospective_trial_id,
            "counterfactual_trial_id": counterfactual_trial_id,
            "shadow_trial_id": shadow_trial_id,
            "agent_decision_id": agent_decision_id,
            "decision": decision,
            "scope_id": scope_id,
            "promoted_release_id": promoted_release_id,
            "rollback_release_id": rollback_release_id,
            "decision_artifact_id": decision_artifact_id,
            "effective_at": effective_at,
            "metadata_json": metadata or {},
        }
        existing = await self.session.scalar(
            select(HarnessPromotionDecision).where(
                HarnessPromotionDecision.prospective_trial_id == prospective_trial_id
            )
        )
        if existing is not None:
            if any(getattr(existing, field) != value for field, value in identity.items()):
                raise ValueError("harness promotion retry payload drifted")
            return existing
        promotion = HarnessPromotionDecision(**identity)
        self.session.add(promotion)
        await self.session.flush()
        await self.events.append_event(
            "harness_trial",
            prospective_trial_id,
            "harness_promotion_decision.recorded",
            "v36-harness-governance",
            {
                "promotion_decision_id": str(promotion.id),
                "decision": decision,
                "scope_id": scope_id,
                "promoted_release_id": _uuid(promoted_release_id),
                "rollback_release_id": _uuid(rollback_release_id),
            },
        )
        return promotion
