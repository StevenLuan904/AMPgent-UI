from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path


def test_pbp2a_coarse5_merge_is_authoritative_and_non_dispatchable():
    root = Path(__file__).parents[1]
    report = root / "reports" / "pbp2a_structure_coarse5_merge_20260904"
    with (report / "coarse5_queue.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        rows = list(csv.DictReader(stream))
    receipt = json.loads(
        (report / "coarse5_queue_receipt.json").read_text(encoding="utf-8")
    )
    assert len(rows) == 16
    assert sum(row["queue_role"] == "new_append_candidate" for row in rows) == 8
    assert sum(row["queue_role"] == "existing_prepared" for row in rows) == 8
    assert len({(row["run_id"], row["authoritative_candidate_id"]) for row in rows}) == 16
    assert len({row["sequence_sha256"] for row in rows}) == 16
    assert len({row["task_key"] for row in rows}) == 16
    for row in rows:
        uuid.UUID(row["authoritative_candidate_id"])
        assert row["task_key"].startswith("rosetta-coarse5:")
        assert row["dispatch_allowed"] == "False"
    assert receipt["global_identity_unique"] is True
    assert receipt["global_sequence_unique"] is True
    assert receipt["global_task_key_unique"] is True
    assert receipt["dispatch_allowed"] is False


def test_pbp2a_remote_prepared_receipt_is_complete_and_non_dispatchable():
    root = Path(__file__).parents[1]
    payload = json.loads(
        (
            root
            / "reports"
            / "pbp2a_structure_coarse5_merge_20260904"
            / "remote_prepared_receipt.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["new_prepared_count"] == 8
    assert payload["remote_write"]["written_count"] == 8
    assert payload["remote_write"]["overwritten_count"] == 0
    assert payload["remote_write"]["dispatch_allowed"] is False
    assert all(item["status"] == "verified_present" for item in payload["entries"])
