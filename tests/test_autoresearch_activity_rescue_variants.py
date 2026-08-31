from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path


def _load_module():
    analysis_dir = Path(__file__).resolve().parents[1] / "analysis"
    sys.path.insert(0, str(analysis_dir))
    spec = importlib.util.spec_from_file_location(
        "_autoresearch_activity_rescue_variants",
        analysis_dir / "autoresearch_activity_rescue_variants.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_activity_rescue_preserves_new_family_lineage_metadata() -> None:
    module = _load_module()
    parent_sequence = "RTKKKKTTLRREGNRGKWGK"
    parent = {
        "branch_key": "acea",
        "generation": "7",
        "sequence": parent_sequence,
        "sequence_sha256": hashlib.sha256(parent_sequence.encode()).hexdigest(),
        "macrel_amp_probability": "0.2",
        "family_key_80_80": "seqfam80-new",
        "family_representative_sequence": "RTKKKKTTLRREGNRGKWGK",
        "new_family_relative_to_all_references": "true",
        "diversity_qualified": "true",
    }

    generated, _ = module._generate([parent], set(), "0" * 64)

    assert generated
    assert {row["family_key_80_80"] for row in generated} == {"seqfam80-new"}
    assert {row["new_family_relative_to_all_references"] for row in generated} == {
        "true"
    }
    assert {row["diversity_qualified"] for row in generated} == {"true"}
