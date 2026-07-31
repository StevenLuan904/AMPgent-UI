from pathlib import Path

import numpy as np

from pepagent.structures.pdb import (
    peptide_backbone_rmsd_after_receptor_alignment,
    prepare_protein_peptide_pdb,
)


def _atom(serial: int, atom: str, residue: int, chain: str, xyz: tuple[float, ...]) -> str:
    return (
        f"ATOM  {serial:5d} {atom:^4s} ALA {chain}{residue:4d}    "
        f"{xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}  1.00 20.00           C"
    )


def test_prepare_pdb_reorders_and_filters_chains(tmp_path: Path) -> None:
    source = tmp_path / "source.pdb"
    source.write_text(
        "\n".join(
            [
                _atom(1, "CA", 1, "B", (0, 0, 0)),
                "HETATM    2  C1  LIG C   1       1.000   1.000   1.000",
                _atom(3, "CA", 1, "A", (1, 0, 0)),
                _atom(4, "CA", 1, "C", (2, 0, 0)),
                "END",
            ]
        ),
        encoding="ascii",
    )
    output = tmp_path / "prepared.pdb"
    counts = prepare_protein_peptide_pdb(source, output, ["A"], "B")
    lines = output.read_text(encoding="ascii").splitlines()
    assert counts == {"A": 1, "B": 1}
    assert lines[0][21] == "A"
    assert lines[2][21] == "B"
    assert not any(line.startswith("HETATM") for line in lines)


def test_peptide_rmsd_aligns_on_receptor(tmp_path: Path) -> None:
    native = tmp_path / "native.pdb"
    model = tmp_path / "model.pdb"
    native_atoms = [
        _atom(1, "CA", 1, "A", (0, 0, 0)),
        _atom(2, "CA", 2, "A", (1, 0, 0)),
        _atom(3, "CA", 3, "A", (0, 1, 0)),
        _atom(4, "N", 1, "B", (0, 0, 2)),
        _atom(5, "CA", 1, "B", (1, 0, 2)),
        _atom(6, "C", 1, "B", (2, 0, 2)),
    ]
    native.write_text("\n".join(native_atoms) + "\n", encoding="ascii")
    translation = np.array([4.0, -3.0, 2.0])
    model_atoms = []
    for line in native_atoms:
        xyz = np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])])
        moved = xyz + translation
        model_atoms.append(
            _atom(
                int(line[6:11]),
                line[12:16].strip(),
                int(line[22:26]),
                line[21],
                tuple(moved),
            )
        )
    model.write_text("\n".join(model_atoms) + "\n", encoding="ascii")
    rmsd = peptide_backbone_rmsd_after_receptor_alignment(model, native, ["A"], "B")
    assert rmsd < 1e-6
