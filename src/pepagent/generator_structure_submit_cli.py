from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import yaml
from temporalio.client import Client

from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.enums import CandidateStatus
from pepagent.domain.schemas import ExperimentSpec
from pepagent.generator_structure_validation import (
    GeneratorStructureScreenManifest,
    load_frozen_structure_cohort,
)
from pepagent.provenance.hashing import sha256_json
from pepagent.settings import get_settings


async def submit(manifest_path: Path) -> dict[str, object]:
    manifest_text = await asyncio.to_thread(manifest_path.read_text, encoding="utf-8")
    payload = yaml.safe_load(manifest_text)
    manifest = GeneratorStructureScreenManifest.model_validate(payload)
    if manifest.execution_status != "cohort_frozen":
        raise ValueError("structure submission requires cohort_frozen status")
    if not manifest.execution_authorized or manifest.formal_run_limit != 1:
        raise ValueError("v31 formal structure run is not authorized")
    base_dir = manifest_path.parent
    rows = load_frozen_structure_cohort(manifest, base_dir)
    spec_path = Path(manifest.spec_path)
    if not spec_path.is_absolute():
        spec_path = (base_dir / spec_path).resolve()
    spec_text = await asyncio.to_thread(spec_path.read_text, encoding="utf-8")
    spec = ExperimentSpec.model_validate(yaml.safe_load(spec_text))
    if spec.target.accession != manifest.target_accession:
        raise ValueError("target accession differs between v31 manifest and experiment spec")

    settings = get_settings()
    temporal = await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace
    )
    raw_spec = {
        **spec.model_dump(mode="json"),
        "run_mode": "frozen_generator_structure_validation",
        "benchmark_id": manifest.benchmark_id,
        "benchmark_version": manifest.version,
        "manifest_sha256": sha256_json(payload),
        "cohort_sha256": manifest.completion.cohort_sha256,
        "candidate_count": len(rows),
        "pepmlm_used": False,
        "formal_run_limit": manifest.formal_run_limit,
        "implementation_revision": manifest.completion.implementation_revision,
        "implementation_archive_sha256": (
            manifest.completion.implementation_archive_sha256
        ),
    }
    async with SessionFactory() as session, session.begin():
        repository = ExperimentRepository(session)
        run = await repository.create_run(
            spec,
            actor="frozen-generator-structure-validation-cli",
            raw_spec_payload=raw_spec,
        )
        environment_sha256 = sha256_json(
            {"adapter": "frozen-generator-structure-import-v31"}
        )
        import_call = await repository.record_completed_tool_call(
            run.id,
            "frozen-generator-structure-import",
            "v31",
            environment_sha256,
            {
                "cohort_sha256": manifest.completion.cohort_sha256,
                "candidate_count": len(rows),
            },
            {
                "exact_row_order_required": True,
                "exact_sequence_hash_required": True,
                "soft_metrics_used_for_selection": False,
                "pepmlm_used": False,
            },
            {
                "candidate_refs": [
                    {
                        "screening_rank": int(row["screening_rank"]),
                        "source_candidate_id": row["source_candidate_id"],
                        "sequence_sha256": row["sequence_sha256"],
                    }
                    for row in rows
                ]
            },
            model_uri="deterministic://frozen-generator-structure-import-v31",
        )
        staged = []
        for row in rows:
            candidate = await repository.add_candidate(
                run.id,
                row["sequence"],
                generation=0,
                proposal_rank=int(row["screening_rank"]),
                generator_call_id=import_call.id,
                metadata={
                    "benchmark_id": manifest.benchmark_id,
                    "benchmark_version": manifest.version,
                    "generator_id": row["generator_id"],
                    "generator_seed": int(row["generator_seed"]),
                    "source_id": row["source_id"],
                    "source_candidate_id": row["source_candidate_id"],
                    "source_selected_rank": int(row["source_selected_rank"]),
                    "source_sequence_sha256": row["sequence_sha256"],
                    "cohort_sha256": manifest.completion.cohort_sha256,
                    "pepmlm_used": False,
                },
                actor="frozen-generator-structure-import",
            )
            await repository.transition_candidate(
                candidate.id,
                CandidateStatus.STRUCTURE_QUEUED,
                "frozen-generator-structure-import",
                "frozen target-blind generator cohort queued for common structure protocol",
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
    workflow_id = f"pepagent-generator-structure-v31-{run_id}"
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
        "cohort_sha256": manifest.completion.cohort_sha256,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit the frozen v31 structure cohort")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(submit(args.manifest.resolve())),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
