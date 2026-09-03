from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "analysis" / "evaluate_pbp2a_matched_source_qd.py"
    spec = importlib.util.spec_from_file_location("matched_qd", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wilson_interval_is_bounded_and_contains_observed_rate():
    low, high = _module().wilson_interval(3, 12)
    assert 0 <= low <= 0.25 <= high <= 1


def test_zero_trial_interval_is_defined():
    assert _module().wilson_interval(0, 0) == (0.0, 0.0)
