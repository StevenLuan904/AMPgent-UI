from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

from generate_angpt1_pepglad_source_expansion import sha256_text
from generate_vegfa_pepmlm_peripheral_rescue import OPERATOR_ID, build_proposals


def _parent(sequence: str):
    return type(
        "Parent",
        (),
        {
            "id": "af5474ec-97c5-457d-93b3-b861f59705b2",
            "run_id": "98170454-be7f-5ab2-8add-979b9f11ddad",
            "sequence": sequence,
            "sequence_sha256": sha256_text(sequence),
        },
    )()


def test_peripheral_operator_preserves_ww_and_is_bounded() -> None:
    parent = _parent("SWWAELLAASGPRLRRAHK")
    rows = build_proposals(parent, "a" * 64, set(), set())
    assert len(rows) == 8
    assert all(row["sequence"][1:3] == "WW" for row in rows)
    assert all(row["operator_id"] == OPERATOR_ID for row in rows)
    assert all(
        sum(left != right for left, right in zip(parent.sequence, row["sequence"], strict=True))
        == 1
        for row in rows
    )
    assert all(row["parent_identity_kind"] == "authoritative_candidate" for row in rows)
    assert all(row["source_proposal_id"] == "" for row in rows)


def test_peripheral_operator_skips_exact_and_edit_history() -> None:
    parent = _parent("SWWAELLAASGPRLRRAHK")
    rows = build_proposals(
        parent,
        "a" * 64,
        {sha256_text("SWWAELLSASGPRLRRAHK")},
        {(3, "S"), (7, "T")},
    )
    assert len(rows) == 5
    assert all(row["to_residue"] != "S" or row["acceptor_start_zero_based"] != "3" for row in rows)
    assert all(row["to_residue"] != "T" or row["acceptor_start_zero_based"] != "7" for row in rows)


def test_generation_receipt_keeps_authoritative_parent_semantics() -> None:
    receipt = {
        "parent_identity_kind": "authoritative_candidate",
        "parent_run_id": "98170454-be7f-5ab2-8add-979b9f11ddad",
        "parent_candidate_id": "af5474ec-97c5-457d-93b3-b861f59705b2",
        "source_proposal_id": "",
    }
    assert json.loads(json.dumps(receipt))["source_proposal_id"] == ""


def test_close_receipt_has_one_pg_candidate_and_prepared_queue() -> None:
    receipt = json.loads(
        Path(
            "reports/vegfa_pepmlm_peripheral_rescue_20260904_v1/close_receipt.json"
        ).read_text(encoding="utf-8")
    )
    assert receipt["materialization"]["candidate_count"] == 1
    assert receipt["materialization"]["evaluation_count"] == 17
    assert receipt["materialization"]["subject_run_drift_count"] == 0
    assert receipt["qd"]["contribution"] == "empty_cell"
    assert receipt["structure_queue"]["task_key"].startswith(
        "rosetta-coarse5:vegfa:d710662b-a74c-50ec-b152-5ea4d72903d3:"
    )
    assert receipt["structure_queue"]["dispatch_allowed"] is False
