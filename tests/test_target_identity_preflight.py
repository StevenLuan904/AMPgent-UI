from copy import deepcopy

import pytest

from pepagent.provenance.hashing import sha256_text
from pepagent.target_identity_preflight import verify_target_identity_bundle


def _inputs() -> tuple[dict, dict, dict, str]:
    panel_sha256 = "f" * 64
    target_id = "11111111-1111-1111-1111-111111111111"
    sequence = "AIEDKNFKQVYKDSSY"
    panel = {
        "branches": [
            {
                "target_key": "ec_gyrA",
                "target_id": target_id,
                "target_sequence_sha256": sha256_text(sequence),
                "coordinate_sha256": "a" * 64,
            }
        ]
    }
    runtime = {target_id: {"target_sequence": sequence}}
    bundle = {
        "schema_version": "v39.target-identity-bundle.1",
        "target_panel_sha256": panel_sha256,
        "branches": [
            {
                "target_key": "ec_gyrA",
                "target_id": target_id,
                "structure_evidence_mode": "direct_experimental",
                "registered_organism": "Escherichia coli",
                "coordinate_organism": "Escherichia coli",
                "registered_accession": "P0AES4",
                "coordinate_polymer_accession": "P0AES4",
                "coordinate_chain_sequence": sequence,
                "coordinate_sha256": "a" * 64,
                "source_artifact_sha256": "b" * 64,
                "pocket_mapping_sha256": "c" * 64,
            }
        ],
    }
    return bundle, panel, runtime, panel_sha256


def test_target_identity_preflight_recomputes_a_direct_witness() -> None:
    bundle, panel, runtime, panel_sha256 = _inputs()
    witness = verify_target_identity_bundle(
        bundle=bundle,
        panel=panel,
        target_runtime_by_id=runtime,
        target_panel_sha256=panel_sha256,
    )
    assert witness["all_branches_accepted"] is True
    assert witness["branches"][0]["structure_evidence_mode"] == "direct_experimental"
    assert witness["branches"][0]["sequence_identity_fraction"] == 1.0


def test_target_identity_preflight_rejects_species_drift_for_direct_structure() -> None:
    bundle, panel, runtime, panel_sha256 = _inputs()
    bundle["branches"][0]["coordinate_organism"] = "Staphylococcus aureus"
    with pytest.raises(ValueError, match="organism does not match"):
        verify_target_identity_bundle(
            bundle=bundle,
            panel=panel,
            target_runtime_by_id=runtime,
            target_panel_sha256=panel_sha256,
        )


def test_target_identity_preflight_requires_explicit_homology_mode() -> None:
    bundle, panel, runtime, panel_sha256 = _inputs()
    branch = bundle["branches"][0]
    branch["coordinate_organism"] = "Staphylococcus aureus"
    branch["coordinate_polymer_accession"] = "A0A0H3JPA5"
    branch["structure_evidence_mode"] = "homology"
    witness = verify_target_identity_bundle(
        bundle=bundle,
        panel=panel,
        target_runtime_by_id=runtime,
        target_panel_sha256=panel_sha256,
    )
    observed = witness["branches"][0]
    assert observed["accepted"] is True
    assert observed["organism_matches"] is False
    assert observed["accession_matches"] is False


def test_target_identity_preflight_rejects_panel_or_coordinate_drift() -> None:
    bundle, panel, runtime, panel_sha256 = _inputs()
    drifted = deepcopy(bundle)
    drifted["branches"][0]["coordinate_sha256"] = "d" * 64
    with pytest.raises(ValueError, match="coordinate identity drifted"):
        verify_target_identity_bundle(
            bundle=drifted,
            panel=panel,
            target_runtime_by_id=runtime,
            target_panel_sha256=panel_sha256,
        )
