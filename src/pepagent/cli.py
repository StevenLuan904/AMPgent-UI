import asyncio
import importlib.metadata
import json
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import typer
import yaml
from sqlalchemy import select
from temporalio.client import Client

from pepagent.db.models import Artifact, EvidenceArtifact
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.enums import CandidateStatus
from pepagent.domain.schemas import ExperimentSpec, PocketCatalogSpec
from pepagent.pockets.catalog import import_pocket_catalog
from pepagent.provenance.hashing import sha256_bytes, sha256_json
from pepagent.registry.service import register_local_model_release
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore
from pepagent.structures.pdb import atom_chain_sequence

app = typer.Typer(no_args_is_help=True, help="Operate the PepAgent control plane.")


def _load_mapping(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return yaml.safe_load(text)


@app.command()
def submit(
    spec_path: Path,
    api: str = typer.Option("http://127.0.0.1:8080", help="Control-plane base URL"),
) -> None:
    """Validate an experiment specification and submit a durable run."""
    spec = ExperimentSpec.model_validate(_load_mapping(spec_path))
    response = httpx.post(
        f"{api}/v1/runs", json=spec.model_dump(mode="json"), timeout=30.0
    )
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command("run")
def show_run(
    run_id: str,
    api: str = typer.Option("http://127.0.0.1:8080", help="Control-plane base URL"),
) -> None:
    """Show canonical run state and append-only lifecycle events."""
    response = httpx.get(f"{api}/v1/runs/{run_id}", timeout=15.0)
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False, indent=2))


@app.command()
def health(
    api: str = typer.Option("http://127.0.0.1:8080", help="Control-plane base URL"),
) -> None:
    """Check whether the API and its Temporal connection completed startup."""
    response = httpx.get(f"{api}/healthz", timeout=10.0)
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False))


