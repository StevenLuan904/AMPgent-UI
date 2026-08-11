from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from pepagent.provenance.hashing import sha256_json

V37_GENERATOR_IDS = ("hydramp", "ampgan_v2", "amp_designer")
V37_RUNTIME_MANIFEST_SCHEMA = "v37.generator-runtime.1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class V37GeneratorRuntimeExpectation:
    """Immutable identities that a probed generator runtime must match."""

    generator_id: str
    adapter_sha256: str
    adapter_version: str
    source_revision: str
    source_manifest_sha256: str
    model_revision: str
    model_manifest_sha256: str
    request_contract_sha256: str
    upstream_source_revision: str | None = None
    provider_acceptance_sha256: str | None = None
    formal_seed_acceptance_sha256: str | None = None


def _require_sha256(value: object, label: str) -> str:
    text = str(value)
    if _SHA256.fullmatch(text) is None:
        raise ValueError(f"v37 runtime manifest has invalid {label}")
    return text


def _require_exact_keys(
    value: Mapping[str, Any], *, required: set[str], label: str
) -> None:
    observed = set(value)
    if observed != required:
        missing = sorted(required - observed)
        unexpected = sorted(observed - required)
        raise ValueError(
            f"v37 runtime manifest {label} keys drifted; "
            f"missing={missing}, unexpected={unexpected}"
        )


def _verify_file_manifest(value: object, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"v37 runtime manifest {label} must be a non-empty list")
    verified: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"v37 runtime manifest {label} entry must be an object")
        _require_exact_keys(
            item,
            required={"path", "size_bytes", "sha256"},
            label=f"{label} entry",
        )
        path = str(item["path"])
        parsed_path = PurePosixPath(path)
        if (
            not path
            or "\\" in path
            or parsed_path.is_absolute()
            or path != parsed_path.as_posix()
            or any(part in {".", ".."} for part in parsed_path.parts)
        ):
            raise ValueError(f"v37 runtime manifest {label} path is not relative")
        size_bytes = int(item["size_bytes"])
        if size_bytes < 0:
            raise ValueError(f"v37 runtime manifest {label} size must be non-negative")
        verified.append(
            {
                "path": path,
                "size_bytes": size_bytes,
                "sha256": _require_sha256(item["sha256"], f"{label} file SHA-256"),
            }
        )
    paths = [item["path"] for item in verified]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError(
            f"v37 runtime manifest {label} paths must be unique and sorted"
        )
    return verified


