from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_json
from pepagent.v37_runtime_manifests import (
    V37_RUNTIME_MANIFEST_SCHEMA,
    V37GeneratorRuntimeExpectation,
    verify_v37_generator_runtime_manifest,
)

_RELEASE_VERSION = "v37-generator-runtimes-v1"
_HYDRAMP_PROVIDER_REVISION = "36b18003122f0d73323f9644b07e1ed267255c11"
_HYDRAMP_UPSTREAM_REVISION = "6590d2f4c2963f25d30669052a4c4a857e0e7279"
_HYDRAMP_ADAPTER_VERSION = "hydramp-safe-pca-stateless-gumbel-v1"
_HYDRAMP_RELEASE_MANIFEST_SHA256 = (
    "5b66bd0c4364e26cf629af27620789408cdb7765448d63765434fe97ed21d822"
)
_HYDRAMP_RELEASE_VERIFIER_SHA256 = (
    "3f4282501403d2e6386836eaeae3e8f6985f83de7ee66fd2601eb63111f5103b"
)
_HYDRAMP_PROVIDER_ACCEPTANCE_SHA256 = (
    "6bfdbda1c544a3dcda7df6bc1413f88fa72b41ba07838d00d49d2f18b158198d"
)
_HYDRAMP_SAFE_PCA_SHA256 = (
    "5f729138df7bf8e3b3fc0d443cc6c3ad259305de4beeb36f09aaea33bd2bd7a3"
)
_HYDRAMP_SAFE_PCA_MANIFEST_SHA256 = (
    "50c31657fffddf77540e054452e329b8508eaff79e4e8196b37092dc93bf55cc"
)
_HYDRAMP_V37_FORMAL_SEED_RECEIPTS = (
    {
        "seed": 20270371,
        "ordered_sequence_sha256": (
            "ad66f0a934fad88f1d5332dce69c7232183f1f032af9b7c1eda69f933a639026"
        ),
        "process_output_sha256": (
            "64d7fe516c65eaef9cf15477c9d210f5be80373b53369a373d923ed9133a4389"
        ),
        "unique_rows": 736,
        "valid_standard_length_rows": 627,
    },
    {
        "seed": 20270372,
        "ordered_sequence_sha256": (
            "e56e2edbbfcb62e9f4ffd8b5bf531ffbdcca17068a93dc8c07052cb68a239cc9"
        ),
        "process_output_sha256": (
            "06b6ac810e9b9f9f2186fb6c9b3968a6087fc23aa54d482cbada1e6e6fdca308"
        ),
        "unique_rows": 722,
        "valid_standard_length_rows": 645,
    },
    {
        "seed": 20270373,
        "ordered_sequence_sha256": (
            "856ca98a2383b5c603832fae61ea180a65d4efe8f15e5dcbda692c81765408da"
        ),
        "process_output_sha256": (
            "2773d60bd41b80c910a4373aa25d8d185ae8addb425f8e487af0fc52b07e8b8b"
        ),
        "unique_rows": 734,
        "valid_standard_length_rows": 608,
    },
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path, relative_paths: list[str] | None = None) -> list[dict[str, Any]]:
    if relative_paths is None:
        paths = sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    else:
        paths = [root / path for path in sorted(relative_paths)]
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in paths
    ]


def _verify_hydramp_provider_assets(provider_root: Path) -> None:
    expected = {
        "releases/hydramp-safe-pca-v1/acceptance.receipt.json": (
            _HYDRAMP_PROVIDER_ACCEPTANCE_SHA256
        ),
        "releases/hydramp-safe-pca-v1/pca_decomposer.safe.manifest.json": (
            _HYDRAMP_SAFE_PCA_MANIFEST_SHA256
        ),
        "releases/hydramp-safe-pca-v1/pca_decomposer.safe.npz": (
            _HYDRAMP_SAFE_PCA_SHA256
        ),
        "releases/hydramp-safe-pca-v1/release.manifest.json": (
            _HYDRAMP_RELEASE_MANIFEST_SHA256
        ),
        "releases/hydramp-safe-pca-v1/release.verifier.receipt.json": (
            _HYDRAMP_RELEASE_VERIFIER_SHA256
        ),
    }
    observed = {
        relative_path: _sha256_file(provider_root / relative_path)
        for relative_path in expected
    }
    if observed != expected:
        raise ValueError("HydrAMP provider release asset SHA-256 drifted")


