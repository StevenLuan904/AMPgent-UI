from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from Bio.PDB import PDBParser

from pepagent.model_workers.pepflow_target_generator_cli import (
    PEPFLOW_SEEDED_LAUNCH,
    run_pepflow,
)
from pepagent.model_workers.pepglad_target_generator_cli import run_pepglad
from pepagent.target_structure_generation import (
    TargetStructureGenerationRequest,
    collect_pepflow_proposals,
    collect_pepglad_proposals,
    write_pepflow_case,
    write_pepglad_pocket,
)


def _request() -> TargetStructureGenerationRequest:
    return TargetStructureGenerationRequest.model_validate(
        {
            "generator_id": "pepglad",
            "target": {
                "target_key": "acea",
                "accession": "NP_418439.1",
                "target_sequence_sha256": "a" * 64,
                "structure_id": "1IGW",
                "structure_uri": "https://files.rcsb.org/download/1IGW.pdb",
                "structure_sha256": "b" * 64,
                "receptor_chain_ids": ["A"],
                "pocket_key": "catalytic_mg_isocitrate_site",
                "pocket_evidence_grade": "A",
                "pocket_residues": [
                    {"chain_id": "A", "auth_residue_number": 89},
                    {"chain_id": "A", "auth_residue_number": 91},
                ],
            },
            "runtime": {
                "generator_id": "pepglad",
                "source_repository": "https://github.com/THUNLP-MT/PepGLAD",
                "source_revision": "bad015ca50c312a89482adb5220c3d907f13df5c",
                "license": "MIT",
                "model_variant": "codesign-pepbench",
                "checkpoint_uri": (
                    "https://github.com/THUNLP-MT/PepGLAD/releases/download/"
                    "v1.0/checkpoints.zip#checkpoints/codesign.ckpt"
                ),
                "checkpoint_sha256": "c" * 64,
                "checkpoint_size_bytes": 18_961_125,
            },
            "seed": 2026082601,
            "requested_proposals": 2,
            "peptide_length_min": 8,
            "peptide_length_max_exclusive": 16,
        }
    )


def _pepflow_request() -> TargetStructureGenerationRequest:
    payload = _request().model_dump(mode="json")
    payload["generator_id"] = "pepflow"
    payload["runtime"] = {
        "generator_id": "pepflow",
        "source_repository": "https://github.com/Ced3-han/PepFlowww",
        "source_revision": "16e0d267c2dbd96cdacbe5ac07c4dada0d61169b",
        "license": "MIT",
        "model_variant": "model2-real-world-design",
        "checkpoint_uri": "https://drive.google.com/file/d/example",
        "checkpoint_sha256": "d" * 64,
        "checkpoint_size_bytes": 83_063_321,
    }
    return TargetStructureGenerationRequest.model_validate(payload)


def _atom_line(
    serial: int,
    atom: str,
    residue: str,
    chain: str,
    number: int,
    x: float,
    y: float,
    z: float,
) -> str:
    return (
        f"ATOM  {serial:5d} {atom:^4s} {residue:>3s} {chain}{number:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00 20.00          {atom[0]:>2s}"
    )


def _write_receptor(path: Path) -> None:
    lines: list[str] = []
    serial = 1
    for number, x in ((89, 0.0), (91, 4.0), (99, 30.0)):
        for atom, delta in (("N", -1.1), ("CA", 0.0), ("C", 1.2), ("O", 2.0), ("CB", 0.3)):
            lines.append(_atom_line(serial, atom, "ALA", "A", number, x + delta, 0.0, 0.0))
            serial += 1
    path.write_text("\n".join([*lines, "TER", "END"]) + "\n", encoding="ascii")


def _write_pepflow_complex(path: Path, sequence: str) -> None:
    one_to_three = {
        "A": "ALA",
        "K": "LYS",
        "L": "LEU",
    }
    lines: list[str] = []
    serial = 1
    for number, letter in enumerate(sequence, start=1):
        for atom, x in (("N", 0.0), ("CA", 1.2), ("C", 2.4), ("O", 3.2)):
            lines.append(
                _atom_line(serial, atom, one_to_three[letter], "Z", number, x, number * 2.0, 0.0)
            )
            serial += 1
    path.write_text("\n".join([*lines, "TER", "END"]) + "\n", encoding="ascii")


def test_pepglad_pocket_format_uses_auth_residue_ids(tmp_path: Path) -> None:
    request = _request()
    output = write_pepglad_pocket(request.target, tmp_path / "pocket.json")
    assert json.loads(output.read_text(encoding="utf-8")) == [
        ["A", [89, ""]],
        ["A", [91, ""]],
    ]


