from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import math
import statistics
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select

from pepagent.db.models import (
    AgentDecision,
    Artifact,
    Candidate,
    Evaluation,
    EvidenceArtifact,
    ExperimentRun,
    ToolCall,
    ToolCallDependency,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.enums import RunStatus
from pepagent.evidence_replay import build_database_evidence_graph, replay_v32_portfolio
from pepagent.multiobjective_portfolio import MultiobjectivePortfolioManifest
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.storage.object_store import ContentAddressedObjectStore, StoredObject
from pepagent.workers.activities import _register_artifact

ACCEPTANCE_VERSION = "v32.acceptance.0.1"


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _csv_bytes(rows: list[dict[str, Any]], fieldnames: list[str]) -> bytes:
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return handle.getvalue().encode("utf-8")


def _evaluation_maps(graph: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    values: dict[str, dict[str, Any]] = {}
    metric_names: set[str] = set()
    for item in graph["evaluations"]:
        candidate_id = item["candidate_id"]
        metric_name = item["metric_name"]
        metric_names.add(metric_name)
        value = item["numeric_value"] if item["numeric_value"] is not None else item["text_value"]
        target = values.setdefault(candidate_id, {})
        if metric_name in target:
            raise ValueError(f"ambiguous evaluation: {candidate_id}/{metric_name}")
        target[metric_name] = value
    return values, sorted(metric_names)


def _lane_summary_rows(portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    keys = [
        "net_charge_ph7_4",
        "hydrophobic_moment_eisenberg",
        "hydrophobic_ratio_modlamp",
        "macrel_amp_probability",
        "llamp_predicted_mic_um",
        "amp_read_predicted_mic_um",
        "toxinpred3_hybrid_score",
        "macrel_hemolysis_probability",
    ]
    rows = []
    for lane in ("membrane", "activity_mic", "risk_control", "balanced"):
        selected = [item for item in portfolio["lane_results"] if item["lane"] == lane]
        numeric_summary = {}
        for key in keys:
            values = [float(item["metrics"][key]) for item in selected]
            numeric_summary[key] = {
                "minimum": min(values),
                "median": statistics.median(values),
                "maximum": max(values),
            }
        rows.append(
            {
                "lane": lane,
                "count": len(selected),
                "seed_counts_json": json.dumps(
                    {
                        str(seed): sum(item["seed"] == seed for item in selected)
                        for seed in sorted({item["seed"] for item in selected})
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "toxicity_label_counts_json": json.dumps(
                    {
                        label: sum(item["labels"]["toxinpred3_label"] == label for item in selected)
                        for label in sorted(
                            {item["labels"]["toxinpred3_label"] for item in selected}
                        )
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "hemolysis_label_counts_json": json.dumps(
                    {
                        label: sum(
                            item["labels"]["macrel_hemolysis_label"] == label
                            for item in selected
                        )
                        for label in sorted(
                            {item["labels"]["macrel_hemolysis_label"] for item in selected}
                        )
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "numeric_summary_json": json.dumps(
                    numeric_summary, sort_keys=True, separators=(",", ":")
                ),
                "interpretation": {
                    "membrane": "membrane-descriptor hypothesis; single-model risk warnings remain",
                    "activity_mic": "AMP/MIC soft-prediction hypothesis; not measured activity",
                    "risk_control": "lower soft-risk hypothesis; predicted activity may be weaker",
                    "balanced": "minimax family-depth compromise without a weighted total",
                }[lane],
            }
        )
    return rows


def build_acceptance_exports(
    graph: dict[str, Any], portfolio: dict[str, Any], contract: dict[str, Any]
) -> dict[str, bytes]:
    evaluations, metric_names = _evaluation_maps(graph)
    selected = {item["candidate_id"]: item for item in portfolio["lane_results"]}
    excluded = set(portfolio["excluded_risk_red_candidate_ids"])
    all_rows = []
    for item in graph["candidates"]:
        selection = selected.get(item["id"])
        row = {
            "candidate_id": item["id"],
            "proposal_rank": item["proposal_rank"],
            "generator_seed": item["metadata"]["generator_seed"],
            "raw_rank": item["metadata"]["raw_rank"],
            "sequence": item["sequence"],
            "sequence_sha256": item["sequence_sha256"],
            "status": item["status"],
            "risk_guard_eligible": item["id"] not in excluded,
            "selected_lane": selection["lane"] if selection else "",
            "lane_rank": selection["lane_rank"] if selection else "",
        }
        row.update({name: evaluations[item["id"]][name] for name in metric_names})
        all_rows.append(row)
    all_fields = [
        "candidate_id",
        "proposal_rank",
        "generator_seed",
        "raw_rank",
        "sequence",
        "sequence_sha256",
        "status",
        "risk_guard_eligible",
        "selected_lane",
        "lane_rank",
        *metric_names,
    ]
    portfolio_rows = []
    for item in portfolio["lane_results"]:
        portfolio_rows.append(
            {
                "lane": item["lane"],
                "lane_rank": item["lane_rank"],
                "candidate_id": item["candidate_id"],
                "generator_seed": item["seed"],
                "sequence": item["sequence"],
                "sequence_sha256": item["sequence_sha256"],
                "family_depths_json": json.dumps(
                    item["family_depths"], sort_keys=True, separators=(",", ":")
                ),
                "metrics_json": json.dumps(
                    item["metrics"], sort_keys=True, separators=(",", ":")
                ),
                "labels_json": json.dumps(
                    item["labels"], sort_keys=True, separators=(",", ":")
                ),
                "claim_scope": item["claim_scope"],
            }
        )
    portfolio_fields = list(portfolio_rows[0])
    lane_rows = _lane_summary_rows(portfolio)
    lane_fields = list(lane_rows[0])

    lane_counts = {
        lane: sum(item["lane"] == lane for item in portfolio["lane_results"])
        for lane in ("membrane", "activity_mic", "risk_control", "balanced")
    }
    seed_lane_counts = {
        lane: {
            str(seed): sum(
                item["lane"] == lane and item["seed"] == seed
                for item in portfolio["lane_results"]
            )
            for seed in (20261101, 20261102, 20261103)
        }
        for lane in lane_counts
    }
    selected_double_red = [
        item["candidate_id"]
        for item in portfolio["lane_results"]
        if item["labels"]["toxinpred3_label"] == "Toxin"
        and item["labels"]["macrel_hemolysis_label"] == "high"
    ]
    charge_values = [
        float(item["metrics"]["net_charge_ph7_4"])
        for item in portfolio["lane_results"]
    ]
    expected = contract["expected_parent"]
    exact_parent_counts = (
        len(graph["candidates"]) == expected["candidate_count"]
        and len(graph["tool_calls"]) == expected["tool_call_count"]
        and len(graph["evaluations"]) == expected["evaluation_count"]
        and len(graph["tool_call_dependencies"])
        == expected["tool_call_dependency_count"]
        and len(graph["agent_decisions"]) == expected["agent_decision_count"]
        and portfolio["selected_count"] == expected["selected_count"]
        and portfolio["eligible_count"] == expected["eligible_count"]
        and portfolio["concordant_risk_red_count"]
        == expected["concordant_risk_red_count"]
    )
    gates = {
        "exact_database_replay": True,
        "exact_parent_counts": exact_parent_counts,
        "source_artifacts_content_verified": True,
        "selected_count": len(portfolio["lane_results"]) == 24,
        "selected_per_lane": all(value == 6 for value in lane_counts.values()),
        "selected_per_seed_per_lane": all(
            value == 2 for counts in seed_lane_counts.values() for value in counts.values()
        ),
        "no_selected_concordant_risk_red": not selected_double_red,
        "no_weighted_total_score": portfolio["weighted_total_score_used"] is False,
        "charge_was_observe_only": portfolio["charge_optimized"] is False,
        "selected_charge_values_finite": all(math.isfinite(value) for value in charge_values),
        "all_exports_content_addressed": True,
    }
    manifest = {
        "schema_version": "1.0",
        "benchmark_id": contract["benchmark_id"],
        "parent_run_id": contract["parent_run_id"],
        "parent_graph_sha256": graph["graph_sha256"],
        "parent_counts": {
            key: len(graph[key])
            for key in (
                "candidates",
                "tool_calls",
                "evaluations",
                "tool_call_dependencies",
                "agent_decisions",
            )
        },
        "portfolio_counts": {
            "eligible": portfolio["eligible_count"],
            "concordant_risk_red": portfolio["concordant_risk_red_count"],
            "selected": portfolio["selected_count"],
            "lanes": lane_counts,
            "seed_by_lane": seed_lane_counts,
        },
        "v33_readiness_gates": gates,
        "verdict": (
            "ready_for_v33_preregistration"
            if all(gates.values())
            else "not_ready_for_v33_preregistration"
        ),
        "claim_scope": "computational_hypothesis_portfolio_only",
        "limitations": [
            "AMP/MIC, toxicity, and hemolysis values are soft predictions, not measurements.",
            "Single-model warnings remain eligible under the frozen concordant-red policy.",
            "Readiness authorizes preregistration only, not v33 generation or execution.",
        ],
    }
    return {
        "all_candidates_csv": _csv_bytes(all_rows, all_fields),
        "portfolio_candidates_csv": _csv_bytes(portfolio_rows, portfolio_fields),
        "lane_summary_csv": _csv_bytes(lane_rows, lane_fields),
        "acceptance_manifest_json": _canonical_json_bytes(manifest),
    }


async def _parent_counts(run_id: uuid.UUID) -> dict[str, int]:
    async with SessionFactory() as session:
        candidate_ids = select(Candidate.id).where(Candidate.run_id == run_id)
        tool_ids = select(ToolCall.id).where(ToolCall.run_id == run_id)
        return {
            "candidate_count": int(
                await session.scalar(
                    select(func.count()).select_from(Candidate).where(Candidate.run_id == run_id)
                )
            ),
            "tool_call_count": int(
                await session.scalar(
                    select(func.count()).select_from(ToolCall).where(ToolCall.run_id == run_id)
                )
            ),
            "evaluation_count": int(
                await session.scalar(
                    select(func.count()).select_from(Evaluation).where(Evaluation.candidate_id.in_(candidate_ids))
                )
            ),
            "tool_call_dependency_count": int(
                await session.scalar(
                    select(func.count()).select_from(ToolCallDependency).where(
                        ToolCallDependency.child_tool_call_id.in_(tool_ids)
                    )
                )
            ),
            "agent_decision_count": int(
                await session.scalar(
                    select(func.count())
                    .select_from(AgentDecision)
                    .where(AgentDecision.run_id == run_id)
                )
            ),
        }


async def _source_artifact_payload(
    run_id: uuid.UUID, role: str, expected_sha256: str
) -> bytes:
    async with SessionFactory() as session:
        row = await session.execute(
            select(Artifact)
            .join(EvidenceArtifact, EvidenceArtifact.artifact_id == Artifact.id)
            .join(ToolCall, ToolCall.id == EvidenceArtifact.tool_call_id)
            .where(ToolCall.run_id == run_id, EvidenceArtifact.role == role)
        )
        artifact = row.scalar_one()
    if artifact.sha256 != expected_sha256:
        raise ValueError(f"{role} SHA mismatch")
    payload = await asyncio.to_thread(ContentAddressedObjectStore().get_bytes, artifact.storage_uri)
    if sha256_bytes(payload) != expected_sha256:
        raise OSError(f"{role} object bytes failed SHA verification")
    return payload


async def _create_child_run(contract: dict[str, Any], parent: ExperimentRun) -> ExperimentRun:
    async with SessionFactory() as session, session.begin():
        existing = list(
            await session.scalars(
                select(ExperimentRun).where(ExperimentRun.parent_run_id == parent.id)
            )
        )
        if any(item.spec_json.get("benchmark_id") == contract["benchmark_id"] for item in existing):
            raise ValueError("v32 acceptance run has already been submitted")
        run = ExperimentRun(
            target_id=parent.target_id,
            spec_json=contract,
            spec_sha256=sha256_json(contract),
            status=RunStatus.RUNNING,
            parent_run_id=parent.id,
            temporal_workflow_id=f"database-native-{contract['benchmark_id']}",
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        repository = ExperimentRepository(session)
        await repository.append_event(
            "run", run.id, "run.created", "v32-acceptance", {"parent_run_id": str(parent.id)}
        )
        await repository.append_event(
            "run", run.id, "run.started", "v32-acceptance", {"mode": "database_native_read_only"}
        )
        return run


async def execute_acceptance(config_path: Path, output_dir: Path) -> dict[str, Any]:
    contract_text = await asyncio.to_thread(config_path.read_text, encoding="utf-8")
    contract = yaml.safe_load(contract_text)
    if not contract["execution_authorized"]:
        raise ValueError("v32 acceptance execution is not authorized")
    parent_id = uuid.UUID(contract["parent_run_id"])
    source_path = Path(contract["source_manifest_path"])
    source_text = await asyncio.to_thread(source_path.read_text, encoding="utf-8")
    source_manifest = MultiobjectivePortfolioManifest.model_validate(yaml.safe_load(source_text))
    async with SessionFactory() as session:
        parent = await session.get(ExperimentRun, parent_id)
        if parent is None or parent.status != RunStatus.SUCCEEDED:
            raise ValueError("parent v32 run is not succeeded")
        await session.refresh(parent)
    counts = await _parent_counts(parent_id)
    for key, value in counts.items():
        if value != contract["expected_parent"][key]:
            raise ValueError(f"parent {key} mismatch: {value}")
    portfolio_bytes = await _source_artifact_payload(
        parent_id, "portfolio_output", contract["expected_parent"]["portfolio_artifact_sha256"]
    )
    replay_bytes = await _source_artifact_payload(
        parent_id,
        "database_replay_bundle",
        contract["expected_parent"]["database_replay_bundle_sha256"],
    )
    portfolio = json.loads(portfolio_bytes)
    stored_replay = json.loads(replay_bytes)
    if stored_replay.get("exact_replay") is not True:
        raise ValueError("parent database replay is not exact")
    async with SessionFactory() as session:
        graph = await build_database_evidence_graph(session, parent_id)
    replayed = replay_v32_portfolio(graph, source_manifest)
    if replayed != portfolio or replayed != stored_replay["replayed_portfolio"]:
        raise ValueError("parent portfolio cannot be exactly reconstructed")
    exports = build_acceptance_exports(graph, portfolio, contract)
    repeated = build_acceptance_exports(graph, replayed, contract)
    export_shas = {name: sha256_bytes(payload) for name, payload in exports.items()}
    if export_shas != {name: sha256_bytes(payload) for name, payload in repeated.items()}:
        raise ValueError("derived exports failed deterministic replay")
    replay_bundle = {
        "schema_version": "1.0",
        "parent_run_id": str(parent_id),
        "parent_graph_sha256": graph["graph_sha256"],
        "source_portfolio_sha256": sha256_bytes(portfolio_bytes),
        "source_replay_bundle_sha256": sha256_bytes(replay_bytes),
        "derived_export_sha256": export_shas,
        "exact_derived_replay": True,
    }
    exports["derived_replay_bundle_json"] = _canonical_json_bytes(replay_bundle)
    stored: dict[str, StoredObject] = {}
    store = ContentAddressedObjectStore()
    for name, payload in exports.items():
        media_type = "text/csv; charset=utf-8" if name.endswith("_csv") else "application/json"
        stored[name] = await asyncio.to_thread(store.put_bytes, payload, media_type)

    child = await _create_child_run(contract, parent)
    try:
        async with SessionFactory() as session, session.begin():
            repository = ExperimentRepository(session)
            export_call = await repository.record_completed_tool_call(
                child.id,
                "v32-database-native-acceptance-export",
                ACCEPTANCE_VERSION,
                sha256_json({"implementation_revision": contract["implementation"]["revision"]}),
                {
                    "parent_run_id": str(parent_id),
                    "parent_graph_sha256": graph["graph_sha256"],
                    "source_portfolio_sha256": sha256_bytes(portfolio_bytes),
                    "source_replay_bundle_sha256": sha256_bytes(replay_bytes),
                },
                {"filesystem_intermediates_used": False, "parent_backwrite": False},
                {"derived_export_sha256": export_shas},
                model_uri="deterministic://v32-database-native-acceptance-export",
            )
            for name, item in stored.items():
                if name == "derived_replay_bundle_json":
                    continue
                await _register_artifact(
                    session,
                    export_call.id,
                    asdict(item),
                    name,
                    {"benchmark_id": contract["benchmark_id"], "parent_run_id": str(parent_id)},
                )
            verify_call = await repository.record_completed_tool_call(
                child.id,
                "v32-derived-artifact-replay-verifier",
                ACCEPTANCE_VERSION,
                sha256_json({"implementation": "derived-replay-v1"}),
                {"export_tool_call_id": str(export_call.id), "export_sha256": export_shas},
                {"database_and_object_store_only": True},
                replay_bundle,
                model_uri="deterministic://v32-derived-artifact-replay-verifier",
            )
            await repository.record_tool_dependency(
                verify_call.id, export_call.id, "verifies_derived_exports"
            )
            replay_artifact = await _register_artifact(
                session,
                verify_call.id,
                asdict(stored["derived_replay_bundle_json"]),
                "derived_replay_bundle_json",
                {"benchmark_id": contract["benchmark_id"], "exact_derived_replay": True},
            )
            acceptance = json.loads(exports["acceptance_manifest_json"])
            prompt = (
                "Verify the frozen v32 database evidence and decide only whether its evidence "
                "governance is ready for a separately preregistered v33 charge-design protocol."
            )
            response = json.dumps(
                acceptance, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            decision = await repository.record_agent_decision(
                child.id,
                0,
                "v32_database_native_acceptance",
                "deterministic-acceptance-agent",
                ACCEPTANCE_VERSION,
                prompt,
                response,
                acceptance,
                response_artifact_id=replay_artifact.id,
            )
            await repository.record_agent_tool_edge(
                decision.id, export_call.id, "input", "observes_database_native_exports"
            )
            await repository.record_agent_tool_edge(
                decision.id, verify_call.id, "output", "materializes_acceptance_verdict"
            )
            run = await session.get(ExperimentRun, child.id, with_for_update=True)
            run.status = RunStatus.SUCCEEDED
            run.finished_at = datetime.now(UTC)
            await repository.append_event(
                "run",
                child.id,
                "v32.acceptance_completed",
                "deterministic-acceptance-agent",
                {
                    "verdict": acceptance["verdict"],
                    "export_sha256": {name: item.sha256 for name, item in stored.items()},
                    "exact_derived_replay": True,
                },
            )
    except Exception:
        async with SessionFactory() as session, session.begin():
            repository = ExperimentRepository(session)
            run = await session.get(ExperimentRun, child.id, with_for_update=True)
            run.status = RunStatus.FAILED
            run.finished_at = datetime.now(UTC)
            await repository.append_event(
                "run", child.id, "v32.acceptance_failed", "v32-acceptance", {}
            )
        raise

    await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
    for name, payload in exports.items():
        suffix = ".csv" if name.endswith("_csv") else ".json"
        await asyncio.to_thread((output_dir / f"{name}{suffix}").write_bytes, payload)
    return {
        "run_id": str(child.id),
        "parent_run_id": str(parent_id),
        "verdict": json.loads(exports["acceptance_manifest_json"])["verdict"],
        "artifacts": {name: asdict(item) for name, item in stored.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(execute_acceptance(args.config, args.output_dir)), indent=2))


if __name__ == "__main__":
    main()
