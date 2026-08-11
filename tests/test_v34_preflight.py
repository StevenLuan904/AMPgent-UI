import json
from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_bytes
from pepagent.v34_external_adapters import (
    KnowledgeAdapterContract,
    PepShotAdapterContract,
)
from pepagent.v34_preflight import verify_v34_external_contract_files


def _json_file(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(payload)
    return sha256_bytes(payload)


def _artifact(root: Path, relative: str) -> dict:
    payload = (root / relative).read_bytes()
    return {"path": relative, "sha256": sha256_bytes(payload), "bytes": len(payload)}


def _knowledge_provider_release(knowledge: Path) -> dict[str, str]:
    export = knowledge / "exports" / "ampgent-readonly"
    release = export / "release-1"
    policy = {"policy_version": "amp-design-context-v2"}
    policy_sha = _json_file(release / "retrieval_policy_snapshot.json", policy)
    selection = {
        "authority": {"kind": "postgresql_table", "selected_record_count": 1},
        "selected_policy_identity": "amp-design-context-v2",
        "selected_policy_record_content_sha256": "1" * 64,
        "selected_policy_specification_sha256": "2" * 64,
        "exact_identity_match": True,
    }
    selection_sha = _json_file(
        release / "retrieval_policy_selection_receipt.json", selection
    )
    roles = {
        "context_retrieval": {
            "role": "authoritative_retrieval_policy",
            "identity": "amp-design-context-v2",
            "record_content_sha256": "1" * 64,
            "specification_sha256": "2" * 64,
        },
        "agent_brief_advisory": {"must_not_be_used_as_retrieval_policy": True},
    }
    roles_sha = _json_file(release / "policy_roles.json", roles)
    _json_file(release / "context_pack.json", policy)
    runtime_sha = _json_file(release / "runtime_manifest.json", {"valid": True})
    files = [
        "retrieval_policy_snapshot.json",
        "retrieval_policy_selection_receipt.json",
        "policy_roles.json",
        "context_pack.json",
        "runtime_manifest.json",
    ]
    manifest = {
        "contract_version": "amp-kb-runtime-release-v2",
        "revision": "release-1",
        "frozen": True,
        "artifacts": [_artifact(release, path) for path in files],
    }
    manifest_sha = _json_file(release / "release_manifest.json", manifest)
    latest_sha = _json_file(
        export / "LATEST.json",
        {
            "revision": "release-1",
            "relative_path": "release-1",
            "release_manifest_sha256": manifest_sha,
            "runtime_manifest_sha256": runtime_sha,
        },
    )
    return {
        "latest": latest_sha,
        "manifest": manifest_sha,
        "runtime": runtime_sha,
        "policy": policy_sha,
        "selection": selection_sha,
        "roles": roles_sha,
    }


def _pepshot_provider_release(pepshot: Path) -> dict[str, str]:
    root = pepshot / "evidence" / "releases" / "pepshot-runtime-v1"
    release = root / "releases" / "pepshot-release-1"
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
    artifacts = []
    for relative in ["runtime.manifest.json", *(item["path"] for item in images)]:
        item = _artifact(release, relative)
        item["size_bytes"] = item.pop("bytes")
        artifacts.append(item)
    manifest = {
        "release_version": "pepshot-consumer-release-v1",
        "release_id": "pepshot-release-1",
        "normalized_source_revision": f"sha256:{'3' * 64}",
        "artifacts": artifacts,
        "bundle": {"bundle_id": "4" * 64},
        "review": {"bundle_id": "4" * 64},
        "agent_request": {"declared_image_count": 9, "images": images},
        "fixture": {"pdb_id": "1YCR", "peptide_chains": ["B"]},
    }
    manifest_sha = _json_file(release / "release.json", manifest)
    latest_sha = _json_file(
        root / "LATEST.json",
        {
            "normalized_source_revision": f"sha256:{'3' * 64}",
            "release_id": "pepshot-release-1",
            "release_manifest": "releases/pepshot-release-1/release.json",
            "release_manifest_sha256": manifest_sha,
        },
    )
    return {"latest": latest_sha, "manifest": manifest_sha, "runtime": runtime_sha}


def _write_fixture(
    root: Path,
) -> tuple[Path, Path, KnowledgeAdapterContract, PepShotAdapterContract]:
    knowledge = root / "knowledge"
    pepshot = root / "pepshot"
    files = {
        knowledge / "schemas" / "design_context.schema.json": b"knowledge-schema",
        knowledge / "policies" / "agent_context_defaults.json": b"knowledge-policy",
        knowledge / "kbctl.py": b"knowledge-cli",
        knowledge / "src" / "amp_kb" / "context_service.py": b"context-service",
        pepshot / "AGENT_TOOL.md": b"pepshot-contract",
        pepshot / "src" / "pepshot" / "schemas" / "agent-request.schema.json": b"request-schema",
        pepshot / "src" / "pepshot" / "schemas" / "review.schema.json": b"review-schema",
        pepshot / "src" / "pepshot" / "cli.py": b"pepshot-cli",
        pepshot / "src" / "pepshot" / "bundle.py": b"pepshot-bundle",
        pepshot / "src" / "pepshot" / "review.py": b"pepshot-review",
    }
    for path, payload in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    knowledge_release = _knowledge_provider_release(knowledge)
    pepshot_release = _pepshot_provider_release(pepshot)
    knowledge_contract = KnowledgeAdapterContract(
        context_schema_sha256=sha256_bytes(b"knowledge-schema"),
        active_policy_sha256=knowledge_release["policy"],
        latest_sha256=knowledge_release["latest"],
        release_revision="release-1",
        release_manifest_sha256=knowledge_release["manifest"],
        runtime_manifest_sha256=knowledge_release["runtime"],
        policy_selection_receipt_sha256=knowledge_release["selection"],
        policy_roles_sha256=knowledge_release["roles"],
        policy_record_content_sha256="1" * 64,
        policy_specification_sha256="2" * 64,
    )
    pepshot_contract = PepShotAdapterContract(
        contract_sha256=sha256_bytes(b"pepshot-contract"),
        request_schema_sha256=sha256_bytes(b"request-schema"),
        review_schema_sha256=sha256_bytes(b"review-schema"),
        latest_sha256=pepshot_release["latest"],
        source_revision=f"sha256:{'3' * 64}",
        release_id="pepshot-release-1",
        release_manifest_sha256=pepshot_release["manifest"],
        runtime_manifest_sha256=pepshot_release["runtime"],
        fixture_bundle_id="4" * 64,
    )
    return knowledge, pepshot, knowledge_contract, pepshot_contract


def test_external_contract_preflight_is_read_only_and_content_addressed(
    tmp_path: Path,
) -> None:
    knowledge, pepshot, knowledge_contract, pepshot_contract = _write_fixture(tmp_path)
    result = verify_v34_external_contract_files(
        knowledge_root=knowledge,
        pepshot_root=pepshot,
        knowledge_contract=knowledge_contract,
        pepshot_contract=pepshot_contract,
    )
    assert result["external_commands_executed"] is False
    assert len(result["frozen_contract_hashes"]) == 4
    assert len(result["observed_entrypoint_hashes"]) == 5
    assert set(result["source_manifest_sha256"]) == {"knowledge", "pepshot"}
    assert set(result["provider_release_receipts"]) == {"knowledge", "pepshot"}
    assert len(result["footprint_sha256"]) == 64

    second = tmp_path / "second"
    knowledge_2, pepshot_2, knowledge_contract_2, pepshot_contract_2 = _write_fixture(
        second
    )
    replay = verify_v34_external_contract_files(
        knowledge_root=knowledge_2,
        pepshot_root=pepshot_2,
        knowledge_contract=knowledge_contract_2,
        pepshot_contract=pepshot_contract_2,
    )
    assert replay["source_manifest_sha256"] == result["source_manifest_sha256"]
    assert replay["footprint_sha256"] == result["footprint_sha256"]


def test_external_contract_preflight_fails_closed_on_drift(tmp_path: Path) -> None:
    knowledge, pepshot, knowledge_contract, pepshot_contract = _write_fixture(tmp_path)
    (knowledge / "schemas" / "design_context.schema.json").write_bytes(b"drift")
    with pytest.raises(ValueError, match="knowledge_context_schema"):
        verify_v34_external_contract_files(
            knowledge_root=knowledge,
            pepshot_root=pepshot,
            knowledge_contract=knowledge_contract,
            pepshot_contract=pepshot_contract,
        )