def _runtime_identity(workspace: Path, runtime_name: str) -> tuple[dict[str, Any], str]:
    relative_python = Path("var/generator-runtimes") / runtime_name / "Scripts/python.exe"
    python = workspace / relative_python
    probe = (
        "import importlib.metadata as m,json,platform;"
        "print(json.dumps({'python_version':platform.python_version(),"
        "'packages':sorted([{'name':d.metadata.get('Name',d.metadata.get('Summary','unknown')),'version':d.version}"
        " for d in m.distributions()],key=lambda x:(x['name'].lower(),x['version']))},"
        "sort_keys=True,separators=(',',':')))"
    )
    raw = subprocess.check_output([str(python), "-c", probe], text=True).strip()
    environment = json.loads(raw)
    packages_lock = "\n".join(
        f"{item['name']}=={item['version']}" for item in environment["packages"]
    ) + "\n"
    packages_sha256 = hashlib.sha256(packages_lock.encode("utf-8")).hexdigest()
    executable_sha256 = _sha256_file(python)
    runtime = {
        "python_executable": relative_python.as_posix(),
        "python_executable_sha256": executable_sha256,
        "python_version": environment["python_version"],
        "environment_sha256": sha256_json(
            {
                "python_executable": relative_python.as_posix(),
                "python_executable_sha256": executable_sha256,
                "python_version": environment["python_version"],
                "packages_lock_sha256": packages_sha256,
            }
        ),
        "packages_lock_sha256": packages_sha256,
    }
    return runtime, packages_lock


