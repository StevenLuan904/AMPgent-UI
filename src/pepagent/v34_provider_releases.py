from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pepagent.provenance.hashing import sha256_bytes, sha256_json

_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?:^|[\"'])[A-Za-z]:[\\/]")
_UNC_PATH = re.compile(r"(?:^|[\"'])\\\\[^\\]")


def _json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"v34 provider release file is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"v34 provider release JSON is invalid: {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"v34 provider release JSON must be an object: {path.name}")
    return value


def _relative_file(root: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute():
        raise ValueError("v34 provider release path must be non-empty and relative")
    root = root.resolve()
    path = (root / Path(relative)).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError("v34 provider release path escaped its immutable root") from error
    if not path.is_file():
        raise ValueError(f"v34 provider release artifact is missing: {relative}")
    return path


def _verify_artifact_table(
    release_root: Path, artifacts: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    if not artifacts:
        raise ValueError("v34 provider release artifact table is empty")
    verified: list[dict[str, Any]] = []
    paths: set[str] = set()
    for item in artifacts:
        relative = str(item.get("path", ""))
        if relative in paths:
            raise ValueError("v34 provider release contains duplicate artifact paths")
        paths.add(relative)
        path = _relative_file(release_root, relative)
        payload = path.read_bytes()
        expected_sha256 = str(item.get("sha256", ""))
        if sha256_bytes(payload) != expected_sha256:
            raise ValueError(f"v34 provider release artifact SHA drifted: {relative}")
        declared_size = item.get("size_bytes", item.get("bytes", -1))
        if int(declared_size) != len(payload):
            raise ValueError(f"v34 provider release artifact size drifted: {relative}")
        if path.suffix.lower() == ".json":
            text = payload.decode("utf-8")
            if _WINDOWS_ABSOLUTE_PATH.search(text) or _UNC_PATH.search(text):
                raise ValueError(
                    f"v34 provider release JSON leaks an absolute machine path: {relative}"
                )
        verified.append(
            {"path": relative, "sha256": expected_sha256, "size_bytes": len(payload)}
        )
    return verified


def verify_knowledge_provider_release(
    latest_path: Path,
    *,
    expected_latest_sha256: str,
    expected_revision: str,
    expected_release_manifest_sha256: str,
    expected_runtime_manifest_sha256: str,
    expected_policy_snapshot_sha256: str,
    expected_policy_selection_receipt_sha256: str,
    expected_policy_roles_sha256: str,
    expected_policy_record_content_sha256: str,
    expected_policy_specification_sha256: str,
) -> dict[str, Any]:
    """Verify the provider-owned knowledge release without interpreting advisory policy."""
    latest_bytes = latest_path.read_bytes()
    if sha256_bytes(latest_bytes) != expected_latest_sha256:
        raise ValueError("v34 knowledge LATEST pointer drifted")
    latest = _json(latest_path)
    if latest.get("revision") != expected_revision:
        raise ValueError("v34 knowledge release revision drifted")
    if latest.get("release_manifest_sha256") != expected_release_manifest_sha256:
        raise ValueError("v34 knowledge release manifest identity drifted")
    if latest.get("runtime_manifest_sha256") != expected_runtime_manifest_sha256:
        raise ValueError("v34 knowledge runtime manifest identity drifted")
    release_root = latest_path.parent / str(latest.get("relative_path", ""))
    release_manifest_path = _relative_file(release_root, "release_manifest.json")
    release_manifest_bytes = release_manifest_path.read_bytes()
    if sha256_bytes(release_manifest_bytes) != expected_release_manifest_sha256:
        raise ValueError("v34 knowledge release manifest bytes drifted")
    manifest = _json(release_manifest_path)
    if manifest.get("contract_version") != "amp-kb-runtime-release-v2":
        raise ValueError("v34 knowledge release contract version drifted")
    if manifest.get("revision") != expected_revision or manifest.get("frozen") is not True:
        raise ValueError("v34 knowledge release is not the frozen revision")
    verified = _verify_artifact_table(release_root, manifest.get("artifacts", []))
    artifact_sha = {item["path"]: item["sha256"] for item in verified}
    expected_roles = {
        "runtime_manifest.json": expected_runtime_manifest_sha256,
        "retrieval_policy_snapshot.json": expected_policy_snapshot_sha256,
        "retrieval_policy_selection_receipt.json": (
            expected_policy_selection_receipt_sha256
        ),
        "policy_roles.json": expected_policy_roles_sha256,
    }
    if any(artifact_sha.get(role) != digest for role, digest in expected_roles.items()):
        raise ValueError("v34 knowledge release role hash drifted")

    snapshot = _json(release_root / "retrieval_policy_snapshot.json")
    selection = _json(release_root / "retrieval_policy_selection_receipt.json")
    roles = _json(release_root / "policy_roles.json")
    pack = _json(release_root / "context_pack.json")
    if selection.get("authority", {}).get("kind") != "postgresql_table":
        raise ValueError("v34 knowledge policy selection is not database-authoritative")
    if selection.get("authority", {}).get("selected_record_count") != 1:
        raise ValueError("v34 knowledge release lacks a unique active retrieval policy")
    if selection.get("exact_identity_match") is not True:
        raise ValueError("v34 knowledge policy selection identity was not exact")
    if (
        selection.get("selected_policy_record_content_sha256")
        != expected_policy_record_content_sha256
        or selection.get("selected_policy_specification_sha256")
        != expected_policy_specification_sha256
    ):
        raise ValueError("v34 knowledge database policy receipt drifted")
    retrieval_role = roles.get("context_retrieval", {})
    advisory_role = roles.get("agent_brief_advisory", {})
    if (
        retrieval_role.get("role") != "authoritative_retrieval_policy"
        or retrieval_role.get("record_content_sha256")
        != expected_policy_record_content_sha256
        or retrieval_role.get("specification_sha256")
        != expected_policy_specification_sha256
        or advisory_role.get("must_not_be_used_as_retrieval_policy") is not True
    ):
        raise ValueError("v34 knowledge provider policy roles are ambiguous")
    identity = str(retrieval_role.get("identity", ""))
    if (
        pack.get("policy_version") != identity
        or snapshot.get("policy_version") != identity
        or selection.get("selected_policy_identity") != identity
    ):
        raise ValueError("v34 knowledge pack and retrieval policy identities differ")
    receipt: dict[str, Any] = {
        "schema_version": "1.0",
        "provider": "amp-system-kb",
        "latest_sha256": expected_latest_sha256,
        "revision": expected_revision,
        "release_manifest_sha256": expected_release_manifest_sha256,
        "runtime_manifest_sha256": expected_runtime_manifest_sha256,
        "retrieval_policy_identity": identity,
        "retrieval_policy_snapshot_sha256": expected_policy_snapshot_sha256,
        "retrieval_policy_selection_receipt_sha256": (
            expected_policy_selection_receipt_sha256
        ),
        "policy_roles_sha256": expected_policy_roles_sha256,
        "verified_artifact_count": len(verified),
        "machine_path_free": True,
        "provider_contract_verified": True,
    }
    receipt["receipt_sha256"] = sha256_json(receipt)
    return receipt


def verify_pepshot_provider_release(
    latest_path: Path,
    *,
    expected_latest_sha256: str,
    expected_source_revision: str,
    expected_release_id: str,
    expected_release_manifest_sha256: str,
    expected_runtime_manifest_sha256: str,
    expected_bundle_id: str,
) -> dict[str, Any]:
    """Verify PepShot's provider-owned release and real fixed visual fixture."""
    latest_bytes = latest_path.read_bytes()
    if sha256_bytes(latest_bytes) != expected_latest_sha256:
        raise ValueError("v34 PepShot LATEST pointer drifted")
    latest = _json(latest_path)
    if latest.get("normalized_source_revision") != expected_source_revision:
        raise ValueError("v34 PepShot source revision drifted")
    if latest.get("release_id") != expected_release_id:
        raise ValueError("v34 PepShot release identity drifted")
    if latest.get("release_manifest_sha256") != expected_release_manifest_sha256:
        raise ValueError("v34 PepShot release manifest identity drifted")
    manifest_path = _relative_file(latest_path.parent, str(latest.get("release_manifest", "")))
    manifest_bytes = manifest_path.read_bytes()
    if sha256_bytes(manifest_bytes) != expected_release_manifest_sha256:
        raise ValueError("v34 PepShot release manifest bytes drifted")
    release_root = manifest_path.parent
    manifest = _json(manifest_path)
    if manifest.get("release_version") != "pepshot-consumer-release-v1":
        raise ValueError("v34 PepShot release contract version drifted")
    if (
        manifest.get("release_id") != expected_release_id
        or manifest.get("normalized_source_revision") != expected_source_revision
    ):
        raise ValueError("v34 PepShot release manifest identity drifted")
    verified = _verify_artifact_table(release_root, manifest.get("artifacts", []))
    artifact_sha = {item["path"]: item["sha256"] for item in verified}
    if artifact_sha.get("runtime.manifest.json") != expected_runtime_manifest_sha256:
        raise ValueError("v34 PepShot runtime manifest drifted")
    bundle = manifest.get("bundle", {})
    review = manifest.get("review", {})
    if (
        bundle.get("bundle_id") != expected_bundle_id
        or review.get("bundle_id") != expected_bundle_id
    ):
        raise ValueError("v34 PepShot bundle and review identities differ")
    request = manifest.get("agent_request", {})
    images = request.get("images", [])
    if request.get("declared_image_count") != 9 or len(images) != 9:
        raise ValueError("v34 PepShot fixed fixture must expose exactly nine images")
    if len({str(item.get("path")) for item in images}) != 9:
        raise ValueError("v34 PepShot fixed fixture image paths are duplicated")
    for item in images:
        if artifact_sha.get(str(item.get("path"))) != item.get("sha256"):
            raise ValueError("v34 PepShot fixed fixture image hash drifted")
        if int(item.get("width", 0)) <= 0 or int(item.get("height", 0)) <= 0:
            raise ValueError("v34 PepShot fixed fixture image dimensions are invalid")
    fixture = manifest.get("fixture", {})
    if fixture.get("pdb_id") != "1YCR" or fixture.get("peptide_chains") != ["B"]:
        raise ValueError("v34 PepShot fixed fixture identity drifted")
    receipt: dict[str, Any] = {
        "schema_version": "1.0",
        "provider": "pepshot",
        "latest_sha256": expected_latest_sha256,
        "normalized_source_revision": expected_source_revision,
        "release_id": expected_release_id,
        "release_manifest_sha256": expected_release_manifest_sha256,
        "runtime_manifest_sha256": expected_runtime_manifest_sha256,
        # This identifies the provider's immutable release-verification fixture.
        # Candidate bundles produced later have their own independent identities.
        "fixture_bundle_id": expected_bundle_id,
        "verified_artifact_count": len(verified),
        "decoded_image_contract_count": 9,
        "machine_path_free": True,
        "provider_contract_verified": True,
    }
    receipt["receipt_sha256"] = sha256_json(receipt)
    return receipt
