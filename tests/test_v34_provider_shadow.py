from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
import yaml

from pepagent.provenance.hashing import sha256_bytes
from pepagent.v34_provider_shadow import (
    build_provider_release_archive,
    build_shadow_replay_bundle,
    verify_provider_archive,
)

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_v34_provider_shadow.yaml"


def _json_file(path: Path, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, sort_keys=True).encode()
    path.write_bytes(encoded)
    return sha256_bytes(encoded)


def _artifact(root: Path, relative: str, *, size_key: str = "bytes") -> dict:
    payload = (root / relative).read_bytes()
    return {"path": relative, "sha256": sha256_bytes(payload), size_key: len(payload)}


def _knowledge_release(root: Path) -> tuple[Path, dict]:
    release = root / "release-1"
    policy_sha = _json_file(
        release / "retrieval_policy_snapshot.json",
        {"policy_version": "amp-design-context-v2"},
    )
    selection_sha = _json_file(
        release / "retrieval_policy_selection_receipt.json",
        {
            "authority": {"kind": "postgresql_table", "selected_record_count": 1},
            "selected_policy_identity": "amp-design-context-v2",
            "selected_policy_record_content_sha256": "1" * 64,
            "selected_policy_specification_sha256": "2" * 64,
            "exact_identity_match": True,
        },
    )
    roles_sha = _json_file(
        release / "policy_roles.json",
        {
            "context_retrieval": {
                "role": "authoritative_retrieval_policy",
                "identity": "amp-design-context-v2",
                "record_content_sha256": "1" * 64,
                "specification_sha256": "2" * 64,
            },
            "agent_brief_advisory": {"must_not_be_used_as_retrieval_policy": True},
        },
    )
    _json_file(
        release / "context_pack.json",
        {"policy_version": "amp-design-context-v2"},
    )
    runtime_sha = _json_file(release / "runtime_manifest.json", {"valid": True})
    files = [
        "retrieval_policy_snapshot.json",
        "retrieval_policy_selection_receipt.json",
        "policy_roles.json",
        "context_pack.json",
        "runtime_manifest.json",
    ]
    manifest_sha = _json_file(
        release / "release_manifest.json",
        {
            "contract_version": "amp-kb-runtime-release-v2",
            "revision": "release-1",
            "frozen": True,
            "artifacts": [_artifact(release, path) for path in files],
        },
    )
    latest_sha = _json_file(
        root / "LATEST.json",
        {
            "revision": "release-1",
            "relative_path": "release-1",
            "release_manifest_sha256": manifest_sha,
            "runtime_manifest_sha256": runtime_sha,
        },
    )
    return root, {
        "release_revision": "release-1",
        "latest_sha256": latest_sha,
        "release_manifest_sha256": manifest_sha,
        "runtime_manifest_sha256": runtime_sha,
        "active_policy_sha256": policy_sha,
        "policy_selection_receipt_sha256": selection_sha,
        "policy_roles_sha256": roles_sha,
        "policy_record_content_sha256": "1" * 64,
        "policy_specification_sha256": "2" * 64,
    }


