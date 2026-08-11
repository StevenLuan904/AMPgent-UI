from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from pepagent.provenance.hashing import sha256_bytes, sha256_json

_PROBE_SCRIPT = r"""
import hashlib
import importlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

requests = json.loads(sys.argv[1])
packages = []
for item in requests:
    record = {"requirement": item["requirement"], "module": item["module"]}
    try:
        distribution = importlib.metadata.distribution(item["distribution"])
        record["distribution"] = distribution.metadata["Name"]
        record["version"] = distribution.version
    except importlib.metadata.PackageNotFoundError:
        record["distribution"] = item["distribution"]
        record["version"] = None
    try:
        module = importlib.import_module(item["module"])
        module_file = getattr(module, "__file__", None)
        record["import_ok"] = True
        record["module_file_sha256"] = (
            hashlib.sha256(Path(module_file).read_bytes()).hexdigest()
            if module_file and Path(module_file).is_file()
            else None
        )
        record["import_error_type"] = None
    except Exception as error:
        record["import_ok"] = False
        record["module_file_sha256"] = None
        record["import_error_type"] = type(error).__name__
    packages.append(record)

print(json.dumps({
    "python_version": platform.python_version(),
    "python_implementation": platform.python_implementation(),
    "python_cache_tag": sys.implementation.cache_tag,
    "platform_system": platform.system(),
    "platform_release": platform.release(),
    "platform_machine": platform.machine(),
    "packages": packages,
}, sort_keys=True))
"""


def probe_python_runtime(
    *,
    python_executable: Path,
    role: str,
    source_manifest_sha256: str,
    requirements: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    """Probe one isolated Python runtime without encoding its machine-specific path."""
    executable = python_executable.resolve()
    if not executable.is_file():
        raise ValueError(f"v34 {role} Python executable is missing")
    requests = [
        {
            "requirement": requirement,
            "distribution": Requirement(requirement).name,
            "module": module,
        }
        for requirement, module in requirements
    ]
    completed = subprocess.run(
        [str(executable), "-I", "-c", _PROBE_SCRIPT, json.dumps(requests)],
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise ValueError(
            f"v34 {role} runtime probe failed with exit code {completed.returncode}"
        )
    try:
        observed = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"v34 {role} runtime probe returned invalid JSON") from error
    result = {
        "schema_version": "1.0",
        "role": role,
        "source_manifest_sha256": source_manifest_sha256,
        "python_executable_sha256": sha256_bytes(executable.read_bytes()),
        **observed,
    }
    result["runtime_manifest_sha256"] = sha256_json(result)
    return result


def augment_runtime_with_conda_metadata(
    manifest: Mapping[str, Any], *, conda_prefix: Path
) -> dict[str, Any]:
    """Bind import evidence to exact conda package records for non-wheel runtimes."""
    metadata_root = conda_prefix / "conda-meta"
    records: dict[str, dict[str, Any]] = {}
    record_hashes: list[dict[str, str]] = []
    for path in sorted(metadata_root.glob("*.json"), key=lambda item: item.name):
        payload = path.read_bytes()
        record = json.loads(payload)
        name = str(record.get("name", "")).lower().replace("_", "-")
        if not name or name in records:
            raise ValueError("v34 conda runtime has missing or duplicate package identity")
        records[name] = record
        record_hashes.append({"filename": path.name, "sha256": sha256_bytes(payload)})
    if not records:
        raise ValueError("v34 conda runtime metadata is empty")
    packages = []
    for package in manifest.get("packages", []):
        enriched = dict(package)
        name = Requirement(str(package["requirement"])).name.lower().replace("_", "-")
        record = records.get(name)
        if record is not None:
            enriched["version"] = str(record["version"])
            enriched["conda_build"] = str(record.get("build", ""))
            enriched["conda_subdir"] = str(record.get("subdir", ""))
            enriched["version_source"] = "conda-meta"
        packages.append(enriched)
    result = {
        key: value
        for key, value in manifest.items()
        if key != "runtime_manifest_sha256"
    }
    result["packages"] = packages
    result["conda_record_manifest_sha256"] = sha256_json(record_hashes)
    result["runtime_manifest_sha256"] = sha256_json(result)
    return result


def verify_python_runtime_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_role: str,
    expected_source_manifest_sha256: str,
    python_specifier: str,
) -> dict[str, Any]:
    """Fail closed when a probed runtime differs from its frozen executable contract."""
    if manifest.get("role") != expected_role:
        raise ValueError("v34 runtime role differs from its contract")
    if manifest.get("source_manifest_sha256") != expected_source_manifest_sha256:
        raise ValueError("v34 runtime is linked to a different source manifest")
    if Version(str(manifest.get("python_version"))) not in SpecifierSet(python_specifier):
        raise ValueError("v34 runtime Python version differs from its contract")
    packages = manifest.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("v34 runtime package manifest is empty")
    verified: list[dict[str, Any]] = []
    for package in packages:
        requirement = Requirement(str(package.get("requirement", "")))
        version = package.get("version")
        if version is None or Version(str(version)) not in requirement.specifier:
            raise ValueError(
                f"v34 runtime requirement is not satisfied: {requirement}"
            )
        if package.get("import_ok") is not True:
            raise ValueError(f"v34 runtime import failed: {package.get('module')}")
        verified.append(
            {
                "distribution": str(package["distribution"]),
                "version": str(version),
                "module": str(package["module"]),
                "module_file_sha256": package.get("module_file_sha256"),
            }
        )
    result = {
        "schema_version": "1.0",
        "role": expected_role,
        "source_manifest_sha256": expected_source_manifest_sha256,
        "runtime_manifest_sha256": manifest["runtime_manifest_sha256"],
        "python_specifier": python_specifier,
        "verified_packages": verified,
        "verified": True,
    }
    result["verification_sha256"] = sha256_json(result)
    return result
