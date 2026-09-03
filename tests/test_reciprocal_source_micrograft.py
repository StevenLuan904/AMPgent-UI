from __future__ import annotations

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
