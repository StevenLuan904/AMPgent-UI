import copy

import pytest

from analysis.build_pool_s_candidate_dossiers import build


def completed_row():
    return {
        "target_key": "acea",
        "run_id": "run-1",
        "candidate_id": "candidate-1",
        "sequence": "AK",
        "sequence_sha256": "a" * 64,
        "pool_a_rank": 1,
        "rosetta_median_dg_reu": -35.0,
        "pool_s_evidence_complete": True,
        "postgresql_evidence_complete": True,
        "interface_rmsd_mean_nm": 0.2,
        "interface_rmsd_max_nm": 0.3,
        "native_contact_fraction_mean": 0.8,
        "native_contact_fraction_min": 0.5,
        "key_contact_count": 1,
        "key_contact_occupancy_mean": 0.75,
        "key_contact_occupancy_max": 0.75,
        "hydrogen_bond_occupancy": 0.9,
        "salt_bridge_occupancy": 0.8,
        "water_bridge_occupancy": 0.7,
        "peptide_departed": False,
        "maximum_departure_duration_ps": 0.0,
        "maximum_peptide_com_shift_nm": 0.25,
        "mmgbsa_mean_kcal_mol": -20.0,
        "mmgbsa_ci95_lower_kcal_mol": -22.0,
        "mmgbsa_ci95_upper_kcal_mol": -18.0,
        "mmgbsa_frame_count": 50,
        "decomposition_residue_count": 2,
    }


def evidence(row):
    identity = {key: row[key] for key in (
        "target_key", "run_id", "candidate_id", "sequence", "sequence_sha256"
    )}
    contacts = {"candidates": [{**identity, "top_contacts": []}]}
    decomposition = {
        "candidates": [
            {
                **identity,
                "peptide_total_decomposition_kcal_mol": -3.0,
                "top_favorable_residues": [],
                "top_unfavorable_residues": [],
            }
        ]
    }
    frontier = {
        "targets": {"acea": {"provisional_frontier": [identity]}}
    }
    return contacts, decomposition, frontier


def test_builds_complete_candidate_dossier():
    row = completed_row()
    contacts, decomposition, frontier = evidence(row)
    payload = build([row], contacts, decomposition, frontier)
    assert payload["complete_dossier_count"] == 1
    dossier = payload["dossiers"][0]
    assert dossier["provisional_pool_s_frontier_member"] is True
    assert dossier["interaction_occupancy"]["water_bridge"] == 0.7
    assert dossier["mmgbsa"]["confidence_interval_95_kcal_mol"] == [-22.0, -18.0]


def test_rejects_cross_run_or_candidate_identity_drift():
    row = completed_row()
    contacts, decomposition, frontier = evidence(row)
    contacts = copy.deepcopy(contacts)
    contacts["candidates"][0]["sequence"] = "AA"
    with pytest.raises(ValueError, match="contact identity drift"):
        build([row], contacts, decomposition, frontier)


def test_requires_full_derived_evidence_coverage():
    row = completed_row()
    contacts, decomposition, frontier = evidence(row)
    decomposition["candidates"] = []
    with pytest.raises(ValueError, match="decomposition dossier coverage"):
        build([row], contacts, decomposition, frontier)
