from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_bytes
from pepagent.v34_external_adapters import (
    KnowledgeAdapterContract,
    PepShotAdapterContract,
)
from pepagent.v34_preflight import verify_v34_external_contract_files


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
    knowledge_contract = KnowledgeAdapterContract(
        context_schema_sha256=sha256_bytes(b"knowledge-schema"),
        active_policy_sha256=sha256_bytes(b"knowledge-policy"),
    )
    pepshot_contract = PepShotAdapterContract(
        contract_sha256=sha256_bytes(b"pepshot-contract"),
        request_schema_sha256=sha256_bytes(b"request-schema"),
        review_schema_sha256=sha256_bytes(b"review-schema"),
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
    assert len(result["frozen_contract_hashes"]) == 5
    assert len(result["observed_entrypoint_hashes"]) == 5
    assert len(result["footprint_sha256"]) == 64


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
