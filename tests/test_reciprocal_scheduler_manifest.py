from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1] / "reports"
MANIFEST = ROOT / "reciprocal_micrograft_scheduler_ready_manifest_20260903.csv"
RECEIPT = ROOT / "reciprocal_micrograft_scheduler_ready_audit_receipt_20260903.json"


def test_manifest_has_authoritative_unique_identity_and_frozen_order() -> None:
    rows = list(csv.DictReader(MANIFEST.open(encoding="utf-8-sig", newline="")))
    assert len(rows) == 98
    assert len({row["sequence"] for row in rows}) == 98
    assert len({row["identity_key"] for row in rows}) == 98
    assert "candidate_id" not in rows[0]
    assert "authoritative_candidate_id" in rows[0]
    assert "source_proposal_id" in rows[0]
    uuid_pattern = re.compile(r"^[0-9a-f-]{36}$")
    assert all(uuid_pattern.match(row["authoritative_candidate_id"]) for row in rows)
    assert all(row["source_proposal_id"] for row in rows)
    assert all(row["scheduler_status"] == "new_ready" for row in rows)
    assert [int(row["new_ready_rank"]) for row in rows] == list(range(1, 99))
    assert {row["target_key"] for row in rows} == {
        "acea",
        "pbp2a",
        "vegfa",
        "fgf2",
        "gyra",
        "angpt1",
    }


def test_manifest_audit_receipt_matches_csv() -> None:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    digest = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    assert receipt["manifest_csv_sha256"] == digest
    assert receipt["identity_resolved_count"] == 98
    assert receipt["identity_unresolved_count"] == 0
    assert receipt["identity_drift_count"] == 0
    assert receipt["capacity_audit"]["dispatch_allowed"] is False
