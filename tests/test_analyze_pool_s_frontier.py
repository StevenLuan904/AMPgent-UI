from analysis.analyze_pool_s_frontier import analyze, dominates, pareto_front


def row(candidate: str, rmsd: float, contacts: float, energy: float, target: str = "acea"):
    return {
        "target_key": target,
        "run_id": f"run-{candidate}",
        "candidate_id": candidate,
        "sequence": "KKLLKKLLKKLL",
        "sequence_sha256": candidate * 64,
        "pool_a_rank": "1",
        "rosetta_median_dg_reu": "-40",
        "interface_rmsd_max_nm": "0.4",
        "native_contact_fraction_min": "0.5",
        "key_contact_count": "4",
        "key_contact_occupancy_mean": "0.7",
        "key_contact_occupancy_max": "0.9",
        "hydrogen_bond_occupancy": "0.8",
        "salt_bridge_occupancy": "0.7",
        "water_bridge_occupancy": "0.6",
        "peptide_departed": "False",
        "maximum_departure_duration_ps": "0",
        "maximum_peptide_com_shift_nm": "0.3",
        "mmgbsa_ci95_lower_kcal_mol": "-55",
        "mmgbsa_ci95_upper_kcal_mol": "-45",
        "mmgbsa_frame_count": "36",
        "decomposition_residue_count": "100",
        "pool_s_evidence_complete": True,
        "postgresql_evidence_complete": True,
        "interface_rmsd_mean_nm": rmsd,
        "native_contact_fraction_mean": contacts,
        "mmgbsa_mean_kcal_mol": energy,
    }


def test_target_local_front_retains_conflicting_endpoints_without_weighting():
    first = row("a", 0.2, 0.7, -50)
    dominated = row("b", 0.3, 0.6, -40)
    contact_leader = row("c", 0.25, 0.9, -45)
    assert dominates(first, dominated)
    assert {item["candidate_id"] for item in pareto_front([first, dominated, contact_leader])} == {
        "a",
        "c",
    }
    other_target = row("d", 0.5, 0.2, -10, "gyra")
    result = analyze([first, dominated, contact_leader, other_target])
    assert result["weighted_total_used"] is False
    assert result["provisional_frontier_count"] == 3
    assert result["targets"]["acea"]["objective_conflict_retained"] is True
    assert result["targets"]["gyra"]["provisional_frontier_count"] == 1


def test_incomplete_candidate_is_reported_but_not_promoted_to_front():
    complete = row("a", 0.2, 0.7, -50)
    incomplete = row("b", 0.1, 0.9, -100)
    incomplete["postgresql_evidence_complete"] = False
    result = analyze([complete, incomplete])
    assert result["pool_a_candidate_count"] == 2
    assert result["md_and_postgresql_complete_count"] == 1
    assert result["targets"]["acea"]["provisional_frontier_count"] == 1
