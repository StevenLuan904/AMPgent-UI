import json
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_bytes
from pepagent.v34_provider_releases import (
    verify_knowledge_provider_release,
    verify_pepshot_provider_release,
)


def _write_json(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True).encode()
    path.write_bytes(payload)
    return sha256_bytes(payload)


def _artifact(path: Path, relative: str) -> dict:
    payload = (path / relative).read_bytes()
    return {
        "path": relative,
        "sha256": sha256_bytes(payload),
        "size_bytes": len(payload),
    }


def _knowledge_release(root: Path) -> dict[str, str]:
    release = root / "release-1"
    policy = {
        "policy_version": "amp-design-context-v2",
        "specification": {"distance": "D3"},
    }
    policy_sha = _write_json(release / "retrieval_policy_snapshot.json", policy)
    selection = {
        "authority": {
            "kind": "postgresql_table",
            "selected_record_count": 1,
        },
        "selected_policy_identity": "amp-design-context-v2",
        "selected_policy_record_content_sha256": "1" * 64,
        "selected_policy_specification_sha256": "2" * 64,
        "exact_identity_match": True,
    }
    selection_sha = _write_json(
        release / "retrieval_policy_selection_receipt.json", selection
    )
    roles = {
        "context_retrieval": {
            "role": "authoritative_retrieval_policy",
            "identity": "amp-design-context-v2",
            "record_content_sha256": "1" * 64,
            "specification_sha256": "2" * 64,
        },
        "agent_brief_advisory": {
            "must_not_be_used_as_retrieval_policy": True,
        },
    }
    roles_sha = _write_json(release / "policy_roles.json", roles)
    _write_json(release / "context_pack.json", {"policy_version": "amp-design-context-v2"})
    runtime_sha = _write_json(release / "runtime_manifest.json", {"valid": True})
    files = [
        "retrieval_policy_snapshot.json",
        "retrieval_policy_selection_receipt.json",
        "policy_roles.json",
        "context_pack.json",
        "runtime_manifest.json",
    ]
    manifest = {
        "contract_version": "amp-kb-runtime-release-v2",
        "revision": "kb-release-1",
        "frozen": True,
        "artifacts": [_artifact(release, path) for path in files],
    }
    manifest_sha = _write_json(release / "release_manifest.json", manifest)
    latest = {
        "revision": "kb-release-1",
        "relative_path": "release-1",
        "release_manifest_sha256": manifest_sha,
        "runtime_manifest_sha256": runtime_sha,
    }
    latest_sha = _write_json(root / "LATEST.json", latest)
    return {
        "latest": latest_sha,
        "manifest": manifest_sha,
        "runtime": runtime_sha,
        "policy": policy_sha,
        "selection": selection_sha,
        "roles": roles_sha,
    }


def test_knowledge_release_requires_exact_database_policy_receipt(tmp_path: Path) -> None:
    hashes = _knowledge_release(tmp_path)
    receipt = verify_knowledge_provider_release(
        tmp_path / "LATEST.json",
        expected_latest_sha256=hashes["latest"],
        expected_revision="kb-release-1",
        expected_release_manifest_sha256=hashes["manifest"],
        expected_runtime_manifest_sha256=hashes["runtime"],
        expected_policy_snapshot_sha256=hashes["policy"],
        expected_policy_selection_receipt_sha256=hashes["selection"],
        expected_policy_roles_sha256=hashes["roles"],
        expected_policy_record_content_sha256="1" * 64,
        expected_policy_specification_sha256="2" * 64,
    )
    assert receipt["provider_contract_verified"] is True
    selection = tmp_path / "release-1" / "retrieval_policy_selection_receipt.json"
    value = json.loads(selection.read_text())
    value["authority"]["selected_record_count"] = 2
    selection.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="artifact SHA drifted"):
        verify_knowledge_provider_release(
            tmp_path / "LATEST.json",
            expected_latest_sha256=hashes["latest"],
            expected_revision="kb-release-1",
            expected_release_manifest_sha256=hashes["manifest"],
            expected_runtime_manifest_sha256=hashes["runtime"],
            expected_policy_snapshot_sha256=hashes["policy"],
            expected_policy_selection_receipt_sha256=hashes["selection"],
            expected_policy_roles_sha256=hashes["roles"],
            expected_policy_record_content_sha256="1" * 64,
            expected_policy_specification_sha256="2" * 64,
        )


