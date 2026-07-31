from pathlib import Path

from pepagent.model_workers.rosetta_cli import _canonicalize_dumped_pdb


def test_canonicalize_dumped_pdb_removes_work_directory_identity(
    tmp_path: Path,
) -> None:
    pdb = tmp_path / "decoy_0001.pdb"
    pdb.write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000\n"
        f"#BEGIN_POSE_ENERGIES_TABLE {pdb}\n"
        f"#END_POSE_ENERGIES_TABLE {pdb}\n",
        encoding="ascii",
    )

    _canonicalize_dumped_pdb(pdb)

    assert pdb.read_text(encoding="ascii").endswith(
        "#BEGIN_POSE_ENERGIES_TABLE decoy_0001.pdb\n"
        "#END_POSE_ENERGIES_TABLE decoy_0001.pdb\n"
    )
