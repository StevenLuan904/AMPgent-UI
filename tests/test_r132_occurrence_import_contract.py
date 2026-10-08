import pytest

from analysis.r132_occurrence_import_contract import (
    assert_retry_identity,
    build_occurrence_rows,
)

ROOT = "f72805f4-7547-5017-a069-74042708d228"
RUN = "61d65749-dab0-55db-b62a-6834e4fe604d"


def _action(action_id, parent, seed, pos):
    return {
        "action_id": action_id,
        "action_kind": "masked_substitution",
        "action_sha256": f"sha-{action_id}",
        "parent_typed_uuid": parent,
        "seed": seed,
        "mutation_positions": [pos],
        "lineage_generation": 8,
    }


def test_four_raw_actions_preserve_target_rank_and_alias_candidate():
    kwk = "KWKWWKEGAVEKVKSTREWKE"
    rhfk = "RHFKGDRTYWVLEALAVRHKK"
    actions = {
        "acea": [_action("a1", "parent-kwk", 1000, 18), _action("a2", "parent-rhfk", 1002, 17)],
        "vegfa": [_action("v1", "parent-kwk", 1001, 18), _action("v2", "parent-rhfk", 1003, 17)],
    }
    generated = {
        "acea": [
            {"action_id": "a1", "sequence": kwk, "conditional_nll": 1.0, "conditional_ppl": 2.0},
            {"action_id": "a2", "sequence": rhfk, "conditional_nll": 3.0, "conditional_ppl": 4.0},
        ],
        "vegfa": [
            {"action_id": "v1", "sequence": kwk, "conditional_nll": 5.0, "conditional_ppl": 6.0},
            {"action_id": "v2", "sequence": rhfk, "conditional_nll": 7.0, "conditional_ppl": 8.0},
        ],
    }
    rows = build_occurrence_rows(
        run_id=RUN,
        target_tool_calls={"acea": "tc-a", "vegfa": "tc-v"},
        action_plans=actions,
        generated_rows=generated,
        canonical_candidate_by_sequence={kwk: "cand-kwk", rhfk: "cand-rhfk"},
        root_uuid=ROOT,
    )
    assert len(rows) == 4
    assert [(row["opaque_arm_label"], row["occurrence_rank"]) for row in rows] == [
        ("acea", 1),
        ("acea", 2),
        ("vegfa", 1),
        ("vegfa", 2),
    ]
    assert [row["candidate_id"] for row in rows] == [
        "cand-kwk",
        "cand-rhfk",
        "cand-kwk",
        "cand-rhfk",
    ]
    assert rows[2]["metadata_json"]["target"] == "vegfa"
    assert rows[2]["metadata_json"]["conditional_ppl"] == 6.0


def test_retry_same_call_rank_is_idempotent_but_payload_drift_fails():
    kwargs = dict(
        run_id=RUN,
        target_tool_calls={"acea": "tc-a"},
        action_plans={"acea": [_action("a1", "parent", 1000, 18)]},
        generated_rows={
            "acea": [
                {
                    "action_id": "a1",
                    "sequence": "ACDE",
                    "conditional_nll": 1.0,
                    "conditional_ppl": 2.0,
                }
            ]
        },
        canonical_candidate_by_sequence={"ACDE": "cand"},
        root_uuid=ROOT,
    )
    first = build_occurrence_rows(**kwargs)[0]
    second = build_occurrence_rows(**kwargs)[0]
    assert_retry_identity(first, second)
    changed = dict(second, metadata_json=dict(second["metadata_json"], conditional_ppl=9.0))
    with pytest.raises(ValueError, match="payload drifted"):
        assert_retry_identity(first, changed)


