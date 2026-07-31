import asyncio
import json
from pathlib import Path

import httpx
import typer
import yaml

from pepagent.domain.schemas import ExperimentSpec
from pepagent.registry.service import register_local_model_release
from pepagent.settings import get_settings

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


if __name__ == "__main__":
    app()
