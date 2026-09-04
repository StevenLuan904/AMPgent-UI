from __future__ import annotations

import csv
import json
from pathlib import Path

from analysis.prepare_gyra_pepflow_qd_neighbor_pg import _strict_rows

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "gyrA_pepflow_qd_neighbor_next_20260904"


def test_gyrA_strict_intersection_and_pg_closure_receipts() -> None:
    scores, challengers = _strict_rows(REPORT)
    assert len(scores) == 8
    assert len(challengers) == 8
    assert all(row["formal_12_complete"] == "true" for row in scores)
    assert all(row["display_eligible"] == "true" for row in scores)
    assert all(int(row["activity_model_support_count_calibrated"]) >= 2 for row in scores)
    assert {row["challenger_conflict_status"] for row in challengers} == {"no_conflict"}
    assert len({row["sequence_sha256"] for row in scores}) == 8

    history = json.loads((REPORT / "pg_exact_history_receipt.json").read_text(encoding="utf-8"))
    readback = json.loads(
        (REPORT / "pg_materialization_readback.json").read_text(encoding="utf-8")
    )
    coarse = json.loads(
        (REPORT / "coarse5_prepared" / "coarse5_prepared_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert history["strict_intersection_count"] == 8
    assert history["candidate_hit_count"] == 8
    assert history["already_materialized_count"] == 8
    assert history["pg_new_count"] == 8
    assert history["rejected_occurrence_count"] == 0
    assert readback["complete"] is True
    assert readback["evaluation_count"] == 136
    assert readback["evaluation_count_per_candidate"] == [17] * 8
    assert readback["drift"] == 0
    assert coarse["candidate_count"] == 8
    assert coarse["task_key_count"] == 8
    assert coarse["dispatch_allowed"] is False

    with (REPORT / "coarse5_prepared" / "coarse5_prepared_queue.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        queue = list(csv.DictReader(stream))
    assert all(row["status"] == "prepared_not_dispatched" for row in queue)
    assert all(row["nstruct"] == "5" for row in queue)
    assert all(row["task_key"].startswith("rosetta-coarse5:gyra:") for row in queue)