@pytest.mark.parametrize("sequence", ["", "AC DE", "acde", "ACDX", "ACDB", "ACDZ"])
def test_rejects_noncanonical_sequence(sequence):
    base = dict(
        run_id=RUN,
        target_tool_calls={"acea": "tc-a"},
        action_plans={"acea": [_action("a1", "parent", 1000, 18)]},
        generated_rows={
            "acea": [
                {
                    "action_id": "a1",
                    "sequence": "ACDE",
                    "conditional_nll": 1.0,
                    "conditional_ppl": 2.0,
                }
            ]
        },
        canonical_candidate_by_sequence={"ACDE": "cand"},
        root_uuid=ROOT,
    )
    with pytest.raises(ValueError, match="canonical"):
        build_occurrence_rows(
            **dict(
                base,
                generated_rows={
                    "acea": [
                        {
                            "action_id": "a1",
                            "sequence": sequence,
                            "conditional_nll": 1.0,
                            "conditional_ppl": 2.0,
                        }
                    ]
                },
            )
        )


def test_rejects_action_set_and_target_set_drift():
    base = dict(
        run_id=RUN,
        target_tool_calls={"acea": "tc-a"},
        action_plans={"acea": [_action("a1", "parent", 1000, 18)]},
        generated_rows={
            "acea": [
                {
                    "action_id": "a1",
                    "sequence": "ACDE",
                    "conditional_nll": 1.0,
                    "conditional_ppl": 2.0,
                }
            ]
        },
        canonical_candidate_by_sequence={"ACDE": "cand"},
        root_uuid=ROOT,
    )
    with pytest.raises(ValueError, match="set mismatch"):
        build_occurrence_rows(
            **dict(
                base,
                generated_rows={
                    "acea": [
                        {
                            "action_id": "extra",
                            "sequence": "ACDE",
                            "conditional_nll": 1.0,
                            "conditional_ppl": 2.0,
                        }
                    ]
                },
            )
        )
    with pytest.raises(ValueError, match="target set"):
        build_occurrence_rows(**dict(base, target_tool_calls={"acea": "tc-a", "vegfa": "tc-v"}))
    with pytest.raises(ValueError, match="duplicate"):
        build_occurrence_rows(
            **dict(
                base,
                action_plans={
                    "acea": [_action("a1", "parent", 1000, 18), _action("a1", "parent", 1001, 19)]
                },
                generated_rows={
                    "acea": [
                        {
                            "action_id": "a1",
                            "sequence": "ACDE",
                            "conditional_nll": 1.0,
                            "conditional_ppl": 2.0,
                        },
                        {
                            "action_id": "a1",
                            "sequence": "ACDF",
                            "conditional_nll": 1.0,
                            "conditional_ppl": 2.0,
                        },
                    ]
                },
            )
        )


def test_rejects_missing_or_nonfinite_identity_scores():
    base = dict(
        run_id=RUN,
        target_tool_calls={"acea": "tc-a"},
        action_plans={"acea": [_action("a1", "parent", 1000, 18)]},
        generated_rows={
            "acea": [
                {
                    "action_id": "a1",
                    "sequence": "ACDE",
                    "conditional_nll": 1.0,
                    "conditional_ppl": 2.0,
                }
            ]
        },
        canonical_candidate_by_sequence={"ACDE": "cand"},
        root_uuid=ROOT,
    )
    with pytest.raises(ValueError, match="incomplete"):
        build_occurrence_rows(
            **dict(
                base,
                generated_rows={
                    "acea": [{"action_id": "a1", "sequence": "ACDE", "conditional_nll": 1.0}]
                },
            )
        )
    with pytest.raises(ValueError, match="non-finite"):
        build_occurrence_rows(
            **dict(
                base,
                generated_rows={
                    "acea": [
                        {
                            "action_id": "a1",
                            "sequence": "ACDE",
                            "conditional_nll": float("inf"),
                            "conditional_ppl": 2.0,
                        }
                    ]
                },
            )
        )
    with pytest.raises(ValueError, match="non-finite"):
        build_occurrence_rows(
            **dict(
                base,
                generated_rows={
                    "acea": [
                        {
                            "action_id": "a1",
                            "sequence": "ACDE",
                            "conditional_nll": True,
                            "conditional_ppl": 2.0,
                        }
                    ]
                },
            )
        )
