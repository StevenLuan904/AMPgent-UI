from __future__ import annotations

from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

from pepagent.settings import get_settings


def register_model_version(
    name: str,
    source_uri: str,
    source_revision: str,
    weights_sha256: str,
    admission_status: str,
) -> str:
    client = MlflowClient(tracking_uri=get_settings().mlflow_tracking_uri)
    try:
        client.get_registered_model(name)
    except MlflowException:
        client.create_registered_model(
            name,
            tags={"managed_by": "pepagent", "scientific_admission_gate": "required"},
            description="PepAgent immutable model releases; aliases reflect admission state.",
        )
    for version in client.search_model_versions(f"name='{name}'"):
        if version.tags.get("weights_sha256") == weights_sha256:
            return str(version.version)
    version = client.create_model_version(
        name=name,
        source=source_uri,
        tags={
            "source_revision": source_revision,
            "weights_sha256": weights_sha256,
            "admission_status": admission_status,
        },
    )
    if admission_status == "admitted":
        client.set_registered_model_alias(name, "admitted", version.version)
    return str(version.version)
