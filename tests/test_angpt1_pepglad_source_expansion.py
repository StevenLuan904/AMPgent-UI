from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

from generate_angpt1_pepglad_source_expansion import build_proposals, sha256_text


def test_source_expansion_is_one_aa_and_preserves_parent_identity() -> None:
    parent = {
        "parent_candidate_id": "00000000-0000-0000-0000-000000000001",
        "parent_sequence": "ACDEFG",
        "parent_sequence_sha256": sha256_text("ACDEFG"),
        "parent_qd_cell": "q1-h1-m1-l1",
    }
    donors = [
        {
            "donor_candidate_id": "pepglad-row",
            "donor_fragment": "W",
            "donor_residue": "W",
            "donor_artifact": "x",
            "donor_row_number": "2",
            "donor_row_sha256": "a",
        }
    ]
    rows = build_proposals(
        [parent],
        donors,
        set(),
        set(),
        target_key="acea",
        parent_run_id="run",
        limit=1,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["donor_source"] == "PepGLAD"
    assert row["operator_id"] == "acea-pepglad-source-expansion-1aa-v1"
    assert row["parent_candidate_id"] == parent["parent_candidate_id"]
    assert sum(a != b for a, b in zip(row["parent_sequence"], row["sequence"], strict=True)) == 1


def test_acea_source_expansion_receipts_keep_qd_and_shadow_semantics() -> None:
    root = Path("reports/acea_pepglad_source_expansion_20260904_v2")
    qd = json.loads((root / "qd_summary.json").read_text(encoding="utf-8"))
    close = json.loads((root / "close_receipt.json").read_text(encoding="utf-8"))
    prepared = json.loads(
        (root / "coarse5_prepared_receipt.json").read_text(encoding="utf-8")
    )
    for receipt in (qd, close, prepared):
        assert receipt["target_key"] == "acea"
        assert receipt["source"] == "PepGLAD"
        assert receipt["operator_id"] == "acea-pepglad-source-expansion-1aa-v1"
    assert qd["schema_version"] == "ampgent.acea-pepglad-source-expansion-qd.1"
    assert qd["new_cell_count"] == 4
    assert close["materialized_candidate_count"] == 4
    assert close["evaluation_count"] == 68
    assert prepared["status"] == "prepared_not_dispatched"
    assert prepared["dispatch_allowed"] is False
