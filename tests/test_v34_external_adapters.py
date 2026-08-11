from pathlib import Path

import pytest

from pepagent.provenance.hashing import sha256_bytes
from pepagent.v34_evidence import build_v34_evidence_plan
from pepagent.v34_external_adapters import (
    KnowledgeAdapterContract,
    PepShotAdapterContract,
    build_knowledge_artifact_payloads,
    build_knowledge_command,
    build_pepshot_artifact_payloads,
    build_pepshot_command_plan,
    validate_knowledge_context_pack,
    validate_pepshot_evidence,
)
from pepagent.v34_preregistration import load_v34_preregistration

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_knowledge_pepshot_ablation_v34.yaml"


def _v34_plan() -> dict:
    manifest = load_v34_preregistration(CONFIG)
    return build_v34_evidence_plan(
        manifest.parent_cohort["members"],
        order_salt=manifest.factorial_design["arm_order_salt"],
        provider_governance=manifest.provider_governance,
    )


def test_knowledge_command_is_shell_free_and_explicit() -> None:
    command = build_knowledge_command(
        python_executable=Path("python"),
        kbctl_path=Path("kbctl.py"),
        target_key="acea",
        query="AceA AMP design",
        application="skin antimicrobial peptide design",
        output_path=Path("pack.json"),
        config_path=Path("kb.toml"),
    )
    assert command == [
        "python",
        "kbctl.py",
        "--config",
        "kb.toml",
        "context",
        "--target",
        "acea",
        "--query",
        "AceA AMP design",
        "--application",
        "skin antimicrobial peptide design",
        "--output",
        "pack.json",
        "--json",
    ]


def _knowledge_fixture() -> tuple[
    dict, bytes, bytes, dict, KnowledgeAdapterContract
]:
    schema = b'{"schema":"fixture"}'
    policy_bytes = b'{"policy_version":"amp-design-context-v2","rules":["frozen"]}'
    policy = {"policy_version": "amp-design-context-v2", "rules": ["frozen"]}
    contract = KnowledgeAdapterContract(
        context_schema_sha256=sha256_bytes(schema),
        active_policy_sha256=sha256_bytes(policy_bytes),
    )
    pack = {
        "task": {
            "target_key": "acea",
            "query": "AceA AMP design",
            "application": "skin antimicrobial peptide design",
        },
        "policy_version": "amp-design-context-v2",
        "target_brief": {
            "constraints": [{"distance": "D3", "evidence_grade": "E1"}]
        },
        "agent_brief": {},
        "design_rules": {
            "direct": [
                {
                    "card_id": "direct-1",
                    "status": "verified",
                    "distance": "D1",
                    "evidence_grade": "E3",
                    "evidence_refs": ["ev-1"],
                }
            ],
            "transfer": [
                {
                    "card_id": "transfer-1",
                    "status": "verified",
                    "distance": "D2",
                    "evidence_grade": "E2",
                    "evidence_refs": ["ev-2"],
                }
            ],
        },
        "evidence_index": [
            {
                "evidence_id": "ev-1",
                "source_id": "PMC1",
                "locator": {"kind": "passage", "value": "p1"},
                "asset_uri": "asset://PMC1",
            },
            {
                "evidence_id": "ev-2",
                "source_id": "PMC2",
                "locator": {"kind": "passage", "value": "p2"},
                "asset_uri": "asset://PMC2",
            },
        ],
        "warnings": ["E1 target constraints are advisory only."],
        "knowledge_gaps": [],
        "retrieval_trace_id": "trace_fixture_123456",
        "generated_at": "2026-08-11T00:00:00Z",
    }
    return pack, schema, policy_bytes, policy, contract


def test_knowledge_adapter_accepts_only_attributed_verified_rules() -> None:
    pack, schema, policy_bytes, policy, contract = _knowledge_fixture()
    result = validate_knowledge_context_pack(
        pack,
        schema_bytes=schema,
        policy_bytes=policy_bytes,
        policy_snapshot=policy,
        expected_task=pack["task"],
        contract=contract,
    )
    assert result["admitted_card_ids"] == ["direct-1", "transfer-1"]
    assert result["retrieval_trace_id"] == "trace_fixture_123456"


