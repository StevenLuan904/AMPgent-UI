from __future__ import annotations

import copy

import pytest

from pepagent.provenance.hashing import sha256_json, sha256_text
from pepagent.v37_provider_consumers import (
    KNOWLEDGE_ACTIVE_POLICY_SHA256,
    KNOWLEDGE_RELEASE_MANIFEST_SHA256,
    KNOWLEDGE_RELEASE_REVISION,
    KNOWLEDGE_RUNTIME_MANIFEST_SHA256,
    PEPSHOT_INSPECT_CONTRACT_ID,
    PEPSHOT_INSPECT_ROUTE,
    PEPSHOT_INSPECTION_SCHEMA_SHA256,
    PEPSHOT_RELEASE_ID,
    PEPSHOT_RELEASE_MANIFEST_SHA256,
    PEPSHOT_REQUEST_SCHEMA_SHA256,
    PEPSHOT_RUNTIME_MANIFEST_SHA256,
    PEPSHOT_SPATIAL_FINDING_SCHEMA_SHA256,
    build_v37_pepshot_inspect_request,
    consume_v37_knowledge_context_pack,
    consume_v37_pepshot_inspection,
    project_v37_knowledge_applicability,
)


def _candidate(candidate_id: str = "candidate-1") -> dict:
    sequence = "KKLLKLLKLLKK"
    return {
        "run_id": "run-1",
        "candidate_id": candidate_id,
        "sequence": sequence,
        "sequence_sha256": sha256_text(sequence),
    }


def _poses(candidate_id: str = "candidate-1") -> list[dict]:
    return [
        {
            "run_id": "run-1",
            "candidate_id": candidate_id,
            "pose_id": f"pose-{seed}",
            "boltz_seed": seed,
            "pair_iptm": score,
            "coordinate_path": f"poses/{seed}.cif",
            "coordinate_sha256": character * 64,
        }
        for seed, score, character in (
            (20270380, 0.2, "a"),
            (20270381, 0.8, "b"),
            (20270382, 0.5, "c"),
        )
    ]


def _inspect_request() -> dict:
    return build_v37_pepshot_inspect_request(
        candidate=_candidate(),
        poses=_poses(),
        receptor_chains=["A"],
        peptide_chains=["B"],
        pocket_residues=[{"chain": "A", "number": 42}],
    )


def test_pepshot_inspect_request_uses_true_median_pose_and_official_spec_only() -> None:
    request = _inspect_request()
    assert request["seed"] == 20270382
    assert request["structure_path"] == "poses/20270382.cif"
    assert request["metadata"]["three_seed_pair_iptm_median"] == 0.5
    assert set(request) == {
        "structure_path",
        "receptor_chains",
        "peptide_chains",
        "pocket_residues",
        "candidate_id",
        "seed",
        "metadata",
    }
    assert "review" not in request


def test_pepshot_inspect_representative_pose_uses_seed_tie_break() -> None:
    poses = _poses()
    poses[0]["pair_iptm"] = 0.5
    poses[1]["pair_iptm"] = 0.5
    poses[2]["pair_iptm"] = 0.8
    request = build_v37_pepshot_inspect_request(
        candidate=_candidate(),
        poses=poses,
        receptor_chains=["A"],
        peptide_chains=["B"],
        pocket_residues=[],
    )
    assert request["seed"] == 20270380


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda candidate, poses: candidate.update(review={}), "prefilled review"),
        (lambda candidate, poses: poses[0].update(candidate_id="other"), "another candidate"),
        (lambda candidate, poses: poses[0].update(coordinate_path="../escape.cif"), "escape"),
        (lambda candidate, poses: poses[0].update(coordinate_sha256="BAD"), "SHA-256"),
        (lambda candidate, poses: poses.pop(), "exactly three"),
    ],
)
def test_pepshot_inspect_request_rejects_prefill_identity_path_hash_and_missing_seed(
    mutator, message: str
) -> None:
    candidate = _candidate()
    poses = _poses()
    mutator(candidate, poses)
    with pytest.raises(ValueError, match=message):
        build_v37_pepshot_inspect_request(
            candidate=candidate,
            poses=poses,
            receptor_chains=["A"],
            peptide_chains=["B"],
            pocket_residues=[],
        )


