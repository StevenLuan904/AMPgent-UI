from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.merge_targeted_coarse5_inventory import (
    load_extra_rows,
    merge_rows,
)


def test_merge_keeps_authoritative_uuid_and_excludes_duplicate_sequence() -> None:
    base = [
        {
            "run_id": "base-run",
            "candidate_id": "11111111-1111-4111-8111-111111111111",
            "target_key": "acea",
            "sequence_sha256": "same",
            "rosetta_task_key": "rosetta5:acea:base",
        }
    ]
    extra = [
        {
            "run_id": "new-run",
            "candidate_id": "22222222-2222-4222-8222-222222222222",
            "target_key": "gyra",
            "sequence_sha256": "same",
            "rosetta_task_key": "rosetta-coarse5:gyra:new",
        },
        {
            "run_id": "new-run",
            "candidate_id": "33333333-3333-4333-8333-333333333333",
            "target_key": "gyra",
            "sequence_sha256": "new",
            "rosetta_task_key": "rosetta-coarse5:gyra:new2",
            "existing_decoy_count": "0",
            "remaining_decoy_count": "5",
        },
    ]
    rows, excluded = merge_rows(base, extra)
    assert len(rows) == 2
    assert excluded == {"global_sequence_duplicate": 1}
    assert rows[-1]["candidate_id"] == "33333333-3333-4333-8333-333333333333"


def test_load_extra_rows_rejects_proposal_identity(tmp_path: Path) -> None:
    queue = tmp_path / "queue.csv"
    queue.write_text(
        "run_id,candidate_id,sequence,sequence_sha256,target_key,source,qd_cell,qd_contribution,task_key,nstruct,existing_decoys,status\n"
        "run,proposal-1,ACDE,abc,gyra,PepFlow,q1,empty_cell,rosetta-coarse5:gyra:run:proposal-1,5,0,prepared_not_dispatched\n",
        encoding="utf-8",
    )
    score = tmp_path / "score.csv"
    score.write_text(
        "sequence_sha256,formal_12_complete,display_eligible,activity_model_support_count_calibrated\n"
        "abc,true,true,2\n",
        encoding="utf-8",
    )
    challenger = tmp_path / "challenger.csv"
    challenger.write_text(
        "sequence_sha256,challenger_conflict_status\nabc,no_conflict\n", encoding="utf-8"
    )
    close = tmp_path / "close.json"
    close.write_text(
        json.dumps({"postgresql": {"candidate_ids": ["proposal-1"]}}),
        encoding="utf-8",
    )
    try:
        load_extra_rows(queue, score, challenger, close, {"gyra": 100})
    except ValueError as exc:
        assert "non-authoritative" in str(exc)
    else:
        raise AssertionError("proposal id must be rejected")
