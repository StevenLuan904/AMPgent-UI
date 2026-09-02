import json
from pathlib import Path, PurePosixPath

import pytest

from analysis.ingest_pool_a_md_evidence import (
    MD_RELEASE,
    md_evidence,
    mmgbsa_evidence,
    relocate_file_uris,
)
from analysis.sync_pool_a_md_compact_evidence import allowed_relative_path


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def candidate(tmp_path: Path) -> Path:
    root = tmp_path / "acea" / "11111111-1111-1111-1111-111111111111"
    write(
        root / "launch_receipt.json",
        {
            "candidate_id": "11111111-1111-1111-1111-111111111111",
            "run_id": "22222222-2222-2222-2222-222222222222",
            "target_key": "acea",
            "sequence_sha256": "a" * 64,
        },
    )
    return root


def test_md_evidence_requires_exact_1_plus_50_ns_and_emits_requested_metrics(tmp_path):
    root = candidate(tmp_path)
    write(
        root / "manifest.json",
        {
            "schema_version": "ampgent.pool-a-md.1",
            "status": "succeeded",
            "npt_ns": 1,
            "production_ns": 50,
        },
    )
    write(
        root / "analysis/interface/interface_analysis.json",
        {
            "schema_version": "ampgent.pool-a-md-interface-analysis.2",
            "frame_count": 5000,
            "interaction_sample_count": 500,
            "interface_rmsd_nm": {"mean": 0.2, "maximum": 0.4},
            "native_contact_fraction": {"mean": 0.8, "minimum": 0.5},
            "hydrogen_bond_occupancy": 0.7,
            "salt_bridge_occupancy": 0.4,
            "water_bridge_occupancy": 0.6,
            "peptide_departed": False,
            "maximum_departure_duration_ps": 0,
            "maximum_peptide_com_shift_nm": 0.3,
            "definitions": {},
            "key_contacts": [],
        },
    )
    (root / "analysis/interface/timeseries.csv").write_text("time_ps\n0\n")
    evidence = md_evidence(root)
    assert evidence is not None and evidence["release"] == MD_RELEASE
    assert evidence["identity"]["run_id"] == "22222222-2222-2222-2222-222222222222"
    assert {
        "md_interface_rmsd_mean_nm",
        "md_native_contact_fraction_mean",
        "md_hydrogen_bond_occupancy",
        "md_salt_bridge_occupancy",
        "md_water_bridge_occupancy",
        "md_peptide_departed",
    } <= evidence["values"].keys()

    write(
        root / "manifest.json",
        {
            "schema_version": "ampgent.pool-a-md.1",
            "status": "succeeded",
            "npt_ns": 1,
            "production_ns": 5,
        },
    )
    with pytest.raises(ValueError, match="50 ns"):
        md_evidence(root)


def test_mmgbsa_evidence_keeps_ci_and_residue_decomposition_remote(tmp_path):
    root = candidate(tmp_path)
    write(
        root / "analysis/mmgbsa/mmgbsa_analysis.json",
        {
            "schema_version": "ampgent.pool-a-mmgbsa.1",
            "mean_binding_energy_kcal_mol": -42.0,
            "confidence_interval_95_kcal_mol": [-45.0, -39.0],
            "decomposition_residue_count": 400,
            "limitations": ["computed estimate"],
        },
    )
    decomposition = root / "analysis/mmgbsa/residue_decomposition_mean.csv"
    decomposition.write_text("residue,mean_TOTAL\nALA1,-1\n")
    evidence = mmgbsa_evidence(root)
    assert evidence is not None
    assert evidence["values"]["mmgbsa_binding_energy_ci95_lower_kcal_mol"] == -45.0
    assert evidence["files"]["residue_decomposition"]["uri"] == str(decomposition)
    assert decomposition.exists()


def test_compact_sync_allowlist_excludes_structures_and_trajectories():
    assert allowed_relative_path(
        PurePosixPath("acea/candidate/analysis/interface/interface_analysis.json")
    )
    assert allowed_relative_path(PurePosixPath("acea/candidate/failure_receipt.json"))
    assert allowed_relative_path(PurePosixPath("acea/candidate/manifest.json"))
    assert not allowed_relative_path(PurePosixPath("acea/candidate/production.dcd"))
    assert not allowed_relative_path(PurePosixPath("acea/candidate/prepared_solvated.pdb"))


def test_evidence_uris_can_point_to_authoritative_remote_tree(tmp_path):
    root = candidate(tmp_path)
    local = root / "manifest.json"
    local.write_text("{}")
    evidence = {"files": {"manifest": {"uri": str(local), "sha256": "a" * 64}}}
    relocate_file_uris(evidence, tmp_path, "/remote/pool-a")
    assert evidence["files"]["manifest"]["uri"].startswith("/remote/pool-a/acea/")