def _pepshot_release(root: Path) -> dict[str, str]:
    release = root / "releases" / "pepshot-release-1"
    runtime_sha = _write_json(release / "runtime.manifest.json", {"valid": True})
    images = []
    for index in range(9):
        relative = f"bundle/images/view-{index}.png"
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"png-{index}".encode())
        images.append(
            {
                "path": relative,
                "sha256": sha256_bytes(path.read_bytes()),
                "width": 10,
                "height": 10,
            }
        )
    files = ["runtime.manifest.json", *(item["path"] for item in images)]
    manifest = {
        "release_version": "pepshot-consumer-release-v1",
        "release_id": "pepshot-release-1",
        "normalized_source_revision": f"sha256:{'1' * 64}",
        "artifacts": [_artifact(release, path) for path in files],
        "bundle": {"bundle_id": "b" * 64},
        "review": {"bundle_id": "b" * 64},
        "agent_request": {"declared_image_count": 9, "images": images},
        "fixture": {"pdb_id": "1YCR", "peptide_chains": ["B"]},
    }
    manifest_sha = _write_json(release / "release.json", manifest)
    latest = {
        "normalized_source_revision": f"sha256:{'1' * 64}",
        "release_id": "pepshot-release-1",
        "release_manifest": "releases/pepshot-release-1/release.json",
        "release_manifest_sha256": manifest_sha,
    }
    latest_sha = _write_json(root / "LATEST.json", latest)
    return {"latest": latest_sha, "manifest": manifest_sha, "runtime": runtime_sha}


def test_pepshot_release_requires_nine_hashed_fixture_images(tmp_path: Path) -> None:
    hashes = _pepshot_release(tmp_path)
    receipt = verify_pepshot_provider_release(
        tmp_path / "LATEST.json",
        expected_latest_sha256=hashes["latest"],
        expected_source_revision=f"sha256:{'1' * 64}",
        expected_release_id="pepshot-release-1",
        expected_release_manifest_sha256=hashes["manifest"],
        expected_runtime_manifest_sha256=hashes["runtime"],
        expected_bundle_id="b" * 64,
    )
    assert receipt["decoded_image_contract_count"] == 9
    assert receipt["fixture_bundle_id"] == "b" * 64
    image = tmp_path / "releases" / "pepshot-release-1" / "bundle" / "images" / "view-0.png"
    image.write_bytes(b"drift")
    with pytest.raises(ValueError, match="artifact SHA drifted"):
        verify_pepshot_provider_release(
            tmp_path / "LATEST.json",
            expected_latest_sha256=hashes["latest"],
            expected_source_revision=f"sha256:{'1' * 64}",
            expected_release_id="pepshot-release-1",
            expected_release_manifest_sha256=hashes["manifest"],
            expected_runtime_manifest_sha256=hashes["runtime"],
            expected_bundle_id="b" * 64,
        )


def test_provider_release_rejects_machine_path_leak(tmp_path: Path) -> None:
    hashes = _knowledge_release(tmp_path)
    runtime = tmp_path / "release-1" / "runtime_manifest.json"
    runtime.write_text(json.dumps({"path": "C:\\machine\\python.exe"}))
    with pytest.raises(ValueError, match="artifact SHA drifted"):
        verify_knowledge_provider_release(
            tmp_path / "LATEST.json",
            expected_latest_sha256=hashes["latest"],
            expected_revision="kb-release-1",
            expected_release_manifest_sha256=hashes["manifest"],
            expected_runtime_manifest_sha256=hashes["runtime"],
            expected_policy_snapshot_sha256=hashes["policy"],
            expected_policy_selection_receipt_sha256=hashes["selection"],
            expected_policy_roles_sha256=hashes["roles"],
            expected_policy_record_content_sha256="1" * 64,
            expected_policy_specification_sha256="2" * 64,
        )