def _release(uri: str, revision: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    files_sha256 = sha256_json(files)
    identity = {
        "uri": uri,
        "revision": revision,
        "files_sha256": files_sha256,
        "files": files,
    }
    return {**identity, "manifest_sha256": sha256_json(identity)}


def _request_contract(generator_id: str) -> dict[str, Any]:
    return {
        "schema_version": "v37.generator-request.1",
        "additional_properties": False,
        "required": ["generator_id", "raw_proposal_budget", "seed"],
        "properties": {
            "generator_id": {"const": generator_id},
            "raw_proposal_budget": {"const": 1000},
            "seed": {"type": "integer"},
        },
    }


def _generator_definitions(workspace: Path) -> list[dict[str, Any]]:
    return [
        {
            "generator_id": "hydramp",
            "runtime_name": "hydramp-py38",
            "adapter": (
                f"var/releases/{_RELEASE_VERSION}/hydramp/provider/"
                f"{_HYDRAMP_PROVIDER_REVISION}/amp/inference/inference.py"
            ),
            "adapter_version": _HYDRAMP_ADAPTER_VERSION,
            "source_root": (
                f"var/releases/{_RELEASE_VERSION}/hydramp/provider/"
                f"{_HYDRAMP_PROVIDER_REVISION}"
            ),
            "source_uri": (
                f"workspace-release://var/releases/{_RELEASE_VERSION}/hydramp/"
                f"provider/{_HYDRAMP_PROVIDER_REVISION}"
            ),
            "source_revision": _HYDRAMP_PROVIDER_REVISION,
            "source_files": None,
            "upstream_source_uri": "https://github.com/szczurek-lab/hydramp",
            "upstream_source_revision": _HYDRAMP_UPSTREAM_REVISION,
            "model_root": (
                f"var/releases/{_RELEASE_VERSION}/hydramp/"
                "model-safe-5f729138df7b"
            ),
            "model_uri": (
                f"workspace-release://var/releases/{_RELEASE_VERSION}/hydramp/"
                "model-safe-5f729138df7b"
            ),
            "model_revision": "zenodo-7420278-models-c2bf137ebe546fee",
            "unsafe": False,
            "provider_acceptance": True,
        },
        {
            "generator_id": "ampgan_v2",
            "runtime_name": "ampgan-py38",
            "adapter": "src/pepagent/model_workers/ampgan_v2_generator_cli.py",
            "adapter_version": "ampgan-v2-generator-v1-positive-conditions-unfiltered",
            "source_root": (
                f"var/releases/{_RELEASE_VERSION}/ampgan_v2/"
                "source-1a2ac60bdc268f99"
            ),
            "source_uri": (
                f"workspace-release://var/releases/{_RELEASE_VERSION}/ampgan_v2/"
                "source-1a2ac60bdc268f99"
            ),
            "source_revision": "1009476bdb988707ff260def863d694549dc18b0",
            "source_files": None,
            "model_root": f"var/releases/{_RELEASE_VERSION}/ampgan_v2/model",
            "model_uri": f"workspace-release://var/releases/{_RELEASE_VERSION}/ampgan_v2/model",
            "model_revision": "ampgan-v2-gan-1606-a5e7cafa16c33010",
            "unsafe": False,
        },
        {
            "generator_id": "amp_designer",
            "runtime_name": "amp-designer-py39",
            "adapter": "src/pepagent/model_workers/amp_designer_generator_cli.py",
            "adapter_version": "amp-designer-v25-raw-topk10-batch100-v1",
            "source_root": (
                "var/generator-sources/"
                "amp-designer-b554b1ac1507040d9d50356e037098e652ce4719"
            ),
            "source_uri": "https://github.com/aziele/AMP-Designer",
            "source_revision": "b554b1ac1507040d9d50356e037098e652ce4719",
            "source_files": [
                "AMP_GPT_generator.py",
                "LICENSE",
                "soft_prompt_embedding.py",
                "voc/vocab.txt",
            ],
            "model_root": f"var/releases/{_RELEASE_VERSION}/amp_designer/model",
            "model_uri": f"workspace-release://var/releases/{_RELEASE_VERSION}/amp_designer/model",
            "model_revision": "zenodo-15051980-47944ff42f7ea6a4",
            "unsafe": False,
        },
    ]


def build_local_runtime_set(workspace: Path, output_root: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    if not output_root.is_absolute():
        output_root = workspace / output_root
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    hydramp_source = workspace / "var/research/hydramp/amp/inference/inference.py"
    hydramp_blocker: dict[str, Any] = {
        "schema_version": "v37.generator-runtime-blocker.1",
        "generator_id": "hydramp",
        "blocker_code": "unsafe_joblib_deserialization",
        "status": "blocked",
        "evidence": {
            "source_revision": "6590d2f4c2963f25d30669052a4c4a857e0e7279",
            "source_path": "amp/inference/inference.py",
            "source_sha256": _sha256_file(hydramp_source),
            "line": 97,
            "operation": "joblib.load(decomposer_path)",
        },
        "acceptance_criteria": [
            "provider publishes a new immutable source and model release",
            "the decomposer is stored as non-executable numeric data",
            "the provider-owned loader does not call pickle or joblib.load",
            "provider tests prove deterministic frozen-seed output parity",
            "the new runtime manifest verifies with unsafe_deserialization_enabled=false",
        ],
        "ampgent_compatibility_patch_forbidden": True,
    }
    hydramp_blocker["blocker_receipt_sha256"] = sha256_json(hydramp_blocker)
    blocker_path = output_root / "hydramp.blocker.json"
    if blocker_path.exists():
        existing_blocker = json.loads(blocker_path.read_text(encoding="utf-8"))
        if existing_blocker != hydramp_blocker:
            raise ValueError("historical HydrAMP blocker receipt drifted")
    else:
        blocker_path.write_text(
            json.dumps(hydramp_blocker, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    provider_root = workspace / (
        f"var/releases/{_RELEASE_VERSION}/hydramp/provider/"
        f"{_HYDRAMP_PROVIDER_REVISION}"
    )
    _verify_hydramp_provider_assets(provider_root)
    provider_acceptance_payload = json.loads(
        (provider_root / "releases/hydramp-safe-pca-v1/acceptance.receipt.json").read_text(
            encoding="utf-8"
        )
    )
    provider_seed_receipts = [
        {
            "seed": item["seed"],
            "rows": item["generated"]["rows"],
            "sequence_order_sha256": item["generated"]["sequence_order_sha256"],
            "cross_process_exact_order": item["checks"][
                "cross_process_exact_order"
            ],
        }
        for item in provider_acceptance_payload["seed_receipts"]
    ]
    hydramp_acceptance: dict[str, Any] = {
        "schema_version": "v37.generator-runtime-provider-acceptance.1",
        "generator_id": "hydramp",
        "status": "accepted",
        "provider_release": {
            "release_name": "hydramp-safe-pca-v1.0.0",
            "tag": "hydramp-safe-v1.0.0",
            "commit": _HYDRAMP_PROVIDER_REVISION,
            "release_manifest_sha256": _HYDRAMP_RELEASE_MANIFEST_SHA256,
            "release_verifier_receipt_sha256": _HYDRAMP_RELEASE_VERIFIER_SHA256,
            "provider_acceptance_receipt_sha256": (
                _HYDRAMP_PROVIDER_ACCEPTANCE_SHA256
            ),
            "safe_pca_sha256": _HYDRAMP_SAFE_PCA_SHA256,
            "adapter_version": _HYDRAMP_ADAPTER_VERSION,
        },
        "upstream_source": {
            "uri": "https://github.com/szczurek-lab/hydramp",
            "revision": _HYDRAMP_UPSTREAM_REVISION,
        },
        "historical_blocker": {
            "receipt_path": blocker_path.relative_to(workspace).as_posix(),
            "receipt_sha256": hydramp_blocker["blocker_receipt_sha256"],
            "preserved_status": "blocked",
        },
        "independent_process_exact_order_receipts": provider_seed_receipts,
        "unsafe_deserialization_enabled": False,
        "ampgent_compatibility_patch_applied": False,
        "formal_run_executed": False,
    }
    hydramp_acceptance["acceptance_receipt_sha256"] = sha256_json(
        hydramp_acceptance
    )
    acceptance_path = output_root / "hydramp.acceptance.json"
    acceptance_path.write_text(
        json.dumps(hydramp_acceptance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    formal_seed_receipts = [
        {
            **item,
            "rows_per_process": 1000,
            "process_a_output_sha256": item["process_output_sha256"],
            "process_b_output_sha256": item["process_output_sha256"],
            "cross_process_exact_order": True,
        }
        for item in _HYDRAMP_V37_FORMAL_SEED_RECEIPTS
    ]
    for item in formal_seed_receipts:
        del item["process_output_sha256"]
    formal_seed_acceptance: dict[str, Any] = {
        "schema_version": "v37.generator-runtime-formal-seed-acceptance.1",
        "benchmark_id": "amp_rapid_champion_generation_v37",
        "generator_id": "hydramp",
        "status": "accepted",
        "provider_release": {
            "commit": _HYDRAMP_PROVIDER_REVISION,
            "adapter_version": _HYDRAMP_ADAPTER_VERSION,
            "release_manifest_sha256": _HYDRAMP_RELEASE_MANIFEST_SHA256,
            "release_verifier_receipt_sha256": _HYDRAMP_RELEASE_VERIFIER_SHA256,
            "safe_pca_sha256": _HYDRAMP_SAFE_PCA_SHA256,
        },
        "formal_generation_contract": {
            "seeds": [20270371, 20270372, 20270373],
            "raw_proposal_budget_per_seed": 1000,
            "mode": "amp",
            "filter_out": False,
            "properties": False,
            "n_attempts": 1,
            "processes_per_seed": 2,
            "process_isolation": "independent_fresh_python_processes",
            "device": "cpu-only",
        },
        "seed_receipts": formal_seed_receipts,
        "acceptance_rule": (
            "each formal seed must produce exactly 1000 rows with exact sequence "
            "identity and order in two independent processes"
        ),
        "raw_validity_or_uniqueness_is_acceptance_gate": False,
        "downstream_selection_rule": "raw_order_first_k_valid_unique",
        "provider_snapshot_hygiene": {
            "validation_generated_bytecode_cache": True,
            "generated_cache_removed_after_path_validation": True,
            "post_cleanup_snapshot_cache_count": 0,
            "future_execution_requires_PYTHONDONTWRITEBYTECODE": True,
        },
        "unsafe_deserialization_executed": False,
        "remote_or_gpu_accessed": False,
        "formal_run_executed": False,
    }
    formal_seed_acceptance["acceptance_receipt_sha256"] = sha256_json(
        formal_seed_acceptance
    )
    formal_seed_acceptance_path = output_root / (
        "hydramp.formal-seed-acceptance.json"
    )
    formal_seed_acceptance_path.write_text(
        json.dumps(formal_seed_acceptance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    entries: list[dict[str, Any]] = []
    for definition in _generator_definitions(workspace):
        generator_id = definition["generator_id"]
        runtime, packages_lock = _runtime_identity(workspace, definition["runtime_name"])
        (output_root / f"{generator_id}.packages.lock.txt").write_text(
            packages_lock, encoding="utf-8", newline="\n"
        )
        source = _release(
            definition["source_uri"],
            definition["source_revision"],
            _inventory(workspace / definition["source_root"], definition["source_files"]),
        )
        model = _release(
            definition["model_uri"],
            definition["model_revision"],
            _inventory(workspace / definition["model_root"]),
        )
        request_contract = _request_contract(generator_id)
        manifest: dict[str, Any] = {
            "schema_version": V37_RUNTIME_MANIFEST_SCHEMA,
            "generator_id": generator_id,
            "adapter": {
                "entrypoint": definition["adapter"],
                "sha256": _sha256_file(workspace / definition["adapter"]),
                "adapter_version": definition["adapter_version"],
            },
            "runtime": runtime,
            "source_release": source,
            "model_release": model,
            "request_contract": request_contract,
            "request_contract_sha256": sha256_json(request_contract),
            "internal_score_filtering_enabled": False,
            "unsafe_deserialization_enabled": definition["unsafe"],
        }
        if definition.get("provider_acceptance") is True:
            manifest["upstream_source_release"] = {
                "uri": definition["upstream_source_uri"],
                "revision": definition["upstream_source_revision"],
            }
            manifest["provider_acceptance"] = {
                "receipt_path": acceptance_path.relative_to(workspace).as_posix(),
                "receipt_sha256": hydramp_acceptance["acceptance_receipt_sha256"],
                "formal_seed_receipt_path": (
                    formal_seed_acceptance_path.relative_to(workspace).as_posix()
                ),
                "formal_seed_receipt_sha256": formal_seed_acceptance[
                    "acceptance_receipt_sha256"
                ],
            }
        manifest["runtime_manifest_sha256"] = sha256_json(manifest)
        manifest_path = output_root / f"{generator_id}.runtime.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        expectation = V37GeneratorRuntimeExpectation(
            generator_id=generator_id,
            adapter_sha256=manifest["adapter"]["sha256"],
            adapter_version=definition["adapter_version"],
            source_revision=definition["source_revision"],
            source_manifest_sha256=source["manifest_sha256"],
            model_revision=definition["model_revision"],
            model_manifest_sha256=model["manifest_sha256"],
            request_contract_sha256=manifest["request_contract_sha256"],
            upstream_source_revision=definition.get("upstream_source_revision"),
            provider_acceptance_sha256=(
                hydramp_acceptance["acceptance_receipt_sha256"]
                if definition.get("provider_acceptance") is True
                else None
            ),
            formal_seed_acceptance_sha256=(
                formal_seed_acceptance["acceptance_receipt_sha256"]
                if definition.get("provider_acceptance") is True
                else None
            ),
        )
        status = "blocked" if definition["unsafe"] else "verified"
        if status == "verified":
            verify_v37_generator_runtime_manifest(manifest, expectation=expectation)
        entries.append(
            {
                "generator_id": generator_id,
                "status": status,
                "manifest_path": manifest_path.relative_to(workspace).as_posix(),
                "runtime_manifest_sha256": manifest["runtime_manifest_sha256"],
                "expectation": expectation.__dict__,
                "blocker_code": (
                    "unsafe_joblib_deserialization" if definition["unsafe"] else None
                ),
                "blocker_receipt_path": (
                    blocker_path.relative_to(workspace).as_posix()
                    if definition["unsafe"]
                    else None
                ),
                "historical_blocker_receipt_path": (
                    blocker_path.relative_to(workspace).as_posix()
                    if generator_id == "hydramp"
                    else None
                ),
                "acceptance_receipt_path": (
                    acceptance_path.relative_to(workspace).as_posix()
                    if generator_id == "hydramp"
                    else None
                ),
                "formal_seed_acceptance_receipt_path": (
                    formal_seed_acceptance_path.relative_to(workspace).as_posix()
                    if generator_id == "hydramp"
                    else None
                ),
            }
        )
    index: dict[str, Any] = {
        "schema_version": "v37.generator-runtime-index.1",
        "release_version": _RELEASE_VERSION,
        "overall_status": "verified" if all(
            entry["status"] == "verified" for entry in entries
        ) else "blocked",
        "entries": entries,
    }
    index["runtime_index_sha256"] = sha256_json(index)
    (output_root / "runtime-index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return index


def verify_local_runtime_set(workspace: Path, output_root: Path) -> dict[str, Any]:
    workspace = workspace.resolve()
    if not output_root.is_absolute():
        output_root = workspace / output_root
    output_root = output_root.resolve()
    index = json.loads((output_root / "runtime-index.json").read_text(encoding="utf-8"))
    if index["runtime_index_sha256"] != sha256_json(
        {key: value for key, value in index.items() if key != "runtime_index_sha256"}
    ):
        raise ValueError("v37 runtime index self-hash drifted")
    definitions = {item["generator_id"]: item for item in _generator_definitions(workspace)}
    verified: list[str] = []
    blocked: list[str] = []
    for entry in index["entries"]:
        generator_id = entry["generator_id"]
        definition = definitions[generator_id]
        manifest_path = workspace / entry["manifest_path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["runtime_manifest_sha256"] != entry["runtime_manifest_sha256"]:
            raise ValueError(f"{generator_id} runtime index/manifest identity drifted")
        expectation = V37GeneratorRuntimeExpectation(**entry["expectation"])
        runtime_python = workspace / manifest["runtime"]["python_executable"]
        if _sha256_file(runtime_python) != manifest["runtime"]["python_executable_sha256"]:
            raise ValueError(f"{generator_id} Python executable drifted")
        lock_path = output_root / f"{generator_id}.packages.lock.txt"
        if _sha256_file(lock_path) != manifest["runtime"]["packages_lock_sha256"]:
            raise ValueError(f"{generator_id} package lock drifted")
        source_files = _inventory(
            workspace / definition["source_root"], definition["source_files"]
        )
        if source_files != manifest["source_release"]["files"]:
            raise ValueError(f"{generator_id} source release files drifted")
        model_files = _inventory(workspace / definition["model_root"])
        if model_files != manifest["model_release"]["files"]:
            raise ValueError(f"{generator_id} model release files drifted")
        adapter_path = workspace / manifest["adapter"]["entrypoint"]
        if _sha256_file(adapter_path) != manifest["adapter"]["sha256"]:
            raise ValueError(f"{generator_id} adapter drifted")
        if entry["status"] == "verified":
            verify_v37_generator_runtime_manifest(manifest, expectation=expectation)
            if generator_id == "hydramp":
                _verify_hydramp_provider_assets(
                    workspace / definition["source_root"]
                )
                acceptance_path = workspace / entry["acceptance_receipt_path"]
                acceptance = json.loads(
                    acceptance_path.read_text(encoding="utf-8")
                )
                acceptance_identity = {
                    key: value
                    for key, value in acceptance.items()
                    if key != "acceptance_receipt_sha256"
                }
                if acceptance["acceptance_receipt_sha256"] != sha256_json(
                    acceptance_identity
                ):
                    raise ValueError("HydrAMP acceptance receipt self-hash drifted")
                if manifest["provider_acceptance"]["receipt_sha256"] != (
                    acceptance["acceptance_receipt_sha256"]
                ):
                    raise ValueError("HydrAMP manifest/acceptance identity drifted")
                formal_seed_acceptance_path = workspace / entry[
                    "formal_seed_acceptance_receipt_path"
                ]
                formal_seed_acceptance = json.loads(
                    formal_seed_acceptance_path.read_text(encoding="utf-8")
                )
                formal_seed_identity = {
                    key: value
                    for key, value in formal_seed_acceptance.items()
                    if key != "acceptance_receipt_sha256"
                }
                if formal_seed_acceptance["acceptance_receipt_sha256"] != (
                    sha256_json(formal_seed_identity)
                ):
                    raise ValueError(
                        "HydrAMP formal-seed acceptance receipt self-hash drifted"
                    )
                if manifest["provider_acceptance"][
                    "formal_seed_receipt_sha256"
                ] != formal_seed_acceptance["acceptance_receipt_sha256"]:
                    raise ValueError(
                        "HydrAMP manifest/formal-seed acceptance identity drifted"
                    )
                formal_seed_receipts = formal_seed_acceptance["seed_receipts"]
                expected_formal_seed_receipts = [
                    {
                        **item,
                        "rows_per_process": 1000,
                        "process_a_output_sha256": item["process_output_sha256"],
                        "process_b_output_sha256": item["process_output_sha256"],
                        "cross_process_exact_order": True,
                    }
                    for item in _HYDRAMP_V37_FORMAL_SEED_RECEIPTS
                ]
                for item in expected_formal_seed_receipts:
                    del item["process_output_sha256"]
                if formal_seed_receipts != expected_formal_seed_receipts:
                    raise ValueError("HydrAMP formal-seed process receipt drifted")
                hygiene = formal_seed_acceptance["provider_snapshot_hygiene"]
                if hygiene != {
                    "validation_generated_bytecode_cache": True,
                    "generated_cache_removed_after_path_validation": True,
                    "post_cleanup_snapshot_cache_count": 0,
                    "future_execution_requires_PYTHONDONTWRITEBYTECODE": True,
                }:
                    raise ValueError("HydrAMP provider snapshot hygiene receipt drifted")
                historical_path = workspace / entry[
                    "historical_blocker_receipt_path"
                ]
                historical = json.loads(historical_path.read_text(encoding="utf-8"))
                if historical["status"] != "blocked" or historical[
                    "blocker_receipt_sha256"
                ] != sha256_json(
                    {
                        key: value
                        for key, value in historical.items()
                        if key != "blocker_receipt_sha256"
                    }
                ):
                    raise ValueError("HydrAMP historical blocker evidence drifted")
                if acceptance["historical_blocker"]["receipt_sha256"] != historical[
                    "blocker_receipt_sha256"
                ]:
                    raise ValueError("HydrAMP blocker/acceptance chain drifted")
                provider = acceptance["provider_release"]
                if provider != {
                    "release_name": "hydramp-safe-pca-v1.0.0",
                    "tag": "hydramp-safe-v1.0.0",
                    "commit": _HYDRAMP_PROVIDER_REVISION,
                    "release_manifest_sha256": _HYDRAMP_RELEASE_MANIFEST_SHA256,
                    "release_verifier_receipt_sha256": (
                        _HYDRAMP_RELEASE_VERIFIER_SHA256
                    ),
                    "provider_acceptance_receipt_sha256": (
                        _HYDRAMP_PROVIDER_ACCEPTANCE_SHA256
                    ),
                    "safe_pca_sha256": _HYDRAMP_SAFE_PCA_SHA256,
                    "adapter_version": _HYDRAMP_ADAPTER_VERSION,
                }:
                    raise ValueError("HydrAMP provider release identity drifted")
                receipts = acceptance[
                    "independent_process_exact_order_receipts"
                ]
                if [item["seed"] for item in receipts] != [
                    20260809,
                    20260810,
                    20260811,
                ] or not all(
                    item["rows"] == 1000
                    and item["cross_process_exact_order"] is True
                    for item in receipts
                ):
                    raise ValueError("HydrAMP independent-process receipt drifted")
            verified.append(generator_id)
            continue
        if generator_id != "hydramp" or entry["blocker_code"] != (
            "unsafe_joblib_deserialization"
        ):
            raise ValueError("v37 runtime index contains an unknown blocker")
        try:
            verify_v37_generator_runtime_manifest(manifest, expectation=expectation)
        except ValueError as error:
            if "unsafe deserialization" not in str(error):
                raise
        else:
            raise ValueError("HydrAMP unsafe runtime unexpectedly passed")
        receipt = json.loads(
            (workspace / entry["blocker_receipt_path"]).read_text(encoding="utf-8")
        )
        if receipt["blocker_receipt_sha256"] != sha256_json(
            {
                key: value
                for key, value in receipt.items()
                if key != "blocker_receipt_sha256"
            }
        ):
            raise ValueError("HydrAMP blocker receipt self-hash drifted")
        blocked.append(generator_id)
    result = {
        "schema_version": "v37.generator-runtime-verification.1",
        "runtime_index_sha256": index["runtime_index_sha256"],
        "verified_generators": verified,
        "blocked_generators": blocked,
        "formal_runtime_set_ready": not blocked,
    }
    result["verification_sha256"] = sha256_json(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("config/environments/v37_generator_runtimes"),
    )
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        result = verify_local_runtime_set(args.workspace, args.output_root)
    else:
        result = build_local_runtime_set(args.workspace, args.output_root)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
