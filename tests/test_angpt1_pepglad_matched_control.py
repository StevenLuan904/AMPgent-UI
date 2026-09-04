from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

from generate_angpt1_pepglad_matched_control import (  # noqa: E402
    _pepglad_donors,
    build_proposals,
    sha256_text,
)


def test_real_pepglad_rows_keep_artifact_and_row_hash(tmp_path: Path) -> None:
    source = tmp_path / "donors.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["source_arm", "donor_candidate_id", "donor_fragment"]
        )
        writer.writeheader()
        writer.writerow(
            {"source_arm": "PepGLAD", "donor_candidate_id": "d1", "donor_fragment": "AIEK"}
        )
        writer.writerow(
            {"source_arm": "PepFlow", "donor_candidate_id": "d2", "donor_fragment": "VVV"}
        )
    donors = _pepglad_donors(source)
    assert len(donors) == 4
    assert {row["donor_residue"] for row in donors} == set("AIEK")
    assert all(row["donor_source"] == "PepGLAD" for row in donors)
    assert all(len(row["donor_artifact_sha256"]) == 64 for row in donors)
    assert all(len(row["donor_source_row_sha256"]) == 64 for row in donors)


def test_matched_control_uses_same_parent_position_slots_and_exact_history() -> None:
    parent = {
        "parent_run_id": "11111111-1111-1111-1111-111111111111",
        "parent_candidate_id": "22222222-2222-2222-2222-222222222222",
        "parent_sequence": "PPVGQRPLDSYTYKHHHHP",
        "parent_sequence_sha256": sha256_text("PPVGQRPLDSYTYKHHHHP"),
        "parent_qd_cell": "q3-h0-m2-l2",
        "acceptor_start_zero_based": "0",
    }
    pepflow_rows = [
        dict(parent, sequence="APVGQRPLDSYTYKHHHHP"),
        dict(parent, acceptor_start_zero_based="9", sequence="PPVGQRPLDAYTYKHHHHP"),
    ]
    donors = [
        {
            "donor_candidate_id": "d1",
            "donor_source": "PepGLAD",
            "donor_fragment": "AIEK",
            "donor_residue": "I",
            "donor_fragment_offset": "1",
            "donor_artifact_path": "x",
            "donor_artifact_sha256": "a" * 64,
            "donor_source_row_number": "2",
            "donor_source_row_sha256": "b" * 64,
        },
        {
            "donor_candidate_id": "d1",
            "donor_source": "PepGLAD",
            "donor_fragment": "AIEK",
            "donor_residue": "E",
            "donor_fragment_offset": "2",
            "donor_artifact_path": "x",
            "donor_artifact_sha256": "a" * 64,
            "donor_source_row_number": "2",
            "donor_source_row_sha256": "b" * 64,
        },
    ]
    rows = build_proposals(pepflow_rows, donors, set(), set(), limit=2)
    assert len(rows) == 2
    assert [row["acceptor_start_zero_based"] for row in rows] == ["0", "9"]
    assert all(row["matched_source"] == "PepFlow_vs_PepGLAD" for row in rows)
    assert all(row["history_gate"].startswith("postgresql_exact") for row in rows)