@pytest.mark.parametrize("drift", ["status", "distance", "evidence"])
def test_knowledge_adapter_fails_closed_on_admission_drift(drift: str) -> None:
    pack, schema, policy_bytes, policy, contract = _knowledge_fixture()
    rule = pack["design_rules"]["direct"][0]
    if drift == "status":
        rule["status"] = "candidate"
    elif drift == "distance":
        rule["distance"] = "D4"
    else:
        rule["evidence_refs"] = ["missing"]
    with pytest.raises(ValueError, match="v34 knowledge"):
        validate_knowledge_context_pack(
            pack,
            schema_bytes=schema,
            policy_bytes=policy_bytes,
            policy_snapshot=policy,
            expected_task=pack["task"],
            contract=contract,
        )


def test_knowledge_adapter_builds_exact_database_artifact_roles() -> None:
    pack, schema, policy_bytes, policy, contract = _knowledge_fixture()
    validation = validate_knowledge_context_pack(
        pack,
        schema_bytes=schema,
        policy_bytes=policy_bytes,
        policy_snapshot=policy,
        expected_task=pack["task"],
        contract=contract,
    )
    trace = {"retrieval_trace_id": pack["retrieval_trace_id"], "events": []}
    passages = {
        "retrieval_trace_id": pack["retrieval_trace_id"],
        "passages": [
            {
                "evidence_id": "ev-1",
                "content_sha256": "1" * 64,
                "locator": {"kind": "passage", "value": "p1"},
                "source_uri": "https://example.test/PMC1",
            },
            {
                "evidence_id": "ev-2",
                "content_sha256": "2" * 64,
                "locator": {"kind": "passage", "value": "p2"},
                "source_uri": "https://example.test/PMC2",
            },
        ],
    }
    artifacts = build_knowledge_artifact_payloads(
        context_pack=pack,
        retrieval_trace=trace,
        policy_snapshot=policy,
        policy_selection_receipt={
            "exact_identity_match": True,
            "selected_policy_identity": "amp-design-context-v2",
        },
        policy_roles={"context_retrieval": {"identity": "amp-design-context-v2"}},
        passage_manifest=passages,
        adapter_validation=validation,
        provider_release_receipt={"provider_contract_verified": True},
    )
    assert set(artifacts) == {
        "context_pack",
        "retrieval_trace",
        "policy_snapshot",
        "policy_selection_receipt",
        "policy_roles",
        "passage_manifest",
        "provider_release_receipt",
    }
    episode = next(item for item in _v34_plan()["episodes"] if item["knowledge_on"])
    contract_roles = next(
        item["required_artifact_roles"]
        for item in episode["tool_calls"]
        if item["tool_name"] == "v34-knowledge-context"
    )
    assert set(artifacts) == set(contract_roles)


def test_pepshot_command_plan_has_required_order() -> None:
    commands = build_pepshot_command_plan(
        executable=Path("pepshot"),
        spec_path=Path("spec.json"),
        bundle_path=Path("bundle"),
        review_path=Path("review.json"),
    )
    assert [item[1] for item in commands] == ["bundle", "verify", "validate-review"]


def _pepshot_fixture() -> dict:
    contract_bytes = b"contract"
    request_schema = b"request-schema"
    review_schema = b"review-schema"
    contract = PepShotAdapterContract(
        contract_sha256=sha256_bytes(contract_bytes),
        request_schema_sha256=sha256_bytes(request_schema),
        review_schema_sha256=sha256_bytes(review_schema),
    )
    image_bytes_sha = "a" * 64
    bundle_id = "b" * 64
    return {
        "contract_bytes": contract_bytes,
        "request_schema_bytes": request_schema,
        "review_schema_bytes": review_schema,
        "agent_request": {
            "bundle_id": bundle_id,
            "images": ["images/global.png"],
            "priority_images": ["images/global.png"],
        },
        "bundle_manifest": {
            "bundle_id": bundle_id,
            "artifacts": [
                {"path": "images/global.png", "sha256": image_bytes_sha}
            ],
            "views": [{"view_id": "global", "file": "global.png"}],
        },
        "coordinate_audit": {"spatial_findings": []},
        "image_manifest": [
            {
                "path": "images/global.png",
                "view_id": "global",
                "sha256": image_bytes_sha,
                "read_by_agent": True,
            }
        ],
        "review": {
            "review_version": "pepshot-visual-review-v1",
            "bundle_id": bundle_id,
            "status": "reviewed",
            "summary": "No coordinate-supported structural conflict was identified.",
            "flags": [
                {
                    "code": "other_structural_anomaly",
                    "severity": "info",
                    "confidence": 0.8,
                    "evidence": [
                        {"view_id": "global", "observation": "clear", "residues": []}
                    ]
                }
            ],
            "uncertainty": {"level": "low", "reason": "All requested views read."},
            "suggested_next_actions": ["none"],
            "scientific_boundary_acknowledged": True,
        },
        "verification_receipt": {
            "valid": True,
            "bundle_id": bundle_id,
            "checked_artifact_count": 1,
            "mismatches": [],
        },
        "review_validation_receipt": {
            "valid": True,
            "bundle_id": bundle_id,
            "errors": [],
        },
        "contract": contract,
    }