@app.command("register-pepmlm")
def register_pepmlm(model_dir: Path) -> None:
    """Verify and register the pinned PepMLM release in MinIO, PostgreSQL and MLflow."""
    settings = get_settings()
    result = asyncio.run(
        register_local_model_release(
            name="PepMLM-650M",
            role="conditional_generator_and_pseudo_perplexity",
            release_dir=model_dir,
            source_uri="https://huggingface.co/ChatterjeeLab/PepMLM-650M",
            source_revision=settings.pepmlm_model_revision,
            expected_weights_sha256=settings.pepmlm_weights_sha256,
            adapter_version="pepagent-pepmlm-cli-v1",
            admission_status="admitted",
        )
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("register-boltz2")
def register_boltz2(release_dir: Path) -> None:
    """Register the structure-only Boltz-2 release after verifying its checkpoint."""
    settings = get_settings()
    result = asyncio.run(
        register_local_model_release(
            name="Boltz-2-structure",
            role="peptide_protein_complex_structure_confidence",
            release_dir=release_dir,
            source_uri="https://huggingface.co/boltz-community/boltz-2",
            source_revision=settings.boltz2_revision,
            expected_weights_sha256=settings.boltz2_weights_sha256,
            weights_filename="boltz2_conf.ckpt",
            adapter_version="pepagent-boltz2-cli-v2-affinity-hard-disabled",
            admission_status="admitted",
        )
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("import-pockets")
def import_pockets(catalog_path: Path) -> None:
    """Import a versioned, multi-source target-pocket evidence catalog."""
    catalog = PocketCatalogSpec.model_validate(_load_mapping(catalog_path))

    async def _run() -> dict:
        async with SessionFactory() as session, session.begin():
            return await import_pocket_catalog(session, catalog)

    result = asyncio.run(_run())
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command("submit-rosetta-validation")
def submit_rosetta_validation(
    suite_path: Path,
    nstruct: int | None = typer.Option(
        None, min=200, help="Override production decoy count; never below 200."
    ),
) -> None:
    """Stage public complexes as immutable evidence and launch durable Rosetta runs."""
    suite = _load_mapping(suite_path)
    suite_digest = sha256_bytes(suite_path.read_bytes())
    production_nstruct = nstruct or int(suite["source_policy"]["production_nstruct"])
    if production_nstruct < 200:
        raise typer.BadParameter("decision-bearing validation requires at least 200 decoys")

    async def _run() -> list[dict]:
        settings = get_settings()
        temporal = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        staged: list[dict] = []
        with TemporaryDirectory(prefix="pepagent-rosetta-validation-") as temporary:
            temporary_root = Path(temporary)
            for case in suite["cases"]:
                response = await asyncio.to_thread(
                    httpx.get, case["source_uri"], timeout=60.0
                )
                response.raise_for_status()
                source_bytes = response.content
                actual_sha256 = sha256_bytes(source_bytes)
                if actual_sha256 != case["source_sha256"]:
                    raise OSError(
                        f"{case['pdb_id']} source hash mismatch: "
                        f"{actual_sha256} != {case['source_sha256']}"
                    )
                source_path = temporary_root / f"{case['pdb_id']}.pdb"
                source_path.write_bytes(source_bytes)
                receptor_sequence = atom_chain_sequence(
                    source_path, list(case["receptor_chains"])
                )
                peptide_sequence = atom_chain_sequence(source_path, [case["peptide_chain"]])
                if peptide_sequence != case["modeled_peptide_sequence"]:
                    raise ValueError(
                        f"{case['pdb_id']} modeled peptide changed: {peptide_sequence}"
                    )
                stored = await asyncio.to_thread(
                    ContentAddressedObjectStore().put_bytes,
                    source_bytes,
                    "chemical/x-pdb",
                )
                spec = ExperimentSpec(
                    target={
                        "name": f"{case['pdb_id']} modeled receptor",
                        "sequence": receptor_sequence,
                        "accession": case["pdb_id"],
                        "source_database": "RCSB PDB",
                        "source_uri": case["source_uri"],
                        "source_version": actual_sha256,
                    },
                    peptide_lengths=[len(peptide_sequence)],
                    candidates_per_length=1,
                    structure_top_k=1,
                    generations=1,
                    seed=int(suite["source_policy"]["seed"]),
                    use_msa_server=False,
                    rosetta_enabled=True,
                    rosetta_top_k=1,
                    rosetta_nstruct=production_nstruct,
                    rosetta_parallel_decoys=int(
                        suite["source_policy"].get("parallel_decoys", 1)
                    ),
                    rosetta_pair_iptm_min=0,
                    rosetta_score_function=suite["source_policy"]["score_function"],
                )
                raw_spec = spec.model_dump(mode="json")
                raw_spec["validation"] = {
                    "suite_id": suite["suite_id"],
                    "suite_sha256": suite_digest,
                    "case": case,
                }
                async with SessionFactory() as session, session.begin():
                    repository = ExperimentRepository(session)
                    run = await repository.create_run(
                        spec,
                        actor="rosetta-validation-cli",
                        raw_spec_payload=raw_spec,
                    )
                    candidate = await repository.add_candidate(
                        run.id,
                        peptide_sequence,
                        generation=0,
                        proposal_rank=1,
                        metadata={
                            "validation_suite": suite["suite_id"],
                            "pdb_id": case["pdb_id"],
                            "native_start": True,
                        },
                    )
                    await repository.transition_candidate(
                        candidate.id,
                        CandidateStatus.ROSETTA_QUEUED,
                        "rosetta-validation-cli",
                        "public native complex staged for protocol validation",
                    )
                    retrieval_environment = sha256_json(
                        {
                            "adapter": "pepagent-rcsb-pdb-retrieval-v1",
                            "httpx": importlib.metadata.version("httpx"),
                        }
                    )
                    source_call = await repository.record_completed_tool_call(
                        run.id,
                        "rcsb-pdb-retrieval",
                        "v1",
                        retrieval_environment,
                        {
                            "source_uri": case["source_uri"],
                            "expected_sha256": case["source_sha256"],
                        },
                        {"exact_hash_required": True},
                        {
                            "sha256": stored.sha256,
                            "size_bytes": stored.size_bytes,
                            "storage_uri": stored.uri,
                        },
                    )
                    artifact = await session.scalar(
                        select(Artifact).where(Artifact.sha256 == stored.sha256)
                    )
                    if artifact is None:
                        artifact = Artifact(
                            sha256=stored.sha256,
                            size_bytes=stored.size_bytes,
                            media_type=stored.media_type,
                            storage_uri=stored.uri,
                            metadata_json={
                                "source": "RCSB PDB",
                                "pdb_id": case["pdb_id"],
                            },
                        )
                        session.add(artifact)
                        await session.flush()
                    link = await session.get(
                        EvidenceArtifact,
                        {
                            "tool_call_id": source_call.id,
                            "artifact_id": artifact.id,
                            "role": "source_complex",
                        },
                    )
                    if link is None:
                        session.add(
                            EvidenceArtifact(
                                tool_call_id=source_call.id,
                                artifact_id=artifact.id,
                                role="source_complex",
                            )
                        )
                workflow_id = (
                    f"rosetta-validation-{suite['suite_id']}-{case['pdb_id']}-{run.id}"
                )
                await temporal.start_workflow(
                    "RosettaValidationWorkflow",
                    {
                        "run_id": str(run.id),
                        "spec": spec.model_dump(mode="json"),
                        "validation_case": case,
                        "structure": {
                            "candidate": {
                                "id": str(candidate.id),
                                "sequence": candidate.sequence,
                            },
                            "tool_call_id": str(source_call.id),
                            "provenance": {
                                "engine_artifacts": [
                                    {"path": f"{case['pdb_id']}.pdb", **asdict(stored)}
                                ]
                            },
                        },
                    },
                    id=workflow_id,
                    task_queue="pepagent-control",
                )
                staged.append(
                    {
                        "pdb_id": case["pdb_id"],
                        "run_id": str(run.id),
                        "candidate_id": str(candidate.id),
                        "source_tool_call_id": str(source_call.id),
                        "workflow_id": workflow_id,
                        "source_sha256": actual_sha256,
                    }
                )
        return staged

    result = asyncio.run(_run())
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()
