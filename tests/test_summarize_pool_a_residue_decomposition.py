import csv
import json

import pytest

from analysis.summarize_pool_a_residue_decomposition import candidate_decomposition


def write_candidate(tmp_path, sequence="AK"):
    candidate = {
        "target_key": "acea",
        "run_id": "run-1",
        "candidate_id": "candidate-1",
        "sequence": sequence,
        "sequence_sha256": "a" * 64,
        "mmgbsa_complete": "True",
        "mmgbsa_postgresql_ingested": "True",
    }
    root = tmp_path / "acea/candidate-1/analysis/mmgbsa"
    root.mkdir(parents=True)
    (root / "mmgbsa_analysis.json").write_text(
        json.dumps({"peptide_residue_range": [3, 4]}), encoding="utf-8"
    )
    columns = [
        "residue",
        "location",
        "mean_Internal",
        "mean_van der Waals",
        "mean_Electrostatic",
        "mean_Polar Solvation",
        "mean_Non-Polar Solv.",
        "mean_TOTAL",
    ]
    with (root / "residue_decomposition_mean.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerow(dict.fromkeys(columns, "0") | {"residue": "ALA 3"})
        writer.writerow(
            dict.fromkeys(columns, "0")
            | {"residue": "LYS 4", "mean_TOTAL": "-2.5"}
        )
    return candidate


def test_extracts_exact_peptide_positions_and_energy_hotspots(tmp_path):
    result = candidate_decomposition(write_candidate(tmp_path), tmp_path)
    assert result is not None
    assert result["peptide_residue_count"] == 2
    assert result["peptide_total_decomposition_kcal_mol"] == -2.5
    assert result["top_favorable_residues"][0]["peptide_position"] == 2
    assert [row["amino_acid"] for row in result["residues"]] == ["A", "K"]


def test_rejects_sequence_and_decomposition_identity_drift(tmp_path):
    candidate = write_candidate(tmp_path, sequence="AA")
    with pytest.raises(ValueError, match="residue identity mismatch"):
        candidate_decomposition(candidate, tmp_path)