def test_pepshot_adapter_requires_every_requested_image_to_be_read() -> None:
    fixture = _pepshot_fixture()
    result = validate_pepshot_evidence(**fixture)
    assert result["requested_image_count"] == 1
    fixture["image_manifest"][0]["read_by_agent"] = False
    with pytest.raises(ValueError, match="was not read"):
        validate_pepshot_evidence(**fixture)


def test_pepshot_adapter_rejects_unread_citation_and_contract_drift() -> None:
    fixture = _pepshot_fixture()
    fixture["review"]["flags"][0]["evidence"][0]["view_id"] = "unread"
    with pytest.raises(ValueError, match="unread view"):
        validate_pepshot_evidence(**fixture)
    fixture = _pepshot_fixture()
    fixture["contract_bytes"] = b"changed"
    with pytest.raises(ValueError, match="revision drifted"):
        validate_pepshot_evidence(**fixture)


def test_pepshot_adapter_builds_exact_database_artifact_roles() -> None:
    fixture = _pepshot_fixture()
    validation = validate_pepshot_evidence(**fixture)
    artifacts = build_pepshot_artifact_payloads(
        agent_request=fixture["agent_request"],
        bundle_manifest=fixture["bundle_manifest"],
        coordinate_audit=fixture["coordinate_audit"],
        image_manifest=fixture["image_manifest"],
        review=fixture["review"],
        verification_receipt=fixture["verification_receipt"],
        review_validation_receipt=fixture["review_validation_receipt"],
        adapter_validation=validation,
        provider_release_receipt={
            "provider_contract_verified": True,
            # The provider release proves its own fixed fixture, not this candidate bundle.
            "fixture_bundle_id": "c" * 64,
        },
    )
    assert set(artifacts) == {
        "agent_request",
        "bundle_manifest",
        "coordinate_audit",
        "image_manifest",
        "validated_review",
        "provider_release_receipt",
    }
    assert artifacts["validated_review"]["adapter_validation"] == validation
    episode = next(item for item in _v34_plan()["episodes"] if item["pepshot_on"])
    contract_roles = next(
        item["required_artifact_roles"]
        for item in episode["tool_calls"]
        if item["tool_name"] == "v34-pepshot-review"
    )
    assert set(artifacts) == set(contract_roles)


def test_pepshot_artifacts_require_verified_provider_fixture_identity() -> None:
    fixture = _pepshot_fixture()
    validation = validate_pepshot_evidence(**fixture)
    kwargs = {
        "agent_request": fixture["agent_request"],
        "bundle_manifest": fixture["bundle_manifest"],
        "coordinate_audit": fixture["coordinate_audit"],
        "image_manifest": fixture["image_manifest"],
        "review": fixture["review"],
        "verification_receipt": fixture["verification_receipt"],
        "review_validation_receipt": fixture["review_validation_receipt"],
        "adapter_validation": validation,
    }
    with pytest.raises(ValueError, match="was not verified"):
        build_pepshot_artifact_payloads(
            **kwargs,
            provider_release_receipt={"fixture_bundle_id": "c" * 64},
        )
    with pytest.raises(ValueError, match="fixture identity is missing"):
        build_pepshot_artifact_payloads(
            **kwargs,
            provider_release_receipt={"provider_contract_verified": True},
        )
