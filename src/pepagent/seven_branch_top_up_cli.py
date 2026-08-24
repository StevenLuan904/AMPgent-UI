from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from pepagent.db.models import (
    AgentDecision,
    Candidate,
    CandidateOccurrence,
    Evaluation,
    ExperimentRun,
)
from pepagent.db.session import SessionFactory
from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.sequence_family import cluster_sequence_families
from pepagent.seven_branch_design import (
    SEQUENCE_METRICS,
    BranchProgress,
    SevenBranchDesignContract,
    delivery_eligible_candidate_ids,
)
from pepagent.seven_branch_reservation_cli import (
    reserve_seven_branch_top_up_schedule,
    submit_reserved_seven_branch_top_up_schedule,
)
from pepagent.seven_branch_schedule import (
    build_top_up_seven_branch_schedule,
    derive_top_up_seven_branch_run_ids,
)
from pepagent.storage.object_store import ContentAddressedObjectStore


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


async def _initial_branch_snapshot(
    *, child: ExperimentRun, contract: SevenBranchDesignContract
) -> dict[str, Any]:
    branch_key = str(child.spec_json["branch_key"])
    branch = next(item for item in contract.branches if item.branch_key == branch_key)
    async with SessionFactory() as session:
        candidates = list(
            await session.scalars(
                select(Candidate)
                .where(Candidate.run_id == child.id)
                .order_by(Candidate.id)
            )
        )
        candidate_ids = [item.id for item in candidates]
        evaluations = list(
            await session.scalars(
                select(Evaluation).where(Evaluation.candidate_id.in_(candidate_ids))
            )
        ) if candidate_ids else []
        decisions = list(
            await session.scalars(
                select(AgentDecision)
                .where(
                    AgentDecision.run_id == child.id,
                    AgentDecision.decision_type
                    == "v38_sequence_maturity_admission",
                )
                .order_by(AgentDecision.created_at.desc())
            )
        )
        if not candidates or not decisions:
            raise ValueError(f"branch child lacks scored admission evidence: {child.id}")
        admission_payload = json.loads(decisions[0].response_text)
        admission = admission_payload["admission"]
        qualified_ids = set(delivery_eligible_candidate_ids(admission))
        sequence_coverage = {item.id: set() for item in candidates}
        target_coverage = {item.id: set() for item in candidates}
        for row in evaluations:
            if row.status != "succeeded":
                continue
            if row.metric_name in SEQUENCE_METRICS:
                sequence_coverage[row.candidate_id].add(row.metric_name)
            if row.metric_name in {"conditional_nll", "conditional_ppl"} and (
                branch.target_sequence_sha256 is not None
                and row.raw_json.get("target", {}).get("sequence_sha256")
                == branch.target_sequence_sha256
            ):
                target_coverage[row.candidate_id].add(row.metric_name)
        fully_scored = sum(
            item == set(SEQUENCE_METRICS) for item in sequence_coverage.values()
        )
        target_scored = (
            sum(
                item == {"conditional_nll", "conditional_ppl"}
                for item in target_coverage.values()
            )
            if branch.target_sequence_interaction_required
            else 0
        )
        if fully_scored != len(candidates) or (
            branch.target_sequence_interaction_required
            and target_scored != len(candidates)
        ):
            raise ValueError(f"branch score-all evidence is incomplete: {child.id}")
        qualified_sequences = [
            item.sequence for item in candidates if item.id in qualified_ids
        ]
        family_count = len(
            {item.family_key for item in cluster_sequence_families(qualified_sequences)}
        )
        raw_count = int(
            await session.scalar(
                select(func.count(CandidateOccurrence.id)).where(
                    CandidateOccurrence.run_id == child.id
                )
            )
            or 0
        )
    progress = BranchProgress(
        branch_key=branch_key,
        raw_count=raw_count,
        valid_unique_count=len(candidates),
        fully_scored_count=fully_scored,
        target_sequence_scored_count=target_scored,
        qualified_count=len(qualified_ids),
        delivered_count=min(len(qualified_ids), branch.requested_delivery_count),
        family_count=family_count,
    )
    return {
        "schema_version": "ampgent.seven-branch-prior-evidence-snapshot.1",
        "branch_key": branch_key,
        "source_run_ids": [str(child.id)],
        "progress": progress.model_dump(mode="json"),
        "next_round_ordinal": int(child.spec_json["round_ordinal"]) + 1,
        "admission_decision_id": str(decisions[0].id),
        "admission_response_sha256": decisions[0].response_sha256,
    }


