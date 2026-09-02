import importlib.util
import sys
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "analysis" / "autoresearch_challenger_review.py"
sys.path.insert(0, str(SOURCE.parent))
SPEC = importlib.util.spec_from_file_location("autoresearch_challenger_review_script", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_default_new_round_scope_can_cover_every_formal12_candidate() -> None:
    rows = [
        {"formal_12_complete": "true", "excellent_sequence_stage_calibrated": "false"},
        {"formal_12_complete": "true", "excellent_sequence_stage_calibrated": "true"},
        {"formal_12_complete": "false", "excellent_sequence_stage_calibrated": "true"},
    ]

    selected = MODULE._select_review_rows(rows, scope="formal12")

    assert selected == rows[:2]


def test_legacy_excellent_scope_remains_explicit() -> None:
    rows = [
        {"formal_12_complete": "true", "excellent_sequence_stage_calibrated": "false"},
        {"formal_12_complete": "true", "excellent_sequence_stage_calibrated": "true"},
    ]

    assert MODULE._select_review_rows(rows, scope="excellent") == rows[1:]
