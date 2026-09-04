from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path("reports/gyrA_pepglad_source_expansion_20260904_run2")


def test_gyrA_pepglad_close_receipt_has_distinct_parent_and_calibration_roles() -> None:
    receipt = json.loads((ROOT / "close_receipt.json").read_text(encoding="utf-8"))
    assert receipt["source_parent_run_id"] == "e666339b-63ee-5efc-8733-a34b9c9f8694"
    assert receipt["calibration_reference_run_id"] == "15ea9977-4ea1-52a6-bcf0-f6e620803d19"
    assert receipt["candidate_count"] == 3
    assert receipt["evaluation_count"] == 51
    assert receipt["challenger_runtime_complete"] is False
    assert receipt["apex_status"] == "runtime_unavailable"
    assert receipt["apex_conflict_status"] == "not_assessed"


def test_gyrA_pepglad_queue_uses_authoritative_ids_and_numeric_gate() -> None:
    receipt = json.loads(
        (ROOT / "coarse5/coarse5_prepared_receipt.json").read_text(encoding="utf-8")
    )
    rows = list(
        csv.DictReader(
            (ROOT / "coarse5/coarse5_prepared_queue.csv").open(
                encoding="utf-8-sig", newline=""
            )
        )
    )
    assert len(rows) == 3
    assert receipt["authoritative_candidate_ids"] == [row["candidate_id"] for row in rows]
    assert len({row["task_key"] for row in rows}) == 3
    for row in rows:
        assert row["target_key"] == "gyra"
        assert row["run_id"] == "e42e5097-aa1b-55ab-a191-93a923d7f51a"
        assert row["task_key"] == (
            f"rosetta-coarse5:{row['target_key']}:{row['run_id']}:{row['candidate_id']}"
        )
        assert row["nstruct"] == "5"
        assert row["median_dg_gate"] == "-30"
        assert row["dispatch_allowed"] == "false"
        assert not row["candidate_id"].startswith("proposal-")
