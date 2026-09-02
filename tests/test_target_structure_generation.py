from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pepagent.model_workers.pepglad_target_generator_cli import run_pepglad
from pepagent.target_structure_generation import (
    TargetStructureGenerationRequest,
    collect_pepglad_proposals,
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
