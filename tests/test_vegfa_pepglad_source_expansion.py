from __future__ import annotations

import csv
import json
from pathlib import Path


def test_vegfa_negative_close_preserves_gate_diagnosis() -> None:
    root = Path("reports/vegfa_pepglad_source_expansion_20260904_run1")
    close = json.loads((root / "close_receipt.json").read_text(encoding="utf-8"))
    diagnosis = list(
        csv.DictReader(
            (root / "support_failure_diagnosis.csv").open(
                encoding="utf-8-sig", newline=""
            )
        )
    )
    assert close["target_key"] == "vegfa"
    assert close["formal12"]["score_all_complete"] == 12
    assert close["formal12"]["display_eligible"] == 6
    assert close["formal12"]["calibrated_support_ge_2"] == 4
    assert close["qd"] == {
        "eligible_batch_candidate_count": 0,
        "new_cell_count": 0,
        "incumbent_replacement_count": 0,
        "effective_archive_contribution": 0,
        "historical_run_modified": False,
    }
    assert len(diagnosis) == 4
    assert all(row["macrel_hemolysis_label"] == "high" for row in diagnosis)
    assert close["next_operator_selection"]["hard_gates_unchanged"] is True