def verify_v37_generator_runtime_manifest(
    manifest: Mapping[str, Any],
    *,
    expectation: V37GeneratorRuntimeExpectation,
) -> dict[str, Any]:
    """Verify one real runtime snapshot against frozen generator identities."""
    if expectation.generator_id not in V37_GENERATOR_IDS:
        raise ValueError("v37 runtime expectation has unknown generator_id")
    required_top_level = {
        "schema_version",
        "generator_id",
        "adapter",
        "runtime",
        "source_release",
        "model_release",
        "request_contract",
        "request_contract_sha256",
        "internal_score_filtering_enabled",
        "unsafe_deserialization_enabled",
        "runtime_manifest_sha256",
    }
    if expectation.upstream_source_revision is not None:
        required_top_level.update({"upstream_source_release", "provider_acceptance"})
    _require_exact_keys(
        manifest,
        required=required_top_level,
        label="top-level",
    )
    if manifest["schema_version"] != V37_RUNTIME_MANIFEST_SCHEMA:
        raise ValueError("v37 runtime manifest schema differs from contract")
    if manifest["generator_id"] != expectation.generator_id:
        raise ValueError("v37 runtime manifest generator identity drifted")
    if manifest["internal_score_filtering_enabled"] is not False:
        raise ValueError("v37 runtime manifest enables internal score filtering")
    if manifest["unsafe_deserialization_enabled"] is not False:
        raise ValueError("v37 runtime manifest enables unsafe deserialization")

    adapter = manifest["adapter"]
    runtime = manifest["runtime"]
    source = manifest["source_release"]
    model = manifest["model_release"]
    request_contract = manifest["request_contract"]
    if not all(isinstance(item, Mapping) for item in (adapter, runtime, source, model)):
        raise ValueError("v37 runtime manifest contains a non-object identity section")
    if not isinstance(request_contract, Mapping) or not request_contract:
        raise ValueError("v37 runtime manifest request contract is empty")

    _require_exact_keys(
        adapter,
        required={"entrypoint", "sha256", "adapter_version"},
        label="adapter",
    )
    if not str(adapter["entrypoint"]):
        raise ValueError("v37 runtime manifest adapter entrypoint is empty")
    if _require_sha256(adapter["sha256"], "adapter SHA-256") != expectation.adapter_sha256:
        raise ValueError("v37 runtime manifest adapter SHA-256 drifted")
    if adapter["adapter_version"] != expectation.adapter_version:
        raise ValueError("v37 runtime manifest adapter version drifted")

    _require_exact_keys(
        runtime,
        required={
            "python_executable",
            "python_executable_sha256",
            "python_version",
            "environment_sha256",
            "packages_lock_sha256",
        },
        label="runtime",
    )
    if not str(runtime["python_executable"]) or not str(runtime["python_version"]):
        raise ValueError("v37 runtime manifest Python identity is incomplete")
    runtime_python = str(runtime["python_executable"])
    parsed_runtime_python = PurePosixPath(runtime_python)
    if (
        "\\" in runtime_python
        or parsed_runtime_python.is_absolute()
        or runtime_python != parsed_runtime_python.as_posix()
        or any(part in {".", ".."} for part in parsed_runtime_python.parts)
    ):
        raise ValueError("v37 runtime manifest Python executable path is not relative")
    for key in (
        "python_executable_sha256",
        "environment_sha256",
        "packages_lock_sha256",
    ):
        _require_sha256(runtime[key], key)

    _require_exact_keys(
        source,
        required={"uri", "revision", "manifest_sha256", "files_sha256", "files"},
        label="source release",
    )
    if not str(source["uri"]):
        raise ValueError("v37 runtime manifest source URI is empty")
    if source["revision"] != expectation.source_revision:
        raise ValueError("v37 runtime manifest source revision drifted")
    if (
        _require_sha256(source["manifest_sha256"], "source manifest SHA-256")
        != expectation.source_manifest_sha256
    ):
        raise ValueError("v37 runtime manifest source manifest drifted")
    source_files = _verify_file_manifest(source["files"], label="source files")
    if _require_sha256(source["files_sha256"], "source files SHA-256") != sha256_json(
        source_files
    ):
        raise ValueError("v37 runtime manifest source file-list hash drifted")
    source_identity = {
        key: value for key, value in source.items() if key != "manifest_sha256"
    }
    if source["manifest_sha256"] != sha256_json(source_identity):
        raise ValueError("v37 runtime manifest source release self-hash drifted")

    if expectation.upstream_source_revision is not None:
        upstream = manifest["upstream_source_release"]
        provider_acceptance = manifest["provider_acceptance"]
        if not isinstance(upstream, Mapping) or not isinstance(
            provider_acceptance, Mapping
        ):
            raise ValueError("v37 provider provenance section is not an object")
        _require_exact_keys(
            upstream,
            required={"uri", "revision"},
            label="upstream source release",
        )
        if upstream["revision"] != expectation.upstream_source_revision:
            raise ValueError("v37 upstream source revision drifted")
        _require_exact_keys(
            provider_acceptance,
            required={
                "receipt_path",
                "receipt_sha256",
                "formal_seed_receipt_path",
                "formal_seed_receipt_sha256",
            },
            label="provider acceptance",
        )
        receipt_path = str(provider_acceptance["receipt_path"])
        parsed_receipt_path = PurePosixPath(receipt_path)
        if (
            not receipt_path
            or "\\" in receipt_path
            or parsed_receipt_path.is_absolute()
            or receipt_path != parsed_receipt_path.as_posix()
            or any(part in {".", ".."} for part in parsed_receipt_path.parts)
        ):
            raise ValueError("v37 provider acceptance receipt path is not relative")
        receipt_sha256 = _require_sha256(
            provider_acceptance["receipt_sha256"],
            "provider acceptance receipt SHA-256",
        )
        if receipt_sha256 != expectation.provider_acceptance_sha256:
            raise ValueError("v37 provider acceptance receipt drifted")
        formal_seed_receipt_path = str(
            provider_acceptance["formal_seed_receipt_path"]
        )
        parsed_formal_seed_path = PurePosixPath(formal_seed_receipt_path)
        if (
            not formal_seed_receipt_path
            or "\\" in formal_seed_receipt_path
            or parsed_formal_seed_path.is_absolute()
            or formal_seed_receipt_path != parsed_formal_seed_path.as_posix()
            or any(part in {".", ".."} for part in parsed_formal_seed_path.parts)
        ):
            raise ValueError("v37 formal-seed acceptance receipt path is not relative")
        formal_seed_receipt_sha256 = _require_sha256(
            provider_acceptance["formal_seed_receipt_sha256"],
            "formal-seed acceptance receipt SHA-256",
        )
        if formal_seed_receipt_sha256 != expectation.formal_seed_acceptance_sha256:
            raise ValueError("v37 formal-seed acceptance receipt drifted")

    _require_exact_keys(
        model,
        required={"uri", "revision", "manifest_sha256", "files_sha256", "files"},
        label="model release",
    )
    if not str(model["uri"]):
        raise ValueError("v37 runtime manifest model URI is empty")
    if model["revision"] != expectation.model_revision:
        raise ValueError("v37 runtime manifest model revision drifted")
    if (
        _require_sha256(model["manifest_sha256"], "model manifest SHA-256")
        != expectation.model_manifest_sha256
    ):
        raise ValueError("v37 runtime manifest model manifest drifted")
    model_files = _verify_file_manifest(model["files"], label="model files")
    if _require_sha256(model["files_sha256"], "model files SHA-256") != sha256_json(
        model_files
    ):
        raise ValueError("v37 runtime manifest model file-list hash drifted")
    model_identity = {
        key: value for key, value in model.items() if key != "manifest_sha256"
    }
    if model["manifest_sha256"] != sha256_json(model_identity):
        raise ValueError("v37 runtime manifest model release self-hash drifted")

    request_contract_sha256 = _require_sha256(
        manifest["request_contract_sha256"], "request contract SHA-256"
    )
    if request_contract_sha256 != sha256_json(request_contract):
        raise ValueError("v37 runtime manifest request contract hash is not canonical")
    if request_contract_sha256 != expectation.request_contract_sha256:
        raise ValueError("v37 runtime manifest request contract drifted")

    identity = {
        key: value for key, value in manifest.items() if key != "runtime_manifest_sha256"
    }
    runtime_manifest_sha256 = _require_sha256(
        manifest["runtime_manifest_sha256"], "runtime manifest SHA-256"
    )
    if runtime_manifest_sha256 != sha256_json(identity):
        raise ValueError("v37 runtime manifest self-hash drifted")
    return {
        "schema_version": V37_RUNTIME_MANIFEST_SCHEMA,
        "generator_id": expectation.generator_id,
        "runtime_manifest_sha256": runtime_manifest_sha256,
        "source_file_count": len(source_files),
        "model_file_count": len(model_files),
        "verified": True,
    }


