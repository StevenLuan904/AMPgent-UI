from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "analysis"))

from generate_angpt1_pepglad_source_expansion import sha256_text  # noqa: E402
from generate_vegfa_pepflow_peripheral_rescue import (  # noqa: E402
    OPERATOR_ID,
    build_proposals,
    donor_rows,
)


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


def _reference() -> list[dict[str, str]]:
    return [
        {
            "acceptor_start_zero_based": str(position),
            "from_residue": residue,
            "to_residue": "",
        }
        for position, residue in (
            (3, "A"),
            (3, "A"),
            (3, "A"),
            (7, "A"),
            (7, "A"),
            (8, "A"),
            (8, "A"),
            (9, "S"),
        )
    ]


def _donors() -> list[dict[str, str]]:
    result = []
    for residue, donor_id in (
        ("G", "pepflow-g"),
        ("P", "pepflow-p"),
        ("S", "pepflow-s"),
        ("V", "pepflow-v"),
    ):
        result.append(
            {
                "donor_candidate_id": donor_id,
                "donor_source": "PepFlow",
                "donor_sequence": "VVVV",
                "donor_fragment": residue,
                "donor_residue": residue,
                "donor_fragment_offset": "0",
                "donor_artifact": "fixture.csv",
                "donor_row_number": "2",
                "donor_row_sha256": "d" * 64,
            }
        )
    return result


def test_donor_rows_retain_real_source_and_row_identity(tmp_path: Path) -> None:
    path = tmp_path / "donors.csv"
    path.write_text(
        "donor_source,donor_candidate_id,donor_sequence,donor_fragment\n"
        "PepFlow,pepflow-1,VVVV,GP\n"
        "PepGLAD,other,VVVV,AA\n",
        encoding="utf-8",
    )
    rows = donor_rows(path)
    assert {row["donor_residue"] for row in rows} == {"G", "P"}
    assert all(row["donor_source"] == "PepFlow" for row in rows)
    assert all(row["donor_row_number"] == "2" for row in rows)
    assert all(row["donor_row_sha256"] for row in rows)


def test_matched_control_preserves_parent_slots_and_budget() -> None:
    parent = _parent("SWWAELLAASGPRLRRAHK")
    rows = build_proposals(
        parent,
        _reference(),
        _donors(),
        set(),
        set(),
        source_generation_receipt_sha256="a" * 64,
        source_score_receipt_sha256="b" * 64,
        reference_proposals_sha256="c" * 64,
    )
    assert len(rows) == 8
    assert {int(row["acceptor_start_zero_based"]) for row in rows} == {3, 7, 8, 9}
    assert all(row["operator_id"] == OPERATOR_ID for row in rows)
    assert all(row["donor_source"] == "PepFlow" for row in rows)
    assert all(row["sequence"][1:3] == "WW" for row in rows)
    assert all(
        sum(
            left != right
            for left, right in zip(parent.sequence, row["sequence"], strict=True)
        )
        == 1
        for row in rows
    )
    assert all(
        row["source_generation_receipt_sha256"] == "a" * 64
        and row["source_score_receipt_sha256"] == "b" * 64
        and row["source_reference_proposals_sha256"] == "c" * 64
        for row in rows
    )


def test_history_and_exact_hashes_are_excluded() -> None:
    parent = _parent("SWWAELLAASGPRLRRAHK")
    reference = _reference()
    first = build_proposals(
        parent,
        reference,
        _donors(),
        set(),
        set(),
        source_generation_receipt_sha256="a" * 64,
        source_score_receipt_sha256="b" * 64,
        reference_proposals_sha256="c" * 64,
    )
    rows = build_proposals(
        parent,
        reference,
        _donors(),
        {first[0]["sequence_sha256"]},
        {(int(first[1]["acceptor_start_zero_based"]), first[1]["to_residue"])},
        source_generation_receipt_sha256="a" * 64,
        source_score_receipt_sha256="b" * 64,
        reference_proposals_sha256="c" * 64,
    )
    assert len(rows) < len(first)
    assert first[0]["sequence_sha256"] not in {row["sequence_sha256"] for row in rows}
