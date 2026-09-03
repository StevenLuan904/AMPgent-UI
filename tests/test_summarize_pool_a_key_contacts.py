import json

import pytest

from analysis.summarize_pool_a_key_contacts import candidate_contacts, summarize


def write_candidate(tmp_path, sequence="AK", contacts=None, candidate_id="candidate-1"):
    candidate = {
        "target_key": "acea",
        "run_id": "run-1",
        "candidate_id": candidate_id,
        "sequence": sequence,
        "sequence_sha256": "a" * 64,
        "interface_complete": "True",
        "interface_postgresql_ingested": "True",
    }
    root = tmp_path / "acea" / candidate_id / "analysis/interface"
    root.mkdir(parents=True)
    (root / "interface_analysis.json").write_text(
        json.dumps(
            {
                "schema_version": "ampgent.pool-a-md-interface-analysis.2",
                "key_contacts": contacts
                or [
                    {
                        "receptor_residue": "ASP12",
                        "peptide_residue": "LYS2",
                        "occupancy": 0.75,
                    },
                    {
                        "receptor_residue": "GLY8",
                        "peptide_residue": "ALA1",
                        "occupancy": 0.25,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return candidate


def test_extracts_stable_contacts_and_target_hotspots(tmp_path):
    candidates = [
        write_candidate(tmp_path, candidate_id="candidate-1"),
        write_candidate(
            tmp_path,
            candidate_id="candidate-2",
            contacts=[
                {
                    "receptor_residue": "ASP12",
                    "peptide_residue": "LYS2",
                    "occupancy": 0.5,
                }
            ],
        ),
    ]
    payload = summarize(candidates, tmp_path)
    assert payload["interface_and_postgresql_complete_count"] == 2
    assert payload["key_contact_pair_count"] == 3
    assert payload["stable_contact_pair_count"] == 2
    hotspot = payload["target_receptor_hotspots"]["acea"][0]
    assert hotspot["receptor_residue"] == "ASP12"
    assert hotspot["candidate_prevalence"] == 1.0
    assert hotspot["mean_candidate_max_occupancy"] == 0.625


def test_rejects_peptide_contact_identity_drift(tmp_path):
    candidate = write_candidate(
        tmp_path,
        contacts=[
            {
                "receptor_residue": "ASP12",
                "peptide_residue": "ALA2",
                "occupancy": 0.75,
            }
        ],
    )
    with pytest.raises(ValueError, match="contact identity mismatch"):
        candidate_contacts(candidate, tmp_path)


def test_pending_candidate_does_not_require_evidence(tmp_path):
    candidate = write_candidate(tmp_path)
    candidate["interface_complete"] = "False"
    assert candidate_contacts(candidate, tmp_path) is None