def verify_v37_generator_runtime_set(
    manifests: Sequence[Mapping[str, Any]],
    *,
    expectations: Mapping[str, V37GeneratorRuntimeExpectation],
) -> dict[str, Any]:
    """Require exactly one verified real runtime manifest per v37 generator."""
    if set(expectations) != set(V37_GENERATOR_IDS):
        raise ValueError("v37 runtime expectations are incomplete")
    by_generator: dict[str, Mapping[str, Any]] = {}
    for manifest in manifests:
        generator_id = str(manifest.get("generator_id", ""))
        if generator_id in by_generator:
            raise ValueError("v37 runtime manifest generator is duplicated")
        by_generator[generator_id] = manifest
    if set(by_generator) != set(V37_GENERATOR_IDS):
        raise ValueError("v37 runtime manifest set is incomplete or unexpected")
    verified = [
        verify_v37_generator_runtime_manifest(
            by_generator[generator_id], expectation=expectations[generator_id]
        )
        for generator_id in V37_GENERATOR_IDS
    ]
    result = {
        "schema_version": "v37.generator-runtime-set.1",
        "generator_runtime_manifests": [
            {
                "generator_id": item["generator_id"],
                "runtime_manifest_sha256": item["runtime_manifest_sha256"],
            }
            for item in verified
        ],
        "verified": True,
    }
    result["runtime_set_sha256"] = sha256_json(result)
    return result


def blocked_v37_runtime_manifest_status() -> dict[str, Any]:
    """Return the honest pre-probe status; this is not an executable gate pass."""
    result = {
        "schema_version": "v37.generator-runtime-status.1",
        "required_generators": list(V37_GENERATOR_IDS),
        "real_runtime_manifests_supplied": 0,
        "verified": False,
        "status": "blocked_real_generator_runtime_manifests_not_supplied",
    }
    result["status_sha256"] = sha256_json(result)
    return result
