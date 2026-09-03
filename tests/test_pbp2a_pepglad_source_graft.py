from __future__ import annotations

import sys
from pathlib import Path


def _load():
    analysis = Path(__file__).resolve().parents[1] / "analysis"
    sys.path.insert(0, str(analysis))
    import pbp2a_pepglad_source_graft as module

    return module


def _parent(sequence: str, family: str, cell: str) -> dict[str, str]:
    return {
        "sequence": sequence,
        "candidate_id": f"parent-{family}",
        "archive_candidate_id": f"archive-{family}",
        "archive_cell_id": cell,
        "archive_family_key_80_80": family,
        "archive_quality": "0.9",
    }


def test_select_fragments_keeps_pepglad_lineage_and_is_bounded():
    module = _load()
    rows = [
        {
            "donor_source": "factorized_pepglad_amp_edit",
            "donor_block_sequence": "LKP",
            "donor_candidate_id": "d1",
            "independent_gain_axes": "['llamp', 'macrel']",
        },
        {
            "donor_source": "pepflow",
            "donor_block_sequence": "AAA",
        },
    ]
    selected = module.select_pepglad_fragments(rows, limit=1)
    assert len(selected) == 1
    assert selected[0]["fragment"] == "LKP"
    assert selected[0]["donor_source"] == "PepGLAD"


def test_generate_excludes_exact_prior_edit_and_preserves_length():
    module = _load()
    parent = _parent("KKLAAAGGKK", "fam-a", "q4-h1-m1-l2")
    fragment = {
        "fragment": "LKP",
        "donor_candidate_id": "d1",
        "donor_sequence": "AIEKAIGLLKPKVERLQLITS",
        "donor_family_key_80_80": "pepglad-fam",
    }
    result = module.generate_proposals(
        [parent], [fragment], prior_edits={(parent["sequence"], 0, "LKP")}, max_total=3
    )
    assert result
    assert all(len(row["sequence"]) == len(parent["sequence"]) for row in result)
    assert all(row["donor_source"] == "PepGLAD" for row in result)
    assert all(row["graft_start_zero_based"] != 0 for row in result)


def test_archive_parent_selection_requires_all_parent_gates_and_balances_family_cell():
    module = _load()
    rows = [
        {
            "sequence": "KKLAAAGGKK",
            "candidate_id": "p1",
            "display_eligible": "true",
            "formal_12_complete": "true",
            "activity_model_support_count_calibrated": "2",
            "guruprasad_instability_index": "20",
            "family_key_80_80": "fam-a",
        },
        {
            "sequence": "RRVAAAGGKK",
            "candidate_id": "p2",
            "display_eligible": "false",
            "formal_12_complete": "true",
            "activity_model_support_count_calibrated": "3",
            "guruprasad_instability_index": "20",
            "family_key_80_80": "fam-b",
        },
    ]
    archive = {
        "branches": {
            "pbp2a": {
                "elites": [
                    {
                        "sequence": "KKLAAAGGKK",
                        "cell_id": "c1",
                        "quality": 0.9,
                        "candidate_id": "a1",
                    },
                    {
                        "sequence": "RRVAAAGGKK",
                        "cell_id": "c2",
                        "quality": 0.9,
                        "candidate_id": "a2",
                    },
                ]
            }
        }
    }
    selected = module.select_archive_parents(rows, archive)
    assert [row["sequence"] for row in selected] == ["KKLAAAGGKK"]
