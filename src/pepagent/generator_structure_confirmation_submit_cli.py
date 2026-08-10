from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
from typing import Any

import yaml
from temporalio.client import Client

from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.enums import CandidateStatus
from pepagent.domain.schemas import ExperimentSpec
from pepagent.generator_structure_validation import sha256_file
from pepagent.provenance.hashing import sha256_json
from pepagent.settings import get_settings


def _load_contract(
    manifest_path: Path,
) -> tuple[dict[str, Any], list[dict[str, str]], ExperimentSpec]:
    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if payload["benchmark_id"] != "amp_generator_target_structure_v31b":
        raise ValueError("unexpected confirmation benchmark")
    if payload["execution_status"] != "ready_for_single_formal_run":
        raise ValueError("v31b is not ready for formal submission")
    execution = payload["execution"]
    if execution["formal_run_limit"] != 1 or not execution["execution_authorized"]:
        raise ValueError("v31b single formal run is not authorized")
    frozen = payload["frozen_selection"]
    cohort_path = (manifest_path.parent / frozen["cohort_path"]).resolve()
    if sha256_file(cohort_path) != frozen["cohort_sha256"]:
        raise ValueError("v31b cohort SHA mismatch")
    audit_path = (manifest_path.parent / frozen["audit_path"]).resolve()
    if sha256_file(audit_path) != frozen["audit_sha256"]:
        raise ValueError("v31b audit SHA mismatch")
    with cohort_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 18 or len({row["sequence_sha256"] for row in rows}) != 18:
        raise ValueError("v31b cohort must contain 18 unique rows")
    if [int(row["confirmation_rank"]) for row in rows] != list(range(1, 19)):
        raise ValueError("v31b confirmation rank/order contract is broken")
    counts = {generator: 0 for generator in ("hydramp", "ampgan_v2", "amp_designer")}
    for row in rows:
        counts[row["generator_id"]] += 1
    if set(counts.values()) != {6}:
        raise ValueError(f"v31b generator balance is broken: {counts}")

    protocol = payload["confirmation_protocol"]
    spec_path = (manifest_path.parent / protocol["target_spec_path"]).resolve()
    spec = ExperimentSpec.model_validate(yaml.safe_load(spec_path.read_text(encoding="utf-8")))
    expected_seeds = [int(seed) for seed in protocol["independent_boltz_seeds"]]
    if spec.boltz_seed_values != expected_seeds or spec.boltz_seeds_per_candidate != 3:
        raise ValueError("v31b Boltz seed contract differs from preregistration")
    if not spec.rosetta_all_boltz_samples or spec.rosetta_nstruct != 16:
        raise ValueError("v31b all-pose Rosetta contract differs from preregistration")
    if spec.target.accession != payload["target_accession"]:
        raise ValueError("v31b target accession mismatch")
    return payload, rows, spec


async def submit(manifest_path: Path) -> dict[str, object]:
    payload, rows, spec = _load_contract(manifest_path)
    settings = get_settings()
    temporal = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    frozen = payload["frozen_selection"]
    raw_spec = {
        **spec.model_dump(mode="json"),
        "run_mode": "frozen_generator_structure_confirmation",
        "benchmark_id": payload["benchmark_id"],
        "benchmark_version": payload["version"],
        "manifest_sha256": sha256_json(payload),
        "cohort_sha256": frozen["cohort_sha256"],
        "candidate_count": len(rows),
        "pepmlm_used": False,
        "formal_run_limit": 1,
        "selection_revision": frozen["selection_revision"],
    }
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        run = await repository.create_run(
            spec,
            actor="frozen-generator-structure-confirmation-cli",
            raw_spec_payload=raw_spec,
        )
        import_call = await repository.record_completed_tool_call(
            run.id,
            "frozen-generator-structure-import",
            "v31b",
            sha256_json({"adapter": "frozen-generator-structure-import-v31b"}),
            {"cohort_sha256": frozen["cohort_sha256"], "candidate_count": len(rows)},
            {
                "exact_row_order_required": True,
                "exact_sequence_hash_required": True,
                "soft_metrics_used_for_selection": False,
                "pepmlm_used": False,
            },
            {
                "candidate_refs": [
                    {
                        "confirmation_rank": int(row["confirmation_rank"]),
                        "phase_a_candidate_id": row["candidate_id"],
                        "sequence_sha256": row["sequence_sha256"],
                    }
                    for row in rows
                ]
            },
            model_uri="deterministic://frozen-generator-structure-import-v31b",
        )
        staged = []
        for row in rows:
            candidate = await repository.add_candidate(
                run.id,
                row["sequence"],
                generation=0,
                proposal_rank=int(row["confirmation_rank"]),
                generator_call_id=import_call.id,
                metadata={
                    "benchmark_id": payload["benchmark_id"],
                    "benchmark_version": payload["version"],
                    "generator_id": row["generator_id"],
                    "phase_a_candidate_id": row["candidate_id"],
                    "phase_a_screening_rank": int(row["phase_a_screening_rank"]),
                    "pareto_front": int(row["pareto_front"]),
                    "cohort_sha256": frozen["cohort_sha256"],
                    "pepmlm_used": False,
                },
                actor="frozen-generator-structure-confirmation-import",
            )
            await repository.transition_candidate(
                candidate.id,
                CandidateStatus.STRUCTURE_QUEUED,
                "frozen-generator-structure-confirmation-import",
                "frozen v31b confirmation cohort queued for common multi-seed protocol",
            )
            staged.append(
                {
                    "id": str(candidate.id),
                    "sequence": candidate.sequence,
                    "sequence_sha256": candidate.sequence_sha256,
                    "generation": 0,
                }
            )
        run_id = str(run.id)
    workflow_id = f"pepagent-generator-structure-v31b-{run_id}"
    await temporal.start_workflow(
        "CandidateStructureValidationWorkflow",
        {"run_id": run_id, "spec": spec.model_dump(mode="json"), "candidates": staged},
        id=workflow_id,
        task_queue="pepagent-control",
    )
    return {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "candidate_count": len(staged),
        "cohort_sha256": frozen["cohort_sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit the frozen v31b confirmation cohort")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(submit(args.manifest.resolve())), indent=2))


if __name__ == "__main__":
    main()
