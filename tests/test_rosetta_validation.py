import pytest

from pepagent.validation.rosetta import summarize_native_start_validation


def test_summarize_native_start_validation_ranks_low_scores_first() -> None:
    result = {
        "primary_dG_separated_reu": -4.0,
        "decoys": [
            {"reweighted_sc": -3.0, "dG_separated": -2.0, "peptide_bb_rmsd": 2.5},
            {"reweighted_sc": -5.0, "dG_separated": -4.0, "peptide_bb_rmsd": 0.5},
            {"reweighted_sc": -4.0, "dG_separated": -3.0, "peptide_bb_rmsd": 1.5},
        ],
    }

    summary = summarize_native_start_validation(result)

    assert summary["nstruct"] == 3
    assert summary["dG_minimum_reu"] == -4.0
    assert summary["rmsd_median_angstrom"] == 1.5
    assert summary["fraction_rmsd_le_1_angstrom"] == pytest.approx(1 / 3)
    assert summary["fraction_rmsd_le_2_angstrom"] == pytest.approx(2 / 3)
    assert summary["top1_reweighted_rmsd_angstrom"] == 0.5
    assert summary["reweighted_rmsd_spearman"] == 1.0


def test_summarize_native_start_validation_rejects_incomplete_decoys() -> None:
    with pytest.raises(ValueError, match="missing validation fields"):
        summarize_native_start_validation(
            {
                "primary_dG_separated_reu": -1.0,
                "decoys": [{"reweighted_sc": -1.0}],
            }
        )
