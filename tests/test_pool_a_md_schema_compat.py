import json

import pytest

from analysis.summarize_pool_a_key_contacts import candidate_contacts
from analysis.summarize_pool_a_md_results import validated_interface_evidence


def v3_interface(**overrides):
    payload = {
        "schema_version": "ampgent.pool-a-md-interface-analysis.3",
        "interface_rmsd_nm": {"mean": 0.2, "maximum": 0.5},
        "native_contact_fraction": {"mean": 0.8, "minimum": 0.4},
        "key_contacts": [],
        "hydrogen_bond_occupancy": 0.6,
        "salt_bridge_occupancy": 0.4,
        "water_bridge_occupancy": 0.5,
        "peptide_departed": False,
        "maximum_departure_duration_ps": 0,
        "maximum_peptide_com_shift_nm": 0.3,
        "hydrogen_bond_network": {
            "bond_count_per_frame": {
                "mean": 0.6,
                "median": 0,
                "maximum": 2,
                "p95": 2,
            },
            "unique_atom_pair_count": 1,
            "unique_residue_pair_count": 1,
            "persistent_residue_pair_count": 1,
            "residue_pairs": [],
            "atom_pairs": [],
        },
    }
    payload.update(overrides)
    return payload


def test_summary_accepts_canonical_v3_network():
    assert validated_interface_evidence(v3_interface()) is True


def test_summary_rejects_incomplete_v3_network():
    payload = v3_interface()
    del payload["hydrogen_bond_network"]["residue_pairs"]
    with pytest.raises(ValueError, match="network is incomplete"):
        validated_interface_evidence(payload)


def test_contact_summary_accepts_v3(tmp_path):
    candidate_id = "candidate-1"
    candidate = {
        "target_key": "acea",
        "run_id": "run-1",
        "candidate_id": candidate_id,
        "sequence": "AK",
        "sequence_sha256": "a" * 64,
        "interface_complete": "True",
        "interface_postgresql_ingested": "True",
    }
    path = tmp_path / "acea" / candidate_id / "analysis/interface/interface_analysis.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(v3_interface()), encoding="utf-8")
    result = candidate_contacts(candidate, tmp_path)
    assert result is not None
    assert result["key_contact_count"] == 0
