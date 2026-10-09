import copy
import json
from pathlib import Path

import pytest

from analysis.generation_call_registration_helper import (
    assert_db_input_matches_request,
    build_registration_input,
    canonical_action_sha256,
    resolve_self_identity,
)

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "config" / "targets" / "ampgent_six_target_sequence_manifest_20260824.json"
RUN = "61d65749-dab0-55db-b62a-6834e4fe604d"
CAMPAIGN = "acea-vegfa-dual-autoresearch-20260923-v1"
ACEA_UUID = "6c45ae22-e578-4b3e-a885-dee1f11b6390"


def _request(tmp_path: Path, round_no=130, mutate=None) -> Path:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    target = next(x for x in manifest["targets"] if x["target_key"] == "acea")
    actions = [
        {
            "action_id": f"r{round_no}-acea-a",
            "action_kind": "masked_substitution",
            "target": "acea",
            "proposal_round": round_no,
            "parent_typed_uuid": "05aadda8-1d95-5257-9bea-d7e60bca17a3",
            "parent_sequence": "RHFKGDRTYWVLEALAVRHKK",
            "parent_lineage_generation": 11,
        },
        {
            "action_id": f"r{round_no}-acea-b",
            "action_kind": "masked_substitution",
            "target": "acea",
            "proposal_round": round_no,
            "parent_typed_uuid": "ec46a0b3-2419-5ae7-8f52-bcaca3ca6ac2",
            "parent_sequence": "KWKWWKEGAVEKVKSTREWKE",
            "parent_lineage_generation": 7,
        },
    ]
    data = {
        "operational_run_id": RUN,
        "proposal_round": round_no,
        "target_key": "acea",
        "target_identity": {"sequence_sha256": target["sequence_sha256"]},
        "action_plans": actions,
    }
    for action in actions:
        action["action_sha256"] = canonical_action_sha256(action)
    if mutate:
        mutate(data)
    out = tmp_path / f"round{round_no}_acea_request.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


def _kwargs(target_uuid=ACEA_UUID):
    return dict(run_id=RUN, campaign_id=CAMPAIGN, target_uuid=target_uuid, expected_round=130)


def test_valid_request_is_accepted(tmp_path):
    path = _request(tmp_path)
    result = build_registration_input(path, **_kwargs())
    assert result["proposal_round"] == 130
    assert len(result["action_ids"]) == 2


def test_r129_request_cannot_masquerade_as_r130(tmp_path):
    path = _request(tmp_path, round_no=129)
    with pytest.raises(ValueError, match="proposal_round mismatch"):
        build_registration_input(path, **_kwargs())


def test_duplicate_action_id_is_rejected(tmp_path):
    def duplicate(data):
        data["action_plans"][1]["action_id"] = data["action_plans"][0]["action_id"]

    path = _request(tmp_path, mutate=duplicate)
    with pytest.raises(ValueError, match="duplicate"):
        build_registration_input(path, **_kwargs())


def test_target_uuid_must_match_frozen_target_mapping(tmp_path):
    path = _request(tmp_path)
    with pytest.raises(ValueError, match="target UUID mismatch"):
        build_registration_input(
            path, **_kwargs(target_uuid="00000000-0000-0000-0000-000000000000")
        )


def test_db_action_mutation_is_rejected(tmp_path):
    path = _request(tmp_path)
    data = build_registration_input(path, **_kwargs())
    mutated = copy.deepcopy(data)
    mutated["action_ids"][0] = "r129-forged-action"
    with pytest.raises(ValueError, match="DB input mismatch at action_ids"):
        assert_db_input_matches_request(mutated, path, **_kwargs())


def test_resolve_self_identity_never_uses_parent_id_or_typo():
    preflight = {
        "candidates": [
            {
                "id": "candidate-uuid",
                "sequence": "RHFKGDRTYWVLEALAVRHKK",
                "generation": 11,
                "parent_id": "parent-uuid",
                "generator_call_id": "call-uuid",
                "metadata": {
                    "authoritative_candidate_id": "r129-acea-r127rhfk-mask16-seed2026209002"
                },
            }
        ]
    }
    resolved = resolve_self_identity(
        preflight, "r129-acea-r127rhfk-mask16-seed2026209002"
    )
    assert resolved["candidate_id"] == "candidate-uuid"
    assert resolved["candidate_id"] != resolved["parent_id"]
    with pytest.raises(ValueError, match="resolve uniquely"):
        resolve_self_identity(preflight, "r129-acea-r127rhfk-mask16-seed2026209003")


def test_resolve_self_identity_allows_unresolved_root_parent():
    preflight = {
        "candidates": [
            {
                "id": "root-candidate",
                "sequence": "RHFKGDRTYWVLEALAVRHKK",
                "generation": 11,
                "parent_id": None,
                "generator_call_id": "call-uuid",
                "metadata": {"authoritative_candidate_id": "external-root"},
            }
        ]
    }
    resolved = resolve_self_identity(preflight, "external-root")
    assert resolved["candidate_id"] == "root-candidate"
    assert resolved["parent_id"] is None


def test_canonical_action_sha_rejects_stale_declared_hash():
    action = {
        "action_id": "r134-test",
        "action_kind": "masked_substitution",
        "target": "acea",
        "proposal_round": 134,
        "parent_typed_uuid": "candidate-uuid",
        "parent_sequence": "RHFKGDRTYWVLEALAVRHKK",
        "parent_lineage_generation": 11,
        "lineage_generation": 12,
        "mutation_positions": [10],
        "action_seed": 2026214002,
    }
    action["action_sha256"] = canonical_action_sha256(action)
    assert canonical_action_sha256(action) == action["action_sha256"]
    action["rationale"] = "changed"
    assert canonical_action_sha256(action) != action["action_sha256"]


def test_worker_unsupported_or_missing_action_kind_is_rejected(tmp_path):
    def missing(data):
        data["action_plans"][0].pop("action_kind", None)

    with pytest.raises(ValueError, match="action_kind"):
        build_registration_input(_request(tmp_path, mutate=missing), **_kwargs())

    def unsupported(data):
        data["action_plans"][0]["action_kind"] = "not_a_worker_action"
        data["action_plans"][0]["action_sha256"] = canonical_action_sha256(
            data["action_plans"][0]
        )

    with pytest.raises(ValueError, match="action_kind"):
        build_registration_input(_request(tmp_path, mutate=unsupported), **_kwargs())
