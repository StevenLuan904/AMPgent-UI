from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).with_name("export_analysis_snapshot.py")
SPEC = importlib.util.spec_from_file_location("export_analysis_snapshot", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import machinery guard
    raise RuntimeError(f"cannot load {SCRIPT_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DisplayEligibilityTests(unittest.TestCase):
    def test_candidates_are_partitioned_by_authoritative_sql_classification(self) -> None:
        visible, excluded = MODULE._display_candidate_rows(
            [
                {"id": "visible", "display_eligible": True},
                {"id": "replay", "display_eligible": False},
            ]
        )

        self.assertEqual([row["id"] for row in visible], ["visible"])
        self.assertEqual([row["id"] for row in excluded], ["replay"])

    def test_legacy_candidate_backed_replay_occurrence_is_excluded(self) -> None:
        row = {"candidate_id": "replay", "metadata_json": {}}

        self.assertTrue(
            MODULE._occurrence_is_historical_exact_replay(row, {"visible"})
        )

    def test_new_candidate_null_replay_occurrence_is_excluded_by_reason(self) -> None:
        row = {
            "candidate_id": None,
            "metadata_json": {
                "reason": "sequence_already_materialized_in_historical_run"
            },
        }

        self.assertTrue(MODULE._occurrence_is_historical_exact_replay(row, set()))

    def test_other_candidate_null_occurrence_remains_visible(self) -> None:
        row = {
            "candidate_id": None,
            "metadata_json": {"reason": "invalid_sequence"},
        }

        self.assertFalse(MODULE._occurrence_is_historical_exact_replay(row, set()))


if __name__ == "__main__":
    unittest.main()
