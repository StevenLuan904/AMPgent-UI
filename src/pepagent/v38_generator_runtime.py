from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_file, sha256_json
from pepagent.v37_generator_launch import build_v37_generator_launch_binding

V38_AMP_DESIGNER_ADAPTER = Path(
    "src/pepagent/model_workers/amp_designer_generator_v38_cli.py"
)
V38_AMP_DESIGNER_ADAPTER_VERSION = "amp-designer-v38-score-all-batch100-v1"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"runtime JSON must be an object: {path}")
    return payload


def _v38_request_contract(generator_id: str) -> dict[str, Any]:
    return {
        "schema_version": "v38.generator-request.1",
        "additional_properties": False,
        "properties": {
            "schema_version": {"const": "v38.generator-request.1"},
            "generator_id": {"const": generator_id},
            "raw_proposal_budget": {"const": 100},
            "seed": {"type": "integer"},
        },
        "required": [
            "schema_version",
            "generator_id",
            "raw_proposal_budget",
            "seed",
        ],
    }


def build_v38_generator_runtime(
    workspace: Path, generator_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derive a score-all runtime from frozen v37 bytes without mutating them."""
    workspace = workspace.resolve()
    runtime_root = workspace / "config/environments/v37_generator_runtimes"
    index = _load_json(runtime_root / "runtime-index.json")
    matching = [item for item in index["entries"] if item["generator_id"] == generator_id]
    if len(matching) != 1:
        raise ValueError("generator runtime index identity is missing or ambiguous")
    old_entry = matching[0]
    old_manifest = _load_json(workspace / old_entry["manifest_path"])
    manifest = copy.deepcopy(old_manifest)
    request_contract = _v38_request_contract(generator_id)
    manifest["request_contract"] = request_contract
    manifest["request_contract_sha256"] = sha256_json(request_contract)
    if generator_id == "amp_designer":
        adapter_path = workspace / V38_AMP_DESIGNER_ADAPTER
        manifest["adapter"] = {
            "entrypoint": V38_AMP_DESIGNER_ADAPTER.as_posix(),
            "sha256": sha256_file(adapter_path),
            "adapter_version": V38_AMP_DESIGNER_ADAPTER_VERSION,
        }
    manifest_without_sha = {
        key: value for key, value in manifest.items() if key != "runtime_manifest_sha256"
    }
    manifest["runtime_manifest_sha256"] = sha256_json(manifest_without_sha)

    expectation = copy.deepcopy(old_entry["expectation"])
    expectation.update(
        adapter_sha256=manifest["adapter"]["sha256"],
        adapter_version=manifest["adapter"]["adapter_version"],
        request_contract_sha256=manifest["request_contract_sha256"],
    )
    derived_entry = {
        "generator_id": generator_id,
        "manifest_path": old_entry["manifest_path"],
        "expectation": expectation,
    }
    derived_index = {
        "schema_version": "v38.generator-runtime-index.1",
        "entries": [derived_entry],
    }
    derived_index["runtime_index_sha256"] = sha256_json(derived_index)
    launch = build_v37_generator_launch_binding(
        workspace=workspace,
        runtime_index=derived_index,
        entry=derived_entry,
        manifest=manifest,
    )
    return manifest, launch


def build_v38_execution_bundle(
    workspace: Path, v37_execution_bundle: dict[str, Any]
) -> dict[str, Any]:
    """Project frozen v37 runtime bytes onto the v38 100-proposal contract.

    The model, source release, Python environment, weights, and sampling settings
    remain unchanged.  Only the consumer request contracts are rebound to the
    v38 cell size; AMP-Designer uses its already-frozen one-batch v38 adapter.
    """
    if v37_execution_bundle.get("schema_version") != "v37.execution-bundle.1":
        raise ValueError("v38 runtime projection requires a v37 execution bundle")
    bundle = copy.deepcopy(v37_execution_bundle)
    generator_ids = ("hydramp", "ampgan_v2", "amp_designer")
    if set(bundle.get("generator_runtimes", {})) != set(generator_ids) or set(
        bundle.get("generator_launch_bindings", {})
    ) != set(generator_ids):
        raise ValueError("v38 runtime projection generator coverage drifted")
    for generator_id in generator_ids:
        manifest, launch = build_v38_generator_runtime(workspace, generator_id)
        bundle["generator_runtimes"][generator_id] = manifest
        bundle["generator_launch_bindings"][generator_id] = launch
    identity = {
        key: value
        for key, value in bundle.items()
        if key != "execution_bundle_identity_sha256"
    }
    bundle["execution_bundle_identity_sha256"] = sha256_json(identity)
    return bundle
