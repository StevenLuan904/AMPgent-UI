from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

from generate_vegfa_pepglad_macrel_rescue import (
    OPERATOR_ID,
    build_rescue_proposals,
    sha256_text,
)


def test_rescue_is_bounded_one_residue_and_uses_authoritative_parent() -> None:
    parent_sequence = "SWWAELLAASGPRLRRAHK"
    parent_sha = sha256_text(parent_sequence)
    parent = type(
        "Parent",
        (),
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "sequence": parent_sequence,
            "sequence_sha256": parent_sha,
        },
    )()
    donor = {
        residue: {
            "donor_candidate_id": f"pepglad-{residue}",
            "donor_source": "PepGLAD",
            "donor_fragment": residue,
            "donor_fragment_offset": "0",
            "donor_artifact": "donor.csv",
            "donor_row_number": "2",
            "donor_row_sha256": "a" * 64,
            "donor_residue": residue,
        }
        for residue in "EST"
    }
    rows = build_rescue_proposals(
        [
            {
                "sequence": parent_sequence,
                "sequence_sha256": parent_sha,
            }
        ],
        {parent_sha: parent},
        donor,
        set(),
        parent_run_id="run",
        limit=3,
    )
    assert len(rows) == 3
    assert {row["to_residue"] for row in rows} == set("EST")
    assert all(row["operator_id"] == OPERATOR_ID for row in rows)
    assert all(len(row["sequence"]) == len(parent_sequence) for row in rows)
    assert all(
        sum(
            left != right
            for left, right in zip(parent_sequence, row["sequence"], strict=True)
        )
        == 1
        for row in rows
    )
    assert all(row["parent_candidate_id"] == "" for row in rows)
    assert all(row["parent_identity_kind"] == "scored_proposal" for row in rows)
    assert all(row["source_proposal_id"] == f"proposal:{parent_sha}" for row in rows)
    assert all(row["parent_run_id"] == "" for row in rows)
    assert all(row["parent_identity_kind"] == "scored_proposal" for row in rows)


def test_rescue_rejects_historical_child_hash() -> None:
    parent_sequence = "SWWAELLAASGPRLRRAHK"
    parent_sha = sha256_text(parent_sequence)
    parent = type(
        "Parent",
        (),
        {"id": "parent", "sequence": parent_sequence, "sequence_sha256": parent_sha},
    )()
    donor = {
        residue: {
            "donor_candidate_id": f"pepglad-{residue}",
            "donor_source": "PepGLAD",
            "donor_fragment": residue,
            "donor_fragment_offset": "0",
            "donor_artifact": "donor.csv",
            "donor_row_number": "2",
            "donor_row_sha256": "a" * 64,
            "donor_residue": residue,
        }
        for residue in "EST"
    }
    historical = {sha256_text("S" + parent_sequence[1:])}
    rows = build_rescue_proposals(
            [{"sequence": parent_sequence, "sequence_sha256": parent_sha}],
            {parent_sha: parent},
            donor,
            historical,
            parent_run_id="run",
            limit=3,
        )
    assert len(rows) == 3
    assert all(row["sequence"] != "S" + parent_sequence[1:] for row in rows)


def test_rescue_round_robins_four_diagnosed_parents() -> None:
    sequences = [
        "AWWAELLAASGPRLRRAHK",
        "IWWAELLAASGPRLRRAHK",
        "KWWAELLAASGPRLRRAHK",
        "LWWAELLAASGPRLRRAHK",
    ]
    diagnoses = [
        {"sequence": sequence, "sequence_sha256": sha256_text(sequence)}
        for sequence in sequences
    ]
    donor = {
        residue: {
            "donor_candidate_id": f"pepglad-{residue}",
            "donor_source": "PepGLAD",
            "donor_fragment": residue,
            "donor_fragment_offset": "0",
            "donor_artifact": "donor.csv",
            "donor_row_number": "2",
            "donor_row_sha256": "a" * 64,
            "donor_residue": residue,
        }
        for residue in "EST"
    }
    rows = build_rescue_proposals(
        diagnoses,
        {},
        donor,
        set(),
        parent_run_id="run",
        limit=12,
    )
    counts = {
        sequence: sum(row["parent_sequence"] == sequence for row in rows)
        for sequence in sequences
    }
    assert len(rows) == 12
    assert counts == dict.fromkeys(sequences, 3)
    assert max(counts.values()) <= 3


def test_operator_does_not_install_hydrophobic_hard_gate() -> None:
    script = Path("analysis/generate_vegfa_pepglad_macrel_rescue.py").read_text(
        encoding="utf-8"
    )
    assert "hydrophobic hard gate" not in script.lower()
    assert "hydrophobic_ratio" not in script


def test_run2_close_receipt_records_negative_gate_without_materialization() -> None:
    receipt_path = Path(
        "reports/vegfa_pepglad_macrel_rescue_20260904_run2/close_receipt.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["proposal_count"] == 12
    assert receipt["pg_exact"]["pg_new_proposals"] == 12
    assert receipt["parent_lineage"]["parent_run_id"] == ""
    assert receipt["parent_lineage"]["parent_identity_kind"] == "scored_proposal"
    assert receipt["formal12"]["complete"] == 12
    assert receipt["formal12"]["calibrated_activity_support_ge_2"] == 0
    assert receipt["challenger"]["apex"]["applicability_status"] == "runtime_unavailable"
    assert receipt["challenger"]["peptiverse"]["conflict_status"] == "not_assessed"
    assert receipt["qd"]["quality_eligible"] == 0
    assert receipt["materialization"]["candidate_count"] == 0
    assert receipt["structure_queue"]["count"] == 0


def test_v1_challenger_runtime_failure_is_not_a_pass() -> None:
    receipt_path = Path(
        "reports/vegfa_pepglad_macrel_rescue_20260904_v1/challenger/runtime_failure_receipt.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["receipt_status"] == "failed_before_receipt"
    assert receipt["error_category"] == "worker_execution_not_authorized"
    assert receipt["hemopi2_receipt_valid"] is False
    assert receipt["no_model_values_recorded"] is True


def test_v1_close_receipt_records_successful_challenger_and_zero_qd() -> None:
    receipt_path = Path(
        "reports/vegfa_pepglad_macrel_rescue_20260904_v1/close_receipt.json"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["challenger"]["hemopi2_status"] == "reviewed"
    assert receipt["challenger"]["hemopi2_reviewed"] == 12
    assert receipt["challenger"]["hemopi2_no_conflict"] == 10
    assert receipt["challenger"]["hemopi2_conflict"] == 2
    assert receipt["qd"]["quality_eligible"] == 0
    assert receipt["materialization"]["candidate_count"] == 0