def _inspection(verdict: str = "PASS") -> dict:
    request = _inspect_request()
    identity = {
        "inspection_version": "pepshot-agent-inspection-v1",
        "tool_version": "0.5.0.dev0",
        "source_sha256": request["metadata"]["source_sha256"],
        "coordinate_input": {"format": "mmcif"},
        "request_context": {
            "candidate_id": request["candidate_id"],
            "seed": request["seed"],
            "metadata": request["metadata"],
        },
        "audit": {
            "interface_plausibility": {"verdict": verdict},
            "spatial_finding_count": 0,
            "spatial_findings": [],
        },
        "spatial_finding_schema_sha256": PEPSHOT_SPATIAL_FINDING_SCHEMA_SHA256,
    }
    inspection = {
        **identity,
        "inspection_id": sha256_json(identity),
        "fallback_allowed": False,
        "route": PEPSHOT_INSPECT_ROUTE,
    }
    return {
        "request": request,
        "inspection": inspection,
        "contract_receipt": {
            "contract_id": PEPSHOT_INSPECT_CONTRACT_ID,
            "task": "inspect",
            "fallback_allowed": False,
            "route": PEPSHOT_INSPECT_ROUTE,
            "schema_sha256": {
                "request": PEPSHOT_REQUEST_SCHEMA_SHA256,
                "response": PEPSHOT_INSPECTION_SCHEMA_SHA256,
                "spatial_finding": PEPSHOT_SPATIAL_FINDING_SCHEMA_SHA256,
            },
        },
        "provider_release_receipt": {
            "provider_contract_verified": True,
            "release_id": PEPSHOT_RELEASE_ID,
            "release_manifest_sha256": PEPSHOT_RELEASE_MANIFEST_SHA256,
            "runtime_manifest_sha256": PEPSHOT_RUNTIME_MANIFEST_SHA256,
        },
    }


def _set_invalid_self_consistent_verdict(value: dict) -> None:
    inspection = value["inspection"]
    inspection["audit"]["interface_plausibility"]["verdict"] = "UNKNOWN"
    identity = {
        key: item
        for key, item in inspection.items()
        if key not in {"inspection_id", "fallback_allowed", "route"}
    }
    inspection["inspection_id"] = sha256_json(identity)


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [("PASS", "retain"), ("WARN", "insufficient"), ("FAIL", "reject")],
)
def test_pepshot_inspection_projects_frozen_verdict_without_fake_review(
    verdict: str, expected: str
) -> None:
    result = consume_v37_pepshot_inspection(**_inspection(verdict))
    assert result.disposition == expected
    assert result.interface_verdict == verdict
    assert result.contract_id == PEPSHOT_INSPECT_CONTRACT_ID


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda value: value["request"].update(review={}), "prefilled review"),
        (
            lambda value: value["inspection"]["request_context"].update(
                candidate_id="other"
            ),
            "another candidate",
        ),
        (
            lambda value: value["inspection"].update(source_sha256="f" * 64),
            "source structure SHA drifted",
        ),
        (
            lambda value: value["inspection"].update(inspection_id="f" * 64),
            "self-hash",
        ),
        (
            lambda value: value["inspection"].update(fallback_allowed=True),
            "enabled fallback",
        ),
        (
            lambda value: value["contract_receipt"]["schema_sha256"].update(
                response="f" * 64
            ),
            "schema identities drifted",
        ),
        (
            lambda value: value["provider_release_receipt"].update(release_id="other"),
            "release receipt drifted",
        ),
        (_set_invalid_self_consistent_verdict, "verdict is invalid"),
    ],
)
def test_pepshot_inspection_rejects_cross_candidate_source_hash_route_schema_and_enum(
    mutator, message: str
) -> None:
    payload = _inspection()
    mutator(payload)
    with pytest.raises(ValueError, match=message):
        consume_v37_pepshot_inspection(**payload)


