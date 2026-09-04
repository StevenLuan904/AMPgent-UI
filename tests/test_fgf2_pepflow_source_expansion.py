from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports/fgf2_pepflow_source_expansion_20260904_run2"


def test_primary_domain_close_and_pg_contract() -> None:
    close = json.loads((REPORT / "close_receipt.json").read_text())
    challenger = json.loads(
        (REPORT / "challenger_primary_eb85/receipt.json").read_text()
    )
    counts = close["stage_counts"]
    assert counts["proposal"] == counts["formal12"] == counts["display"] == 12
    assert counts["primary_support_ge_2"] == counts["primary_excellent"] == 10
    assert counts["challenger_reviewed"] == 12
    assert counts["qd_quality_eligible"] == 10
    assert counts["qd_new_cell"] == 3
    assert counts["materialized_candidate"] == 3
    assert counts["inserted_evaluation"] == 51
    assert challenger["reviewed_excellent_candidate_count"] == 10
    assert challenger["branch_summary"][0]["excellent_candidate_count"] == 10
    assert close["primary_calibration_reference_run_id"] == (
        "eb85d014-e7b3-5f6c-acab-34ac580b30e1"
    )
    assert close["sensitivity_calibrations"][
        "2b8f5096-5e59-5e27-a53d-692adadebd82"
    ]["support_ge_2"] == 2
    assert close["sensitivity_calibrations"][
        "1ff62322-46ce-5422-8e40-a328b729b984"
    ]["support_ge_2"] == 4


def test_prepared_queue_is_target_run_candidate_bound() -> None:
    with (REPORT / "coarse5_prepared/coarse5_prepared_queue.csv").open(
        encoding="utf-8", newline=""
    ) as handle:
        queue = list(csv.DictReader(handle))
    assert len(queue) == 3
    assert len({row["authoritative_candidate_id"] for row in queue}) == 3
    for row in queue:
        assert row["target_key"] == "fgf2"
        assert row["task_key"] == (
            "rosetta-coarse5:fgf2:"
            f"{row['run_id']}:{row['authoritative_candidate_id']}"
        )
        assert row["nstruct"] == "5"
        assert row["dispatch_allowed"] == "false"
        assert row["median_dg_gate"] == "-30"
