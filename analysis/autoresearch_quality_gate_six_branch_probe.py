from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from pepagent.autoresearch_closed_loop import (
    MultiFrontArchivePolicy,
    apply_evolution_action,
    build_multi_front_archive,
    parse_evolution_action,
)
from pepagent.autoresearch_operational_call import (
    OperationalCallRecord,
    persist_operational_call,
)
from pepagent.autoresearch_planner import (
    PlannerDeltaEvidence,
    _hydrophobic_fraction,
    _sequence_prescreen,
    build_multifront_rule_action_plan,
)
from pepagent.db.models import (
    AutoResearchMetricDelta,
    Candidate,
    Evaluation,
    ExperimentRun,
    ToolCall,
)
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_bytes, sha256_json, sha256_text
from pepagent.workers.autoresearch_activities import (
    _effective_planner_seed,
    _select_complete_evidence,
)

PARENT_RUNS = {
    "acea": uuid.UUID("2e6f38f2-4730-57cc-b149-8c54eda82cd9"),
    "angpt1": uuid.UUID("2bcb662d-da67-51e3-adbe-997d3aacad89"),
    "fgf2": uuid.UUID("eb85d014-e7b3-5f6c-acab-34ac580b30e1"),
    "gyra": uuid.UUID("15ea9977-4ea1-52a6-bcf0-f6e620803d19"),
    "pbp2a": uuid.UUID("bde9b74d-84a0-50c4-9002-8ae419d937e3"),
    "vegfa": uuid.UUID("7490f36c-8f0a-5908-af1d-de1fa97f09cf"),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay six immutable parent cohorts through quality-gated rule planning."
    )
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--persist", action="store_true")
    return parser


