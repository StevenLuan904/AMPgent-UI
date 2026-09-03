import pytest

from analysis.analyze_rosetta_md_concordance import analyze, ranks, spearman


def row(index: int, rosetta: float, rmsd: float, contacts: float, mmgbsa: float) -> dict:
    return {
        "run_id": "run",
        "candidate_id": f"candidate-{index}",
        "target_key": "acea",
        "pool_s_evidence_complete": "True",
        "postgresql_evidence_complete": "True",
        "peptide_departed": "False",
        "rosetta_median_dg_reu": str(rosetta),
        "interface_rmsd_mean_nm": str(rmsd),
        "native_contact_fraction_mean": str(contacts),
        "mmgbsa_mean_kcal_mol": str(mmgbsa),
    }


def test_rank_and_spearman_handle_ties():
    assert ranks([2.0, 1.0, 2.0]) == [2.5, 1.0, 2.5]
    assert spearman([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)


def test_analysis_reports_expected_direction_and_keeps_md_for_small_cohort():
    result = analyze(
        [
            row(1, -60, 0.1, 0.9, -100),
            row(2, -50, 0.2, 0.8, -80),
            row(3, -40, 0.3, 0.7, -60),
        ]
    )
    metrics = result["overall"]["metrics"]
    assert metrics["interface_rmsd_mean_nm"]["spearman_rho"] == pytest.approx(1.0)
    assert metrics["native_contact_fraction_mean"]["spearman_rho"] == pytest.approx(-1.0)
    assert result["decision"] == "retain_md_as_nonredundant_gate"


def test_analysis_rejects_duplicate_exact_identity():
    duplicate = row(1, -60, 0.1, 0.9, -100)
    with pytest.raises(ValueError, match="duplicate"):
        analyze([duplicate, dict(duplicate)])
