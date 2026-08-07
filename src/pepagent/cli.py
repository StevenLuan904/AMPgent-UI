import asyncio
import importlib.metadata
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import typer
import yaml
from sqlalchemy import select
from temporalio.client import Client

from pepagent.db.models import (
    Artifact,
    Candidate,
    Evaluation,
    EvidenceArtifact,
    ExperimentRun,
    Target,
)
from pepagent.db.repository import ExperimentRepository
from pepagent.db.session import SessionFactory
from pepagent.domain.enums import CandidateStatus, MetricName
from pepagent.domain.schemas import ExperimentSpec, PocketCatalogSpec
from pepagent.pockets.catalog import import_pocket_catalog
from pepagent.provenance.hashing import sha256_bytes, sha256_json, sha256_text
from pepagent.registry.service import register_local_model_release
from pepagent.reporting import build_bulk_rosetta_rows, render_bulk_rosetta_csv
from pepagent.settings import get_settings
from pepagent.storage.object_store import ContentAddressedObjectStore
from pepagent.structures.pdb import atom_chain_sequence
from pepagent.validation.handoff import validate_handoff_metric_control
from pepagent.validation.rosetta import (
    summarize_native_start_validation,
    validate_rosetta_protocol_policy,
)

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


@app.command("export-bulk-rosetta-report")
def export_bulk_rosetta_report(
    output_path: Path,
    minimum_rows: int = typer.Option(200, min=1, help="Natural accumulation milestone."),
    allow_partial: bool = typer.Option(
        False, help="Export before the milestone for an explicitly requested interim snapshot."
    ),
) -> None:
    """Export unique, protocol-compatible completed Rosetta rows accumulated across runs."""

    async def _run() -> list[dict]:
        async with SessionFactory() as session:
            pairs = (
                await session.execute(
                    select(Candidate, Evaluation)
                    .join(Evaluation, Evaluation.candidate_id == Candidate.id)
                    .where(Evaluation.metric_name == MetricName.ROSETTA_DG_SEPARATED_REU)
                    .order_by(Evaluation.created_at.desc(), Evaluation.id.desc())
                )
            ).all()
            chosen_by_sequence: dict[str, tuple[Candidate, Evaluation]] = {}
            for candidate, evaluation in pairs:
                raw = evaluation.raw_json
                compatible = (
                    evaluation.numeric_value is not None
                    and raw.get("adapter_version") == "pepagent-pyrosetta-flexpepdock-v3"
                    and bool(raw.get("prepacked_input_sha256"))
                    and raw.get("pack_input") is False
                    and raw.get("pack_separated") is False
                )
                if compatible:
                    chosen_by_sequence.setdefault(candidate.sequence, (candidate, evaluation))
            candidates = [candidate for candidate, _ in chosen_by_sequence.values()]
            chosen_dg_evaluation_ids = {
                evaluation.id for _, evaluation in chosen_by_sequence.values()
            }
            candidate_ids = [candidate.id for candidate in candidates]
            evaluations = (
                list(
                    await session.scalars(
                        select(Evaluation)
                        .where(Evaluation.candidate_id.in_(candidate_ids))
                        .order_by(Evaluation.created_at, Evaluation.id)
                    )
                )
                if candidate_ids
                else []
            )
            evaluations = [
                evaluation
                for evaluation in evaluations
                if evaluation.metric_name != MetricName.ROSETTA_DG_SEPARATED_REU
                or evaluation.id in chosen_dg_evaluation_ids
            ]
            return build_bulk_rosetta_rows(candidates, evaluations)

    rows = asyncio.run(_run())
    if len(rows) < minimum_rows and not allow_partial:
        raise typer.BadParameter(
            f"only {len(rows)} unique compatible rows are complete; "
            f"the reporting milestone is {minimum_rows}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(render_bulk_rosetta_csv(rows))
    typer.echo(
        json.dumps(
            {"output_path": str(output_path.resolve()), "row_count": len(rows)},
            ensure_ascii=False,
            indent=2,
        )
    )


@app.command()
def health(
    api: str = typer.Option("http://127.0.0.1:8080", help="Control-plane base URL"),
) -> None:
    """Check whether the API and its Temporal connection completed startup."""
    response = httpx.get(f"{api}/healthz", timeout=10.0)
    response.raise_for_status()
    typer.echo(json.dumps(response.json(), ensure_ascii=False))


@app.command("submit-candidate-structure-validation")
def submit_candidate_structure_validation(manifest_path: Path) -> None:
    """Import an immutable candidate cohort and run durable Boltz/Rosetta validation."""
    manifest = _load_mapping(manifest_path)
    spec_path = Path(manifest["spec_path"])
    if not spec_path.is_absolute():
        spec_path = manifest_path.parent / spec_path
    spec = ExperimentSpec.model_validate(_load_mapping(spec_path.resolve()))
    source_run_id = uuid.UUID(manifest["source_run_id"])
    source_candidate_ids = [uuid.UUID(value) for value in manifest["source_candidate_ids"]]

    async def _run() -> dict:
        settings = get_settings()
        temporal = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        async with SessionFactory() as session, session.begin():
            source_run = await session.get(ExperimentRun, source_run_id)
            if source_run is None:
                raise KeyError(f"source run not found: {source_run_id}")
            source_target = await session.get(Target, source_run.target_id)
            target_mismatch = source_target is None or (
                source_target.sequence_sha256 != sha256_text(spec.target.sequence)
            )
            if target_mismatch:
                raise ValueError(
                    "source candidates and validation spec must reference the same target"
                )
            source_candidates = list(
                await session.scalars(
                    select(Candidate)
                    .where(
                        Candidate.run_id == source_run_id,
                        Candidate.id.in_(source_candidate_ids),
                    )
                    .order_by(Candidate.proposal_rank, Candidate.id)
                )
            )
            found = {candidate.id for candidate in source_candidates}
            missing = [str(value) for value in source_candidate_ids if value not in found]
            if missing:
                raise KeyError(f"source candidates not found in source run: {missing}")
            source_candidates.sort(key=lambda item: source_candidate_ids.index(item.id))
            source_refs = [
                {
                    "source_run_id": str(source_run_id),
                    "source_candidate_id": str(candidate.id),
                    "sequence": candidate.sequence,
                    "sequence_sha256": candidate.sequence_sha256,
                }
                for candidate in source_candidates
            ]
            raw_spec = {
                **spec.model_dump(mode="json"),
                "run_mode": "candidate_structure_validation",
                "source_candidates": source_refs,
                "manifest_sha256": sha256_json(manifest),
            }
            repository = ExperimentRepository(session)
            run = await repository.create_run(
                spec,
                actor="candidate-structure-validation-cli",
                parent_run_id=source_run_id,
                raw_spec_payload=raw_spec,
            )
            environment_sha256 = sha256_json(
                {"adapter": "candidate-structure-validation-import-v1"}
            )
            import_call = await repository.record_completed_tool_call(
                run.id,
                "candidate-structure-validation-import",
                "v1",
                environment_sha256,
                {"source_candidates": source_refs},
                {"exact_sequence_hash_required": True, "same_target_required": True},
                {"imported_candidates": source_refs},
                model_uri="deterministic://candidate-structure-validation-import",
            )
            staged: list[dict] = []
            for rank, source in enumerate(source_candidates):
                candidate = await repository.add_candidate(
                    run.id,
                    source.sequence,
                    generation=0,
                    proposal_rank=rank,
                    generator_call_id=import_call.id,
                    metadata={
                        "source_run_id": str(source_run_id),
                        "source_candidate_id": str(source.id),
                        "source_sequence_sha256": source.sequence_sha256,
                        "import_tool_call_id": str(import_call.id),
                    },
                    actor="candidate-structure-validation-import",
                )
                staged.append(
                    {
                        "id": str(candidate.id),
                        "sequence": candidate.sequence,
                        "sequence_sha256": candidate.sequence_sha256,
                        "generation": 0,
                    }
                )
        workflow_id = f"pepagent-structure-validation-{run.id}"
        await temporal.start_workflow(
            "CandidateStructureValidationWorkflow",
            {"run_id": str(run.id), "spec": spec.model_dump(mode="json"), "candidates": staged},
            id=workflow_id,
            task_queue="pepagent-control",
        )
        return {"run_id": str(run.id), "workflow_id": workflow_id, "candidates": staged}

    typer.echo(json.dumps(asyncio.run(_run()), ensure_ascii=False, indent=2))


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


@app.command("submit-sequence-binding-proxy-calibration")
def submit_sequence_binding_proxy_calibration(manifest_path: Path) -> None:
    """Import a fixed cohort and run the low-confidence target-conditioned PepMLM proxy."""
    manifest = _load_mapping(manifest_path)
    spec_path = Path(manifest["spec_path"])
    if not spec_path.is_absolute():
        spec_path = manifest_path.parent / spec_path
    spec = ExperimentSpec.model_validate(_load_mapping(spec_path.resolve()))
    parent_run_id = uuid.UUID(manifest["parent_run_id"])

    targets: list[dict] = []
    for index, raw in enumerate(manifest["target_panel"]):
        sequence = "".join(str(raw["sequence"]).split()).upper()
        sequence_sha256 = sha256_text(sequence)
        if sequence_sha256 != raw["sequence_sha256"]:
            raise typer.BadParameter(f"target_panel[{index}] sequence SHA-256 mismatch")
        control_type = raw["control_type"]
        if control_type not in {"primary", "unrelated", "composition_shuffle"}:
            raise typer.BadParameter(f"target_panel[{index}] has invalid control_type")
        targets.append(
            {
                "accession": str(raw["accession"]),
                "sequence": sequence,
                "sequence_sha256": sequence_sha256,
                "control_type": control_type,
                "source": raw.get("source"),
                "source_version": raw.get("source_version"),
            }
        )
    primary = [target for target in targets if target["control_type"] == "primary"]
    decoys = [target for target in targets if target["control_type"] != "primary"]
    if len(primary) != 1 or primary[0]["sequence"] != spec.target.sequence:
        raise typer.BadParameter(
            "target panel must contain exactly one primary matching spec.target"
        )
    if len(decoys) < 2:
        raise typer.BadParameter("target panel requires at least two decoy targets")
    if len({target["sequence_sha256"] for target in targets}) != len(targets):
        raise typer.BadParameter("target panel sequences must be unique")
    target_panel_sha256 = sha256_json(targets)

    candidate_entries: list[dict] = []
    for index, raw in enumerate(manifest["candidates"]):
        sequence = "".join(str(raw["sequence"]).split()).upper()
        sequence_sha256 = sha256_text(sequence)
        if sequence_sha256 != raw["sequence_sha256"]:
            raise typer.BadParameter(f"candidates[{index}] sequence SHA-256 mismatch")
        source_run_id = raw.get("source_run_id")
        source_candidate_id = raw.get("source_candidate_id")
        if bool(source_run_id) != bool(source_candidate_id):
            raise typer.BadParameter(
                f"candidates[{index}] must provide both source IDs or neither"
            )
        candidate_entries.append(
            {
                "sequence": sequence,
                "sequence_sha256": sequence_sha256,
                "source_run_id": source_run_id,
                "source_candidate_id": source_candidate_id,
                "cohort_role": str(raw["cohort_role"]),
            }
        )
    if len({item["sequence_sha256"] for item in candidate_entries}) != len(candidate_entries):
        raise typer.BadParameter("calibration candidate sequences must be unique")

    async def _run() -> dict:
        settings = get_settings()
        temporal = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        async with SessionFactory() as session, session.begin():
            parent_run = await session.get(ExperimentRun, parent_run_id)
            if parent_run is None:
                raise KeyError(f"parent run not found: {parent_run_id}")
            parent_target = await session.get(Target, parent_run.target_id)
            if parent_target is None or parent_target.sequence_sha256 != sha256_text(
                spec.target.sequence
            ):
                raise ValueError("parent run and proxy calibration spec must share the target")
            for entry in candidate_entries:
                if not entry["source_candidate_id"]:
                    continue
                source = await session.get(Candidate, uuid.UUID(entry["source_candidate_id"]))
                if (
                    source is None
                    or source.run_id != uuid.UUID(entry["source_run_id"])
                    or source.sequence_sha256 != entry["sequence_sha256"]
                ):
                    raise ValueError(
                        "source candidate identity or sequence mismatch: "
                        f"{entry['source_candidate_id']}"
                    )
                source_run = await session.get(ExperimentRun, source.run_id)
                if source_run is None or source_run.target_id != parent_run.target_id:
                    raise ValueError("source candidate target differs from calibration target")

            raw_spec = {
                **spec.model_dump(mode="json"),
                "run_mode": "sequence_binding_proxy_calibration",
                "parent_run_id": str(parent_run_id),
                "target_panel": targets,
                "target_panel_sha256": target_panel_sha256,
                "calibration_candidates": candidate_entries,
                "manifest_sha256": sha256_json(manifest),
                "scientific_contract": {
                    "confidence": "low",
                    "rank_only": True,
                    "admission_status": "out_of_domain",
                    "not_binding_probability": True,
                    "not_affinity": True,
                    "cannot_override_structure_evidence": True,
                    "not_independent_from_pepmlm_generation_or_ppl": True,
                },
            }
            repository = ExperimentRepository(session)
            run = await repository.create_run(
                spec,
                actor="sequence-binding-proxy-calibration-cli",
                parent_run_id=parent_run_id,
                raw_spec_payload=raw_spec,
            )
            import_result = {
                "candidates": candidate_entries,
                "target_panel_sha256": target_panel_sha256,
            }
            import_call = await repository.record_completed_tool_call(
                run.id,
                "sequence-binding-proxy-calibration-import",
                "v1",
                sha256_json({"adapter": "sequence-binding-proxy-calibration-import-v1"}),
                import_result,
                {
                    "exact_sequence_hash_required": True,
                    "same_target_required": True,
                    "fixed_target_panel_required": True,
                },
                import_result,
                model_uri="deterministic://sequence-binding-proxy-calibration-import",
            )
            staged: list[dict] = []
            for rank, entry in enumerate(candidate_entries, start=1):
                candidate = await repository.add_candidate(
                    run.id,
                    entry["sequence"],
                    generation=0,
                    proposal_rank=rank,
                    generator_call_id=import_call.id,
                    metadata={
                        **entry,
                        "import_tool_call_id": str(import_call.id),
                        "target_panel_sha256": target_panel_sha256,
                    },
                    actor="sequence-binding-proxy-calibration-import",
                )
                staged.append(
                    {
                        "id": str(candidate.id),
                        "sequence": candidate.sequence,
                        "sequence_sha256": candidate.sequence_sha256,
                        "cohort_role": entry["cohort_role"],
                    }
                )
        workflow_id = f"pepagent-sequence-binding-proxy-{run.id}"
        await temporal.start_workflow(
            "SequenceBindingProxyCalibrationWorkflow",
            {
                "run_id": str(run.id),
                "model_name": spec.pepmlm_model,
                "peptides": staged,
                "targets": targets,
                "target_panel_sha256": target_panel_sha256,
            },
            id=workflow_id,
            task_queue="pepagent-control",
        )
        return {
            "run_id": str(run.id),
            "workflow_id": workflow_id,
            "target_panel_sha256": target_panel_sha256,
            "candidates": staged,
        }

    typer.echo(json.dumps(asyncio.run(_run()), ensure_ascii=False, indent=2))


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


@app.command("summarize-rosetta-validation")
def summarize_rosetta_validation(run_id: list[uuid.UUID]) -> None:
    """Recompute native-start validation checks from immutable Rosetta evaluation payloads."""

    async def _run() -> list[dict]:
        summaries: list[dict] = []
        async with SessionFactory() as session:
            for identifier in run_id:
                run = await session.get(ExperimentRun, identifier)
                if run is None:
                    raise typer.BadParameter(f"run not found: {identifier}")
                evaluation = await session.scalar(
                    select(Evaluation)
                    .join(Candidate, Evaluation.candidate_id == Candidate.id)
                    .where(
                        Candidate.run_id == identifier,
                        Evaluation.metric_name == "rosetta_dg_separated_reu",
                    )
                )
                if evaluation is None:
                    raise typer.BadParameter(
                        f"run has no completed Rosetta dG evaluation: {identifier}"
                    )
                validation = run.spec_json.get("validation", {})
                summaries.append(
                    {
                        "run_id": str(identifier),
                        "run_status": run.status,
                        "suite_id": validation.get("suite_id"),
                        "pdb_id": validation.get("case", {}).get("pdb_id"),
                        "tool_call_id": str(evaluation.tool_call_id),
                        **summarize_native_start_validation(evaluation.raw_json),
                    }
                )
        return summaries

    typer.echo(json.dumps(asyncio.run(_run()), ensure_ascii=False, indent=2, sort_keys=True))


@app.command("submit-rosetta-validation")
def submit_rosetta_validation(
    suite_path: Path,
    nstruct: int | None = typer.Option(
        None, min=200, help="Override production decoy count; never below 200."
    ),
    case: list[str] | None = typer.Option(  # noqa: B008
        None, "--case", help="Submit only the named PDB case; repeat for multiple cases."
    ),
) -> None:
    """Stage public complexes as immutable evidence and launch durable Rosetta runs."""
    suite = _load_mapping(suite_path)
    try:
        validate_rosetta_protocol_policy(suite["source_policy"])
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    suite_digest = sha256_bytes(suite_path.read_bytes())
    production_nstruct = nstruct or int(suite["source_policy"]["production_nstruct"])
    if production_nstruct < 200:
        raise typer.BadParameter("decision-bearing validation requires at least 200 decoys")
    requested_cases = {name.upper() for name in case or []}
    selected_cases = [
        item
        for item in suite["cases"]
        if not requested_cases or item["pdb_id"].upper() in requested_cases
    ]
    found_cases = {item["pdb_id"].upper() for item in selected_cases}
    if missing := requested_cases - found_cases:
        raise typer.BadParameter(f"unknown validation cases: {sorted(missing)}")

    async def _run() -> list[dict]:
        settings = get_settings()
        temporal = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        staged: list[dict] = []
        with TemporaryDirectory(prefix="pepagent-rosetta-validation-") as temporary:
            temporary_root = Path(temporary)
            for case_spec in selected_cases:
                source_database = case_spec.get("source_database", "RCSB PDB")
                if source_database == "RCSB PDB":
                    retrieval_tool = "rcsb-pdb-retrieval"
                    retrieval_adapter = "pepagent-rcsb-pdb-retrieval-v1"
                else:
                    retrieval_tool = "pdb-coordinate-retrieval"
                    retrieval_adapter = "pepagent-pdb-coordinate-retrieval-v1"
                response = await asyncio.to_thread(
                    httpx.get, case_spec["source_uri"], timeout=60.0
                )
                response.raise_for_status()
                source_bytes = response.content
                actual_sha256 = sha256_bytes(source_bytes)
                if actual_sha256 != case_spec["source_sha256"]:
                    raise OSError(
                        f"{case_spec['pdb_id']} source hash mismatch: "
                        f"{actual_sha256} != {case_spec['source_sha256']}"
                    )
                source_path = temporary_root / f"{case_spec['pdb_id']}.pdb"
                source_path.write_bytes(source_bytes)
                receptor_sequence = atom_chain_sequence(
                    source_path, list(case_spec["receptor_chains"])
                )
                peptide_sequence = atom_chain_sequence(
                    source_path, [case_spec["peptide_chain"]]
                )
                if peptide_sequence != case_spec["modeled_peptide_sequence"]:
                    raise ValueError(
                        f"{case_spec['pdb_id']} modeled peptide changed: {peptide_sequence}"
                    )
                stored = await asyncio.to_thread(
                    ContentAddressedObjectStore().put_bytes,
                    source_bytes,
                    "chemical/x-pdb",
                )
                spec = ExperimentSpec(
                    target={
                        "name": f"{case_spec['pdb_id']} modeled receptor",
                        "sequence": receptor_sequence,
                        "accession": case_spec["pdb_id"],
                        "source_database": source_database,
                        "source_uri": case_spec["source_uri"],
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
                    "case": case_spec,
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
                            "pdb_id": case_spec["pdb_id"],
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
                            "adapter": retrieval_adapter,
                            "httpx": importlib.metadata.version("httpx"),
                        }
                    )
                    source_call = await repository.record_completed_tool_call(
                        run.id,
                        retrieval_tool,
                        "v1",
                        retrieval_environment,
                        {
                            "source_uri": case_spec["source_uri"],
                            "expected_sha256": case_spec["source_sha256"],
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
                                "source": source_database,
                                "pdb_id": case_spec["pdb_id"],
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
                    f"rosetta-validation-{suite['suite_id']}-{case_spec['pdb_id']}-{run.id}"
                )
                await temporal.start_workflow(
                    "RosettaValidationWorkflow",
                    {
                        "run_id": str(run.id),
                        "spec": spec.model_dump(mode="json"),
                        "validation_case": case_spec,
                        "structure": {
                            "candidate": {
                                "id": str(candidate.id),
                                "sequence": candidate.sequence,
                            },
                            "tool_call_id": str(source_call.id),
                            "provenance": {
                                "engine_artifacts": [
                                    {
                                        "path": f"{case_spec['pdb_id']}.pdb",
                                        **asdict(stored),
                                    }
                                ]
                            },
                        },
                    },
                    id=workflow_id,
                    task_queue="pepagent-control",
                )
                staged.append(
                    {
                        "pdb_id": case_spec["pdb_id"],
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


@app.command("validate-handoff-metrics")
def validate_handoff_metrics(
    suite_path: Path,
    output_path: Path,
    registry_path: Path | None = typer.Option(None),  # noqa: B008
) -> None:
    """Replay optional metrics against a hash-locked public complex control."""
    suite = _load_mapping(suite_path)
    case = suite["case"]
    with TemporaryDirectory(prefix="pepagent-handoff-metric-validation-") as temporary:
        temporary_root = Path(temporary)
        response = httpx.get(case["source_uri"], timeout=60.0)
        response.raise_for_status()
        source_path = temporary_root / f"{case['pdb_id']}.pdb"
        source_path.write_bytes(response.content)
        result = validate_handoff_metric_control(
            suite,
            source_path,
            temporary_root / "metric-work",
            registry_path,
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    summary_keys = (
        "suite_id",
        "overall_status",
        "scientific_status",
        "metric_statuses",
        "descriptor_reproduced",
        "qualitative_checks",
    )
    typer.echo(
        json.dumps(
            {key: result[key] for key in summary_keys},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