def test_collect_pepglad_proposals_keeps_every_raw_row(tmp_path: Path) -> None:
    request = _request()
    rows = [
        {"id": "acea_0", "pep_seq": "KLLKLLKK"},
        {"id": "acea_1", "pep_seq": "GIGKFLHSAK"},
    ]
    (tmp_path / "summary.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    for row in rows:
        (tmp_path / f"{row['id']}.pdb").write_text(f"REMARK {row['pep_seq']}\n", encoding="utf-8")
    proposals = collect_pepglad_proposals(request, tmp_path)
    assert [item.raw_rank for item in proposals] == [1, 2]
    assert [item.sequence for item in proposals] == ["KLLKLLKK", "GIGKFLHSAK"]
    assert proposals[0].candidate_id == "acea-pepglad-2026082601-0001"
    assert all(item.valid_sequence for item in proposals)
    assert proposals[0].sequence_sha256 == hashlib.sha256(b"KLLKLLKK").hexdigest()


def test_collect_pepglad_proposals_retains_invalid_raw_sequence(
    tmp_path: Path,
) -> None:
    request = _request().model_copy(update={"requested_proposals": 1})
    row = {"id": "acea_0", "pep_seq": "KLLX"}
    (tmp_path / "summary.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (tmp_path / "acea_0.pdb").write_text("REMARK KLLX\n", encoding="utf-8")
    proposal = collect_pepglad_proposals(request, tmp_path)[0]
    assert proposal.sequence == "KLLX"
    assert proposal.valid_sequence is False
    assert proposal.invalid_reason == "noncanonical_or_empty_sequence"


def test_request_rejects_pocket_residue_on_unknown_chain() -> None:
    payload = _request().model_dump(mode="json")
    payload["target"]["pocket_residues"][0]["chain_id"] = "B"
    with pytest.raises(ValueError, match="unknown chains"):
        TargetStructureGenerationRequest.model_validate(payload)


def test_request_rejects_runtime_generator_drift() -> None:
    payload = _request().model_dump(mode="json")
    payload["runtime"]["generator_id"] = "pepflow"
    with pytest.raises(ValueError, match="runtime pin"):
        TargetStructureGenerationRequest.model_validate(payload)


def test_adapter_rejects_negative_physical_gpu_before_launch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="physical GPU"):
        run_pepglad(
            _request(),
            python_executable=tmp_path / "python",
            source_root=tmp_path / "source",
            checkpoint_path=tmp_path / "model.ckpt",
            receptor_pdb=tmp_path / "target.pdb",
            run_dir=tmp_path / "run",
            gpu_index=-1,
        )


def test_source_contract_adds_target_conditioned_pepmlm_to_every_target() -> None:
    contract = json.loads(
        (
            Path(__file__).parents[1]
            / "config/targets/ampgent_target_structure_generators_20260826.json"
        ).read_text(encoding="utf-8")
    )
    assert contract["sources"]["pepmlm_650m"]["conditioning"].startswith(
        "exact target protein sequence"
    )
    assert all("pepmlm_650m" in row["candidate_generators"] for row in contract["routing"])


def test_pepflow_request_rejects_lengths_above_upstream_limit() -> None:
    payload = _pepflow_request().model_dump(mode="json")
    payload["peptide_length_max_exclusive"] = 27
    with pytest.raises(ValueError, match="3 through 25"):
        TargetStructureGenerationRequest.model_validate(payload)


def test_write_pepflow_case_uses_exact_pocket_and_centroid(tmp_path: Path) -> None:
    receptor = tmp_path / "target.pdb"
    _write_receptor(receptor)
    result = write_pepflow_case(
        _pepflow_request().target,
        receptor,
        tmp_path / "case",
        peptide_length=8,
    )
    parser = PDBParser(QUIET=True)
    pocket = parser.get_structure("pocket", tmp_path / "case" / "pocket.pdb")
    pocket_numbers = [residue.id[1] for residue in pocket.get_residues()]
    assert pocket_numbers == [89, 91]
    peptide = parser.get_structure("peptide", tmp_path / "case" / "peptide.pdb")
    peptide_residues = list(peptide.get_residues())
    assert len(peptide_residues) == 8
    assert {residue.get_parent().id for residue in peptide_residues} == {"Z"}
    peptide_center_x = sum(float(residue["CA"].coord[0]) for residue in peptide_residues) / 8
    assert peptide_center_x == pytest.approx(2.0, abs=1e-3)
    assert result["pocket_residue_count"] == 2
    assert result["peptide_template_semantics"].startswith("masked_polyalanine")


def test_collect_pepflow_proposals_binds_sequence_to_peptide_chain(tmp_path: Path) -> None:
    request = _pepflow_request().model_copy(update={"requested_proposals": 1})
    sequence = "KLLKLLKK"
    structure = tmp_path / "sample_0001.pdb"
    _write_pepflow_complex(structure, sequence)
    (tmp_path / "sequences.jsonl").write_text(
        json.dumps(
            {
                "raw_rank": 1,
                "sequence": sequence,
                "peptide_length": len(sequence),
                "structure_file_name": structure.name,
                "case_id": "acea-pepflow-L08",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    proposal = collect_pepflow_proposals(request, tmp_path, peptide_chain_id="Z")[0]
    assert proposal.sequence == sequence
    assert proposal.valid_sequence is True
    assert proposal.candidate_id == "acea-pepflow-2026082601-0001"


def test_pepflow_embedded_launcher_compiles() -> None:
    compile(PEPFLOW_SEEDED_LAUNCH, "<pepflow-launch>", "exec")


def test_pepflow_adapter_rejects_negative_physical_gpu_before_launch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="physical GPU"):
        run_pepflow(
            _pepflow_request(),
            python_executable=tmp_path / "python",
            source_root=tmp_path / "source",
            checkpoint_path=tmp_path / "model2.pt",
            receptor_pdb=tmp_path / "target.pdb",
            run_dir=tmp_path / "run",
            gpu_index=-1,
        )
