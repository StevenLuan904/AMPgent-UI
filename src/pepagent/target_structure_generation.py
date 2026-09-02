from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CANONICAL_AA = frozenset("ACDEFGHIKLMNPQRSTVWY")
TargetStructureGenerator = Literal["pepglad", "pepflow"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PocketResidue(FrozenModel):
    chain_id: str = Field(min_length=1, max_length=4)
    auth_residue_number: int
    insertion_code: str = Field(default="", max_length=1)


class TargetStructureInput(FrozenModel):
    target_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    accession: str = Field(min_length=1)
    target_sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structure_id: str = Field(min_length=1)
    structure_uri: str = Field(min_length=1)
    structure_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receptor_chain_ids: tuple[str, ...]
    pocket_key: str = Field(min_length=1)
    pocket_evidence_grade: Literal["A", "B", "C"]
    pocket_residues: tuple[PocketResidue, ...]

    @model_validator(mode="after")
    def validate_structure_input(self) -> TargetStructureInput:
        if not self.receptor_chain_ids:
            raise ValueError("at least one receptor chain is required")
        if len(set(self.receptor_chain_ids)) != len(self.receptor_chain_ids):
            raise ValueError("receptor chain identities must be unique")
        if not self.pocket_residues:
            raise ValueError("a target-specific generator requires pocket residues")
        unknown_chains = {residue.chain_id for residue in self.pocket_residues} - set(
            self.receptor_chain_ids
        )
        if unknown_chains:
            raise ValueError(f"pocket residues reference unknown chains: {unknown_chains}")
        return self


class TargetStructureRuntimePin(FrozenModel):
    generator_id: TargetStructureGenerator
    source_repository: str = Field(min_length=1)
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: Literal["MIT"]
    model_variant: str = Field(min_length=1)
    checkpoint_uri: str = Field(min_length=1)
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checkpoint_size_bytes: int = Field(gt=0)


class TargetStructureGenerationRequest(FrozenModel):
    schema_version: Literal["ampgent.target-structure-generator-request.1"] = (
        "ampgent.target-structure-generator-request.1"
    )
    generator_id: TargetStructureGenerator
    target: TargetStructureInput
    runtime: TargetStructureRuntimePin
    seed: int = Field(ge=0)
    requested_proposals: int = Field(gt=0, le=1000)
    peptide_length_min: int = Field(ge=5, le=50)
    peptide_length_max_exclusive: int = Field(ge=6, le=51)
    persist_every_raw_occurrence: Literal[True] = True
    internal_score_filtering_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_request(self) -> TargetStructureGenerationRequest:
        if self.runtime.generator_id != self.generator_id:
            raise ValueError("runtime pin does not match the requested generator")
        if self.peptide_length_max_exclusive <= self.peptide_length_min:
            raise ValueError("peptide length range is empty")
        return self


class TargetStructureProposal(FrozenModel):
    raw_rank: int = Field(gt=0)
    candidate_id: str = Field(min_length=1)
    sequence: str
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    valid_sequence: bool
    invalid_reason: str | None = None
    structure_file_name: str = Field(min_length=1)
    structure_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structure_size_bytes: int = Field(gt=0)

    @field_validator("sequence")
    @classmethod
    def validate_sequence(cls, value: str) -> str:
        return "".join(value.split()).upper()

    @model_validator(mode="after")
    def validate_sequence_hash(self) -> TargetStructureProposal:
        if hashlib.sha256(self.sequence.encode("utf-8")).hexdigest() != (self.sequence_sha256):
            raise ValueError("generated sequence sha256 mismatch")
        canonical = bool(self.sequence) and not (set(self.sequence) - CANONICAL_AA)
        if self.valid_sequence and (not canonical or self.invalid_reason is not None):
            raise ValueError("valid generated sequence has invalid sequence semantics")
        if not self.valid_sequence and not self.invalid_reason:
            raise ValueError("invalid generated sequence requires an audit reason")
        return self


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_pepglad_pocket(target: TargetStructureInput, output_path: Path) -> Path:
    """Write PepGLAD's ``[[chain, [auth_id, insertion]], ...]`` pocket format."""

    payload = [
        [
            residue.chain_id,
            [residue.auth_residue_number, residue.insertion_code],
        ]
        for residue in target.pocket_residues
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return output_path


def collect_pepglad_proposals(
    request: TargetStructureGenerationRequest,
    output_dir: Path,
) -> tuple[TargetStructureProposal, ...]:
    summary_path = output_dir / "summary.jsonl"
    if not summary_path.is_file():
        raise FileNotFoundError(summary_path)
    rows = [
        json.loads(line)
        for line in summary_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != request.requested_proposals:
        raise ValueError(
            "PepGLAD proposal count mismatch: "
            f"expected {request.requested_proposals}, got {len(rows)}"
        )
    proposals: list[TargetStructureProposal] = []
    for raw_rank, row in enumerate(rows, start=1):
        sequence = "".join(str(row["pep_seq"]).split()).upper()
        pdb_path = output_dir / f"{row['id']}.pdb"
        if not pdb_path.is_file():
            raise FileNotFoundError(pdb_path)
        valid_sequence = bool(sequence) and not (set(sequence) - CANONICAL_AA)
        invalid_reason = None
        if not valid_sequence:
            invalid_reason = "noncanonical_or_empty_sequence"
        elif not (
            request.peptide_length_min <= len(sequence) < request.peptide_length_max_exclusive
        ):
            valid_sequence = False
            invalid_reason = "outside_frozen_length_range"
        proposals.append(
            TargetStructureProposal(
                raw_rank=raw_rank,
                candidate_id=(f"{request.target.target_key}-pepglad-{request.seed}-{raw_rank:04d}"),
                sequence=sequence,
                sequence_sha256=hashlib.sha256(sequence.encode("utf-8")).hexdigest(),
                valid_sequence=valid_sequence,
                invalid_reason=invalid_reason,
                structure_file_name=pdb_path.name,
                structure_sha256=sha256_file(pdb_path),
                structure_size_bytes=pdb_path.stat().st_size,
            )
        )
    return tuple(proposals)