def _knowledge() -> tuple[dict, dict, list[dict]]:
    pack = {
        "run_id": "run-1",
        "retrieval_trace_id": "trace-v37-1",
        "active_policy_sha256": KNOWLEDGE_ACTIVE_POLICY_SHA256,
        "positive_support_is_not_a_selection_score": True,
        "release_revision": KNOWLEDGE_RELEASE_REVISION,
        "release_manifest_sha256": KNOWLEDGE_RELEASE_MANIFEST_SHA256,
        "runtime_manifest_sha256": KNOWLEDGE_RUNTIME_MANIFEST_SHA256,
        "cards": [
            {
                "card_id": "warning-1",
                "status": "verified",
                "kind": "warning",
                "content_sha256": "3" * 64,
                "passage_ids": ["passage-1"],
            },
            {
                "card_id": "support-1",
                "status": "verified",
                "kind": "positive_support",
                "content_sha256": "4" * 64,
                "passage_ids": ["passage-2"],
            },
        ],
        "passages": [
            {
                "passage_id": "passage-1",
                "content_sha256": "5" * 64,
                "artifact_path": "passages/1.json",
            },
            {
                "passage_id": "passage-2",
                "content_sha256": "6" * 64,
                "artifact_path": "passages/2.json",
            },
        ],
    }
    release = {
        "provider_contract_verified": True,
        "release_revision": KNOWLEDGE_RELEASE_REVISION,
        "release_manifest_sha256": KNOWLEDGE_RELEASE_MANIFEST_SHA256,
        "runtime_manifest_sha256": KNOWLEDGE_RUNTIME_MANIFEST_SHA256,
        "active_policy_sha256": KNOWLEDGE_ACTIVE_POLICY_SHA256,
    }
    applicability = [
        {
            "run_id": "run-1",
            "candidate_id": "candidate-1",
            "card_id": "warning-1",
            "card_content_sha256": "3" * 64,
            "passage_ids": ["passage-1"],
            "applicability": "direct",
            "action": "adopt",
            "candidate_effect": "reject_candidate",
        },
        {
            "run_id": "run-1",
            "candidate_id": "candidate-1",
            "card_id": "support-1",
            "card_content_sha256": "4" * 64,
            "passage_ids": ["passage-2"],
            "applicability": "transfer",
            "action": "adopt",
            "candidate_effect": "annotate",
        },
    ]
    return pack, release, applicability


def test_knowledge_projection_preserves_lineage_and_warning_only_rejection() -> None:
    pack, release, applicability = _knowledge()
    result = project_v37_knowledge_applicability(
        candidate=_candidate(),
        context_pack=pack,
        applicability=applicability,
        provider_release_receipt=release,
    )
    assert result.disposition == "reject"
    assert result.rejected_card_ids == ()
    assert result.adopted_card_ids == ("warning-1", "support-1")
    assert result.candidate_rejection_card_ids == ("warning-1",)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda pack, release, app: app[0].update(candidate_id="other"), "another candidate"),
        (
            lambda pack, release, app: app[0].update(card_content_sha256="7" * 64),
            "card SHA drifted",
        ),
        (
            lambda pack, release, app: app[0].update(passage_ids=["missing"]),
            "unknown passage",
        ),
        (
            lambda pack, release, app: pack["passages"][0].update(
                artifact_path="../passage.json"
            ),
            "portable relative path",
        ),
        (
            lambda pack, release, app: app[1].update(
                candidate_effect="reject_candidate"
            ),
            "only an adopted directly applicable verified warning",
        ),
        (
            lambda pack, release, app: release.update(active_policy_sha256="8" * 64),
            "release receipt drifted",
        ),
        (
            lambda pack, release, app: pack.update(
                positive_support_is_not_a_selection_score=False
            ),
            "no-score boundary",
        ),
        (lambda pack, release, app: app.pop(), "decide every frozen context card"),
    ],
)
def test_knowledge_projection_rejects_cross_candidate_hash_path_enum_and_lineage_drift(
    mutator, message: str
) -> None:
    pack, release, applicability = _knowledge()
    pack = copy.deepcopy(pack)
    release = copy.deepcopy(release)
    applicability = copy.deepcopy(applicability)
    mutator(pack, release, applicability)
    with pytest.raises(ValueError, match=message):
        project_v37_knowledge_applicability(
            candidate=_candidate(),
            context_pack=pack,
            applicability=applicability,
            provider_release_receipt=release,
        )