async def _probe_branch(
    *,
    branch_key: str,
    parent_run_id: uuid.UUID,
    source_commit: str,
    operator_release_sha256: str,
    globally_excluded_sha256s: set[str],
    probe_sequence_sha256s: set[str],
    persist: bool,
) -> dict[str, Any]:
    async with SessionFactory() as session:
        run = await session.get(ExperimentRun, parent_run_id)
        if run is None:
            raise ValueError(f"missing parent run {parent_run_id}")
        spec = run.spec_json or {}
        if spec.get("branch_key") != branch_key:
            raise ValueError(f"parent run branch drifted for {branch_key}")
        request = dict(spec.get("workflow_request") or {})
        execution_contract = dict(request.get("execution_contract") or {})
        required_metrics = set(execution_contract.get("required_sequence_metrics") or ())
        if len(required_metrics) != 12:
            raise ValueError(f"{branch_key} parent does not declare 12 metrics")
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == parent_run_id)
                .order_by(Candidate.generation, Candidate.proposal_rank, Candidate.id)
            )
        )
        candidate_ids = [item.id for item in candidates]
        evaluations = list(
            await session.scalars(
                select(Evaluation).where(Evaluation.candidate_id.in_(candidate_ids))
            )
        )
        calls = {
            item.id: item
            for item in await session.scalars(
                select(ToolCall).where(
                    ToolCall.id.in_({row.tool_call_id for row in evaluations})
                )
            )
        }
        evidence = _select_complete_evidence(
            candidates=candidates,
            evaluations=evaluations,
            calls=calls,
            required_metrics=required_metrics,
        )
        if not evidence:
            raise ValueError(f"{branch_key} has no complete parent evidence")
        generation = max(item.generation for item in candidates)
        archive_policy = MultiFrontArchivePolicy.model_validate(request["archive_policy"])
        snapshot = build_multi_front_archive(
            evidence,
            archive_policy,
            generation=generation,
        )
        deltas = list(
            await session.scalars(
                select(AutoResearchMetricDelta)
                .where(AutoResearchMetricDelta.child_candidate_id.in_(candidate_ids))
                .order_by(
                    AutoResearchMetricDelta.child_candidate_id,
                    AutoResearchMetricDelta.metric_name,
                    AutoResearchMetricDelta.id,
                )
            )
        )
        delta_evidence = tuple(
            PlannerDeltaEvidence(
                candidate_id=str(item.child_candidate_id),
                metric_name=item.metric_name,
                delta_sha256=item.delta_sha256,
                improved=bool(item.improved),
            )
            for item in deltas
        )
        planner_contract = dict(
            (request.get("planner_provider") or {}).get("planner_contract") or {}
        )
        seed = _effective_planner_seed(planner_contract, generation)
        de_novo_quota = float(planner_contract.get("de_novo_quota", 0.2))
        excluded = globally_excluded_sha256s | probe_sequence_sha256s
        plan = build_multifront_rule_action_plan(
            candidates=evidence,
            snapshot=snapshot,
            branch_key=branch_key,
            generation=generation + 1,
            seed=seed,
            operator_release_sha256=operator_release_sha256,
            target_sequence_sha256=str(spec["target_sequence_sha256"]),
            prior_deltas=delta_evidence,
            historical_sequence_sha256s=excluded,
            gold_target=50,
            de_novo_quota=de_novo_quota,
            pepmlm_targeted_enabled=False,
        )
        evidence_by_id = {item.candidate_id: item for item in evidence}
        proposals: list[dict[str, Any]] = []
        for row in plan["actions"]:
            action = parse_evolution_action(row)
            sequence = apply_evolution_action(action, evidence_by_id)
            sequence_sha256 = sha256_text(sequence)
            if sequence_sha256 in excluded:
                raise ValueError(f"{branch_key} proposal repeats historical evidence")
            if sequence_sha256 in {item["sequence_sha256"] for item in proposals}:
                raise ValueError(f"{branch_key} probe emitted a duplicate proposal")
            instability, maximum_hydrophobic_run, net_charge = _sequence_prescreen(sequence)
            proposals.append(
                {
                    "action_sha256": action.action_sha256,
                    "action_type": action.action_type,
                    "operator_id": action.operator_id,
                    "sequence": sequence,
                    "sequence_sha256": sequence_sha256,
                    "length": len(sequence),
                    "guruprasad_instability_index": instability,
                    "maximum_hydrophobic_run": maximum_hydrophobic_run,
                    "hydrophobic_fraction": _hydrophobic_fraction(sequence),
                    "net_charge_ph7_4": net_charge,
                    "instability_score_qualified": instability < 50.0,
                    "historical_exact_replay": False,
                    "score_all_status": "not_started",
                }
            )
        probe_sequence_sha256s.update(item["sequence_sha256"] for item in proposals)
        output = {
            "schema_version": "ampgent.autoresearch-quality-gate-six-branch-result.1",
            "branch_key": branch_key,
            "parent_run_id": str(parent_run_id),
            "parent_run_status": run.status,
            "parent_candidate_count": len(candidates),
            "complete_parent_count": len(evidence),
            "parent_max_generation": generation,
            "parent_delta_count": len(deltas),
            "archive_sha256": snapshot.archive_sha256,
            "archive_member_counts": {
                key: len(value) for key, value in snapshot.archive_members.items()
            },
            "plan_sha256": sha256_json(plan),
            "de_novo_quota": de_novo_quota,
            "proposal_count": len(proposals),
            "proposals": proposals,
            "proposal_semantics": "unmaterialized_score_all_pending",
        }
        if persist:
            now = datetime.now(UTC)
            record = OperationalCallRecord(
                operation_key=(
                    f"six-branch-quality-gate-probe:{operator_release_sha256}:"
                    f"{parent_run_id}"
                ),
                target_key=branch_key,  # type: ignore[arg-type]
                purpose="generation",
                tool_name="autoresearch_quality_gate_proposal_probe",
                tool_version="v1",
                status="succeeded",
                input_payload={
                    "parent_run_id": str(parent_run_id),
                    "parent_temporal_workflow_id": run.temporal_workflow_id,
                    "parent_temporal_run_id": run.temporal_run_id,
                    "required_metric_count": len(required_metrics),
                    "global_historical_exclusion_count": len(
                        globally_excluded_sha256s
                    ),
                },
                parameters={
                    "archive_policy_sha256": archive_policy.sha256(),
                    "archive_sha256": snapshot.archive_sha256,
                    "planner_seed": seed,
                    "de_novo_quota": de_novo_quota,
                    "pepmlm_targeted_enabled": False,
                    "operator_release_sha256": operator_release_sha256,
                },
                execution_context={
                    "source_commit": source_commit,
                    "execution_mode": "local_cpu_rule_only",
                    "temporal_used": False,
                    "historical_run_modified": False,
                },
                output_payload=output,
                queued_at=now,
                started_at=now,
                finished_at=now,
            )
            operational_run, tool_call = await persist_operational_call(session, record)
            await session.commit()
            output["operational_run_id"] = str(operational_run.id)
            output["tool_call_id"] = str(tool_call.id)
        return output


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if len(args.source_commit) != 40 or set(args.source_commit) - set("0123456789abcdef"):
        raise ValueError("source commit must be a lowercase SHA-1")
    planner_path = Path(__file__).parents[1] / "src" / "pepagent" / "autoresearch_planner.py"
    operator_release_sha256 = sha256_json(
        {
            "source_commit": args.source_commit,
            "planner_source_sha256": sha256_bytes(planner_path.read_bytes()),
            "probe_schema": "ampgent.autoresearch-quality-gate-six-branch-probe.1",
        }
    )
    async with SessionFactory() as session:
        globally_excluded_sha256s = set(
            await session.scalars(select(Candidate.sequence_sha256).distinct())
        )
        preexisting_call_count = int(
            await session.scalar(
                select(func.count())
                .select_from(ToolCall)
                .where(ToolCall.tool_name == "autoresearch_quality_gate_proposal_probe")
            )
            or 0
        )
    probe_sequence_sha256s: set[str] = set()
    results = []
    for branch_key, parent_run_id in sorted(PARENT_RUNS.items()):
        results.append(
            await _probe_branch(
                branch_key=branch_key,
                parent_run_id=parent_run_id,
                source_commit=args.source_commit,
                operator_release_sha256=operator_release_sha256,
                globally_excluded_sha256s=globally_excluded_sha256s,
                probe_sequence_sha256s=probe_sequence_sha256s,
                persist=bool(args.persist),
            )
        )
    return {
        "schema_version": "ampgent.autoresearch-quality-gate-six-branch-probe.1",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "source_commit": args.source_commit,
        "operator_release_sha256": operator_release_sha256,
        "persisted": bool(args.persist),
        "historical_sequence_exclusion_count": len(globally_excluded_sha256s),
        "preexisting_probe_tool_call_count": preexisting_call_count,
        "branch_count": len(results),
        "proposal_count": sum(item["proposal_count"] for item in results),
        "globally_unique_probe_proposal_count": len(probe_sequence_sha256s),
        "branches": results,
    }


def main() -> None:
    args = _parser().parse_args()
    print(json.dumps(asyncio.run(_run(args)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
