from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "analysis" / "build_pbp2a_matched_source_experiment.py"
    spec = importlib.util.spec_from_file_location("matched_source", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parent(candidate_id: str, sequence: str) -> dict[str, str]:
    return {
        "run_id": "9640722a-0f63-53e4-892d-ae42b0085445",
        "candidate_id": candidate_id,
        "sequence": sequence,
        "sequence_sha256": hashlib.sha256(sequence.encode()).hexdigest(),
        "generation": "131",
    }


def test_same_schedule_differs_only_by_source_and_preserves_length():
    module = _module()
    parents = [
        _parent("000c151b-9f58-4650-a2cc-db2c4b9ad429", "KHRKKWANNKIKVRKVNLDEKK"),
        _parent("004ffbc8-95d8-4102-9f0c-84a05b42e104", "RDKKWRQWEKHRFKHAYKTQKNEYK"),
    ]
    common = {
        "generation": 133,
        "seed": 20260904,
        "max_per_arm": 12,
        "history_hashes": set(),
        "prior_edits": set(),
    }
    glad = module.generate_arm(
        parents,
        [{"donor_candidate_id": "g", "donor_fragment": "AIEK"}],
        source="PepGLAD",
        **common,
    )
    flow = module.generate_arm(
        parents,
        [{"donor_candidate_id": "f", "donor_fragment": "P"}],
        source="PepFlow",
        **common,
    )
    assert [row["matched_pair_key"] for row in glad] == [row["matched_pair_key"] for row in flow]
    assert {row["edit_length"] for row in glad + flow} == {1}
    assert all(len(row["sequence"]) == len(row["parent_sequence"]) for row in glad + flow)
    assert {row["source_arm"] for row in glad + flow} == {"PepGLAD", "PepFlow"}


def test_history_hash_and_tried_edit_are_both_excluded():
    module = _module()
    parent = _parent("000c151b-9f58-4650-a2cc-db2c4b9ad429", "KHRKKWANNKIKVRKVNLDEKK")
    child = "AHRKKWANNKIKVRKVNLDEKK"
    result = module.generate_arm(
        [parent],
        [{"donor_candidate_id": "g", "donor_fragment": "A"}],
        source="PepGLAD",
        generation=133,
        seed=20260904,
        max_per_arm=12,
        history_hashes={hashlib.sha256(child.encode()).hexdigest()},
        prior_edits={(parent["sequence"], 0, "A")},
    )
    assert all(row["edit_position_1based"] != 1 for row in result)
    assert all(row["sequence"] != child for row in result)


def test_parent_validation_rejects_non_uuid_and_hash_drift():
    module = _module()
    parent = _parent("not-a-proposal-id", "KHRKKWANNKIKVRKVNLDEKK")
    try:
        module.validate_parent_rows(
            [parent], run_id=parent["run_id"], parent_generation=131
        )
    except ValueError as error:
        assert "authoritative UUID" in str(error)
    else:
        raise AssertionError("proposal-like parent identity was accepted")
