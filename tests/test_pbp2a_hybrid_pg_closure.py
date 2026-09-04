from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from analysis.build_pbp2a_pepmlm_pepflow_coarse5_prepared import build
from analysis.prepare_pbp2a_hybrid_materialization_inputs import prepare


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_selects_four_gate_passers_and_binds_prepared_tasks(tmp_path: Path) -> None:
    report = tmp_path / "report"
    challenger_dir = report / "challenger"
    challenger_dir.mkdir(parents=True)
    sequences = ["AAAA", "CCCC", "DDDD", "EEEE"]
    hashes = [hashlib.sha256(sequence.encode()).hexdigest() for sequence in sequences]
    scores = [
        {
            "sequence_sha256": digest,
            "sequence": sequence,
            "formal_12_complete": "true",
            "display_eligible": "true",
            "activity_model_support_count_calibrated": "2",
        }
        for digest, sequence in zip(hashes, sequences, strict=True)
    ]
    challengers = [
        {"sequence_sha256": digest, "challenger_conflict_status": "no_conflict"}
        for digest in hashes
    ]
    qd = [
        {
            "sequence_sha256": digest,
            "actual_cell_id": f"cell-{index}",
            "contribution": "empty_cell",
            "new_cell": "true",
        }
        for index, digest in enumerate(hashes)
    ]
    _write_csv(report / "candidate_scores_calibrated.csv", scores)
    _write_csv(challenger_dir / "challenger_review.csv", challengers)
    _write_csv(report / "qd_candidates.csv", qd)
    selected_dir = report / "materialization_inputs"
    selection = prepare(report, selected_dir)
    assert selection["selected_count"] == 4

    readback = report / "pg_materialization_readback.json"
    readback.write_text(
        json.dumps(
            {
                "complete": True,
                "drift": 0,
                "run_id": "unused",
                "tool_call_id": "call",
                "candidate_count": 4,
                "evaluation_count": 68,
                "evaluation_count_per_candidate": [17, 17, 17, 17],
                "identity_drift_count": 0,
                "authoritative_candidates": [
                    {"candidate_id": f"candidate-{index}", "sequence_sha256": digest}
                    for index, digest in enumerate(hashes)
                ],
            }
        ),
        encoding="utf-8",
    )
    receipt = build(
        selected_dir / "candidate_scores.csv",
        report / "qd_candidates.csv",
        readback,
        report / "coarse5_prepared",
    )
    assert receipt["candidate_count"] == 4
    assert receipt["task_key_count"] == 4
    assert receipt["dispatch_allowed"] is False
    queue_path = report / "coarse5_prepared" / "coarse5_prepared_queue.csv"
    queue = list(csv.DictReader(queue_path.open(encoding="utf-8-sig")))
    assert all(row["nstruct"] == "5" for row in queue)
    assert all(row["status"] == "prepared_not_dispatched" for row in queue)
