from analysis.postprocess_masked_round import occurrence_aliases, resolve_amplify
from analysis.r129_postprocess_contract import validate_generation_pair


def test_duplicate_raw_occurrences_keep_canonical_alias():
    rows = [
        {"action_id": "a1", "sequence": "SEQ", "target": "acea"},
        {"action_id": "a2", "sequence": "SEQ", "target": "acea"},
        {"action_id": "v1", "sequence": "SEQ", "target": "vegfa"},
        {"action_id": "v2", "sequence": "SEQ", "target": "vegfa"},
    ]
    out = occurrence_aliases(rows)
    assert [x["canonical_action_id"] for x in out] == ["a1", "a1", "v1", "v1"]
    assert out[1]["duplicate_of"] == "a1" and out[3]["duplicate_of"] == "v1"


def test_amplify_missing_is_explicit_not_default_success():
    row, status = resolve_amplify({"a": {"candidate_id": "a"}}, {}, "missing", "missing", "SEQ", "acea")
    assert row == {} and status == "missing_or_failed"


def test_mixed_parent_generations_are_checked_per_action():
    validate_generation_pair(9, 10)
    validate_generation_pair(10, 11)
