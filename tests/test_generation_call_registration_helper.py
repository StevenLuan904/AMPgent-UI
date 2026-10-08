import copy
import json
from pathlib import Path

import pytest

from analysis.generation_call_registration_helper import (
    build_registration_input,
    assert_db_input_matches_request,
)

ROOT = Path(__file__).parents[1]
R130 = ROOT / 'tmp' / 'r130_prepared' / 'round130_acea_request.json'
R129 = ROOT / 'tmp' / 'r129_prepared' / 'round129_acea_request.json'
RUN = '61d65749-dab0-55db-b62a-6834e4fe604d'
CAMPAIGN = 'acea-vegfa-dual-autoresearch-20260923-v1'
ACEA_UUID = '6c45ae22-e578-4b3e-a885-dee1f11b6390'


def _request_copy(tmp_path: Path, source: Path, mutate=None) -> Path:
    data = json.loads(source.read_text(encoding='utf-8'))
    if mutate:
        mutate(data)
    out = tmp_path / source.name
    out.write_text(json.dumps(data), encoding='utf-8')
    return out


def _kwargs(path: Path, target_uuid=ACEA_UUID):
    return dict(request_path=path, run_id=RUN, campaign_id=CAMPAIGN,
                target_uuid=target_uuid, expected_round=130)


def test_r129_request_cannot_masquerade_as_r130():
    with pytest.raises(ValueError, match='proposal_round mismatch'):
        build_registration_input(R129, **_kwargs(R129))


def test_duplicate_action_id_is_rejected(tmp_path):
    def duplicate(data):
        data['action_plans'][1]['action_id'] = data['action_plans'][0]['action_id']
    path = _request_copy(tmp_path, R130, duplicate)
    with pytest.raises(ValueError, match='duplicate'):
        build_registration_input(path, **_kwargs(path))


def test_target_uuid_must_match_frozen_target_mapping():
    with pytest.raises(ValueError, match='target UUID mismatch'):
        build_registration_input(R130, **_kwargs(R130, target_uuid='00000000-0000-0000-0000-000000000000'))


def test_db_action_mutation_is_rejected(tmp_path):
    data = build_registration_input(R130, **_kwargs(R130))
    mutated = copy.deepcopy(data)
    mutated['action_ids'][0] = 'r129-forged-action'
    with pytest.raises(ValueError, match='DB input mismatch at action_ids'):
        assert_db_input_matches_request(mutated, R130, **_kwargs(R130))