def _knowledge_v3() -> tuple[dict, dict, dict]:
    query = {
        "schema_version": "v37.knowledge-query.1",
        "target_key": "AceA",
        "application": "v37_rapid_champion_generation",
        "query": "frozen v37 query",
    }
    pack = {
        "task": {
            "target_key": "acea",
            "query": query["query"],
            "application": "bacterial AceA inhibition and antibacterial validation",
        },
        "policy_version": "amp-design-context-v2",
        "target_brief": {},
        "agent_brief": {},
        "design_rules": {
            "direct": [],
            "transfer": [
                {
                    "card_id": "card-1",
                    "status": "candidate",
                    "evidence_refs": ["passage-1"],
                    "action": "annotate only",
                }
            ],
        },
        "evidence_index": [
            {
                "evidence_id": "passage-1",
                "source_id": "PMC1",
                "status": "candidate",
                "kind": "passage",
            }
        ],
        "warnings": [],
        "knowledge_gaps": [],
        "retrieval_trace_id": "trace-v37-v3",
        "generated_at": "2026-08-11T00:00:00+00:00",
    }
    release = {
        "provider_contract_verified": True,
        "release_revision": KNOWLEDGE_RELEASE_REVISION,
        "release_manifest_sha256": KNOWLEDGE_RELEASE_MANIFEST_SHA256,
        "runtime_manifest_sha256": KNOWLEDGE_RUNTIME_MANIFEST_SHA256,
        "active_policy_sha256": KNOWLEDGE_ACTIVE_POLICY_SHA256,
    }
    return pack, query, release


def test_knowledge_v3_consumer_preserves_provider_order_and_lineage() -> None:
    pack, query, release = _knowledge_v3()

    result = consume_v37_knowledge_context_pack(
        context_pack=pack,
        query_payload=query,
        candidate_ids=["candidate-2", "candidate-1"],
        provider_release_receipt=release,
    )

    assert result.retrieval_trace_id == "trace-v37-v3"
    assert result.cards[0]["card_id"] == "card-1"
    assert result.cards[0]["passage_ids"] == ["passage-1"]
    assert result.adoption_edges[0]["candidate_ids"] == [
        "candidate-2",
        "candidate-1",
    ]
    assert result.adoption_edges[0]["disposition"] == "used"


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda pack, query, release: pack["task"].update(query="other"), "query drifted"),
        (
            lambda pack, query, release: release.update(active_policy_sha256="0" * 64),
            "release receipt drifted",
        ),
        (
            lambda pack, query, release: pack["design_rules"]["transfer"][0].update(
                evidence_refs=["missing"]
            ),
            "evidence lineage drifted",
        ),
        (
            lambda pack, query, release: pack.update(unexpected=True),
            "top-level schema drifted",
        ),
    ],
)
def test_knowledge_v3_consumer_fails_closed_on_schema_or_lineage_drift(
    mutator, message: str
) -> None:
    pack, query, release = _knowledge_v3()
    mutator(pack, query, release)

    with pytest.raises(ValueError, match=message):
        consume_v37_knowledge_context_pack(
            context_pack=pack,
            query_payload=query,
            candidate_ids=["candidate-1"],
            provider_release_receipt=release,
        )


def test_knowledge_v3_consumer_rejects_duplicate_candidate_projection() -> None:
    pack, query, release = _knowledge_v3()

    with pytest.raises(ValueError, match="identity/order"):
        consume_v37_knowledge_context_pack(
            context_pack=pack,
            query_payload=query,
            candidate_ids=["candidate-1", "candidate-1"],
            provider_release_receipt=release,
        )
