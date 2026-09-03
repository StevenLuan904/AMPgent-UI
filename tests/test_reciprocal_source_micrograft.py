from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path


def _load():
    analysis = Path(__file__).resolve().parents[1] / "analysis"
    sys.path.insert(0, str(analysis))
    path = analysis / "reciprocal_source_micrograft.py"
    spec = importlib.util.spec_from_file_location("reciprocal_source_micrograft", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_micrograft_preserves_length_and_uses_one_or_two_residues():
    module = _load()
    acceptor = {"sequence": "KKLAAAGGKK", "candidate_id": "a", "family_key_80_80": "a"}
    donor = {
        "sequence": "VVVAAAAGGG",
        "candidate_id": "d",
        "family_key_80_80": "d",
        "generator_id": "pepflow",
    }
    result = module.generate([acceptor], [donor], set(), limit=4)
    assert result
    assert all(len(row["sequence"]) == len(acceptor["sequence"]) for row in result)
    assert all(row["micrograft_length"] in (1, 2) for row in result)
    assert all(row["generation"] == 1 for row in result)
    assert all(
        row["parent_sequence_sha256"]
        == hashlib.sha256(acceptor["sequence"].encode()).hexdigest()
        for row in result
    )


def test_target_key_is_explicit_and_acea_regression_remains():
    module = _load()
    row = {
        "target_key": "pbp2a",
        "display_eligible": "True",
        "formal_metrics_complete": "True",
        "toxinpred3_label": "Non-Toxin",
        "macrel_hemolysis_label": "low",
        "activity_model_support_count": "2",
        "sequence": "KKLAAAGGKK",
    }
    assert module.safe_active(row, "pbp2a")
    assert not module.safe_active(row, "acea")