async def build_top_up_branch_evidence(
    *, controller_run_id: uuid.UUID, contract: SevenBranchDesignContract
) -> dict[str, dict[str, Any]]:
    async with SessionFactory() as session:
        controller = await session.get(ExperimentRun, controller_run_id)
        if controller is None or controller.status != "succeeded":
            raise ValueError("seven-branch controller is not durably succeeded")
        descendants = list(
            await session.scalars(
                select(ExperimentRun)
                .where(ExperimentRun.parent_run_id == controller_run_id)
                .order_by(ExperimentRun.created_at, ExperimentRun.id)
            )
        )
        children = [
            item
            for item in descendants
            if item.spec_json.get("run_kind") == "seven_branch_design_round"
        ]
        if not children or any(item.status != "succeeded" for item in children):
            raise ValueError("seven-branch child runs are not durably succeeded")
        cumulative_decisions = list(
            await session.scalars(
                select(AgentDecision).where(
                    AgentDecision.run_id == controller_run_id,
                    AgentDecision.decision_type.like("seven_branch_delivery:%"),
                )
            )
        )
    snapshots: list[dict[str, Any]] = []
    if cumulative_decisions:
        snapshots = [item.structured_json for item in cumulative_decisions]
    else:
        snapshots = [
            await _initial_branch_snapshot(child=item, contract=contract)
            for item in children
        ]
    object_store = await asyncio.to_thread(ContentAddressedObjectStore)
    incomplete: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        progress = BranchProgress.model_validate(snapshot["progress"])
        branch = next(
            item for item in contract.branches if item.branch_key == progress.branch_key
        )
        if progress.delivered_count >= branch.requested_delivery_count:
            continue
        normalized = {
            "schema_version": "ampgent.seven-branch-prior-evidence-snapshot.1",
            "branch_key": progress.branch_key,
            "source_run_ids": snapshot["source_run_ids"],
            "progress": progress.model_dump(mode="json"),
            "next_round_ordinal": int(
                snapshot.get(
                    "next_round_ordinal",
                    snapshot.get("top_up_plan", {}).get("next_round_ordinal"),
                )
            ),
            "source_snapshot_sha256": sha256_json(snapshot),
        }
        stored = await asyncio.to_thread(
            object_store.put_bytes,
            json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            "application/json",
        )
        incomplete[progress.branch_key] = {
            **normalized,
            "snapshot_sha256": stored.sha256,
        }
    return incomplete


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze and exact-once submit one seven-branch top-up epoch"
    )
    parser.add_argument("--parent-controller-run-id", required=True)
    parser.add_argument("--epoch-ordinal", type=int, required=True)
    parser.add_argument("--request-template", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--design-contract", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        raise SystemExit("seven-branch top-up is inert without explicit --execute")

    async def execute() -> dict[str, Any]:
        request_template = _load_json(args.request_template.resolve())
        preflight = _load_json(args.preflight.resolve())
        contract = SevenBranchDesignContract.model_validate(
            _load_json(args.design_contract.resolve())
        )
        manifest_path = args.target_manifest.resolve()
        manifest = _load_json(manifest_path)
        parent = uuid.UUID(args.parent_controller_run_id)
        evidence = await build_top_up_branch_evidence(
            controller_run_id=parent, contract=contract
        )
        if not evidence:
            return {"status": "all_branch_quotas_complete", "submitted": False}
        controller, child_ids = derive_top_up_seven_branch_run_ids(
            parent_controller_run_id=parent,
            epoch_ordinal=args.epoch_ordinal,
            branch_evidence_sha256_by_key={
                key: value["snapshot_sha256"] for key, value in evidence.items()
            },
        )
        schedule = build_top_up_seven_branch_schedule(
            request_template=request_template,
            submission_preflight=preflight,
            design_contract=contract,
            target_manifest=manifest,
            target_manifest_sha256=sha256_file(manifest_path),
            parent_controller_run_id=parent,
            controller_run_id=controller,
            epoch_ordinal=args.epoch_ordinal,
            branch_evidence=evidence,
            child_run_ids_by_key=child_ids,
        )
        reservation = await reserve_seven_branch_top_up_schedule(
            schedule=schedule, target_manifest=manifest
        )
        if not args.submit:
            return {"evidence": evidence, "reservation": reservation}
        submission = await submit_reserved_seven_branch_top_up_schedule(
            schedule=schedule
        )
        return {
            "evidence": evidence,
            "reservation": reservation,
            "submission": submission,
        }

    print(
        json.dumps(
            asyncio.run(execute()), ensure_ascii=False, indent=2, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
