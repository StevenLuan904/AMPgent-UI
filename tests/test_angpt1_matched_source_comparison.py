from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

from build_angpt1_matched_source_comparison import wilson  # noqa: E402


def test_wilson_is_bounded_for_small_matched_arm() -> None:
    rate, low, high = wilson(1, 3)
    assert 0.0 <= low <= rate <= high <= 1.0


def test_wilson_zero_trials_is_explicit() -> None:
    assert wilson(0, 0) == (0.0, 0.0, 0.0)