def _pepshot_release(root: Path) -> tuple[Path, dict]:
    release = root / "releases" / "release-1"
    runtime_sha = _json_file(release / "runtime.manifest.json", {"valid": True})
    images = []
    for index in range(9):
        relative = f"bundle/images/{index}.png"
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(str(index).encode())
        images.append(
            {
                "path": relative,
                "sha256": sha256_bytes(path.read_bytes()),
                "width": 1,
                "height": 1,
            }
        )
    artifacts = [
        _artifact(release, relative, size_key="size_bytes")
        for relative in ["runtime.manifest.json", *(item["path"] for item in images)]
    ]
    manifest_sha = _json_file(
        release / "release.json",
        {
            "release_version": "pepshot-consumer-release-v1",
            "release_id": "release-1",
            "normalized_source_revision": f"sha256:{'3' * 64}",
            "artifacts": artifacts,
            "bundle": {"bundle_id": "4" * 64},
            "review": {"bundle_id": "4" * 64},
            "agent_request": {"declared_image_count": 9, "images": images},
            "fixture": {"pdb_id": "1YCR", "peptide_chains": ["B"]},
        },
    )
    latest_sha = _json_file(
        root / "LATEST.json",
        {
            "normalized_source_revision": f"sha256:{'3' * 64}",
            "release_id": "release-1",
            "release_manifest": "releases/release-1/release.json",
            "release_manifest_sha256": manifest_sha,
        },
    )
    return root, {
        "normalized_source_revision": f"sha256:{'3' * 64}",
        "release_id": "release-1",
        "latest_sha256": latest_sha,
        "release_manifest_sha256": manifest_sha,
        "runtime_manifest_sha256": runtime_sha,
        "fixed_fixture_bundle_id": "4" * 64,
    }


def test_shadow_contract_is_non_generative_and_not_authorized() -> None:
    contract = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert contract["execution_authorized"] is False
    assert contract["formal_v34_execution_authorized"] is False
    assert contract["submitted"] is False
    assert contract["run_id"] is None
    assert contract["implementation_revision"] is None
    assert contract["expected_graph"] == {
        "candidate_count": 0,
        "evaluation_count": 0,
        "tool_call_count": 4,
        "dependency_count": 5,
        "decision_count": 1,
        "decision_edge_count": 3,
        "artifact_count": 6,
        "lifecycle_event_count": 8,
    }
    assert all(contract["scientific_contract"].values())


def test_provider_archives_are_deterministic_and_portably_reverified(tmp_path: Path) -> None:
    knowledge_root, knowledge_contract = _knowledge_release(tmp_path / "knowledge")
    pepshot_root, pepshot_contract = _pepshot_release(tmp_path / "pepshot")
    archives = {
        "knowledge": build_provider_release_archive(knowledge_root),
        "pepshot": build_provider_release_archive(pepshot_root),
    }
    assert archives["knowledge"] == build_provider_release_archive(knowledge_root)
    assert archives["pepshot"] == build_provider_release_archive(pepshot_root)
    receipts = {
        "knowledge": verify_provider_archive(
            "knowledge", archives["knowledge"], knowledge_contract
        ),
        "pepshot": verify_provider_archive("pepshot", archives["pepshot"], pepshot_contract),
    }
    contract_bytes = b"benchmark_id: isolated-shadow\n"
    replay = build_shadow_replay_bundle(
        contract_bytes=contract_bytes,
        release_archives=archives,
        receipts=receipts,
    )
    assert replay["exact_replay"] is True
    assert replay["candidate_count"] == 0
    assert replay["evaluation_count"] == 0
    assert replay["provider_effectiveness_evaluated"] is False
    assert replay["formal_v34_authorized"] is False


def test_provider_archive_rejects_path_traversal_and_tampering(tmp_path: Path) -> None:
    pepshot_root, pepshot_contract = _pepshot_release(tmp_path / "pepshot")
    archive = build_provider_release_archive(pepshot_root)
    source = zipfile.ZipFile(io.BytesIO(archive), "r")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as target:
        for info in source.infolist():
            target.writestr(info, source.read(info))
        target.writestr("../escape.json", b"{}")
    with pytest.raises(ValueError, match="unsafe member path"):
        verify_provider_archive("pepshot", buffer.getvalue(), pepshot_contract)

    tampered_buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive), "r") as source_archive:
        with zipfile.ZipFile(
            tampered_buffer, "w", compression=zipfile.ZIP_STORED
        ) as target_archive:
            for info in source_archive.infolist():
                payload = source_archive.read(info)
                if info.filename == "LATEST.json":
                    payload += b" "
                target_archive.writestr(info, payload)
    with pytest.raises(ValueError, match="LATEST pointer drifted"):
        verify_provider_archive("pepshot", tampered_buffer.getvalue(), pepshot_contract)
