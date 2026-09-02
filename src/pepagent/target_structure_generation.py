from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from Bio.PDB import PDBIO, PDBParser, Select
from Bio.SeqUtils import seq1
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
        if self.generator_id == "pepflow" and self.peptide_length_max_exclusive > 26:
            raise ValueError("PepFlow supports peptide lengths from 3 through 25")
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


class _PocketSelect(Select):
    def __init__(self, residue_keys: frozenset[tuple[str, int, str]]) -> None:
        self.residue_keys = residue_keys

    def accept_residue(self, residue: object) -> bool:
        parent = residue.get_parent()  # type: ignore[attr-defined]
        hetero, number, insertion = residue.get_id()  # type: ignore[attr-defined]
        return hetero == " " and (parent.id, int(number), insertion.strip()) in self.residue_keys


def _unused_pdb_chain_id(target: TargetStructureInput) -> str:
    for chain_id in "ZYWXVUTSRQPONMLKJIHGFEDCBA":
        if chain_id not in target.receptor_chain_ids:
            return chain_id
    raise ValueError("no single-character PDB chain id remains for the PepFlow peptide mask")


def write_pepflow_case(
    target: TargetStructureInput,
    receptor_pdb: Path,
    case_dir: Path,
    *,
    peptide_length: int,
) -> dict[str, object]:
    """Build the exact pocket + masked-length case expected by PepFlow.

    PepFlow masks all peptide sequence/structure features during conditioning. The
    poly-Ala helix therefore supplies only a valid peptide length and a reproducible
    center. Its center is the selected pocket CA centroid, matching PepFlow's own
    peptide-centering preprocessor without inventing a known bound peptide pose.
    """

    if not receptor_pdb.is_file():
        raise FileNotFoundError(receptor_pdb)
    if not 3 <= peptide_length <= 25:
        raise ValueError("PepFlow peptide length must be in [3, 25]")
    residue_keys = frozenset(
        (item.chain_id, item.auth_residue_number, item.insertion_code)
        for item in target.pocket_residues
    )
    if len(residue_keys) != len(target.pocket_residues):
        raise ValueError("PepFlow pocket residue identities must be unique")

    structure = PDBParser(QUIET=True).get_structure(target.structure_id, receptor_pdb)
    model = next(structure.get_models())
    found: set[tuple[str, int, str]] = set()
    ca_positions: list[tuple[float, float, float]] = []
    for chain in model:
        for residue in chain:
            hetero, number, insertion = residue.id
            key = (chain.id, int(number), insertion.strip())
            if hetero == " " and key in residue_keys:
                if not all(name in residue for name in ("N", "CA", "C")):
                    raise ValueError(f"PepFlow pocket residue lacks backbone atoms: {key}")
                found.add(key)
                ca = residue["CA"].coord
                ca_positions.append((float(ca[0]), float(ca[1]), float(ca[2])))
    missing = sorted(residue_keys - found)
    if missing:
        raise ValueError(f"PepFlow pocket residues absent from target coordinate: {missing}")

    center = tuple(sum(p[axis] for p in ca_positions) / len(ca_positions) for axis in range(3))
    case_dir.mkdir(parents=True, exist_ok=False)
    pocket_path = case_dir / "pocket.pdb"
    writer = PDBIO()
    writer.set_structure(model)
    writer.save(str(pocket_path), _PocketSelect(residue_keys))

    peptide_chain_id = _unused_pdb_chain_id(target)
    peptide_path = case_dir / "peptide.pdb"
    lines: list[str] = []
    serial = 1
    midpoint = (peptide_length - 1) / 2.0
    helix_radials = [
        (
            math.cos(math.radians(100.0 * index)),
            math.sin(math.radians(100.0 * index)),
            0.0,
        )
        for index in range(peptide_length)
    ]
    mean_radial = tuple(
        sum(radial[axis] for radial in helix_radials) / peptide_length for axis in range(3)
    )
    for index in range(peptide_length):
        theta = math.radians(100.0 * index)
        radial = helix_radials[index]
        tangent = (-math.sin(theta), math.cos(theta), 1.5 / 2.3)
        norm = math.sqrt(sum(value * value for value in tangent))
        tangent = tuple(value / norm for value in tangent)
        ca = (
            center[0] + 2.3 * (radial[0] - mean_radial[0]),
            center[1] + 2.3 * (radial[1] - mean_radial[1]),
            center[2] + 1.5 * (index - midpoint),
        )
        atoms = {
            "N": tuple(ca[j] - 1.20 * tangent[j] - 0.35 * radial[j] for j in range(3)),
            "CA": ca,
            "C": tuple(ca[j] + 1.22 * tangent[j] - 0.30 * radial[j] for j in range(3)),
            "O": tuple(ca[j] + 1.75 * tangent[j] - 1.20 * radial[j] for j in range(3)),
            "CB": tuple(ca[j] + 1.53 * radial[j] for j in range(3)),
        }
        for atom_name, position in atoms.items():
            element = atom_name[0]
            lines.append(
                f"ATOM  {serial:5d} {atom_name:^4s} ALA {peptide_chain_id}{index + 1:4d}    "
                f"{position[0]:8.3f}{position[1]:8.3f}{position[2]:8.3f}"
                f"  1.00  0.00          {element:>2s}"
            )
            serial += 1
    lines.extend([f"TER   {serial:5d}      ALA {peptide_chain_id}{peptide_length:4d}", "END"])
    peptide_path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return {
        "case_dir": str(case_dir),
        "peptide_length": peptide_length,
        "peptide_chain_id": peptide_chain_id,
        "pocket_residue_count": len(found),
        "pocket_ca_centroid_angstrom": list(center),
        "pocket_sha256": sha256_file(pocket_path),
        "peptide_template_sha256": sha256_file(peptide_path),
        "peptide_template_semantics": "masked_polyalanine_length_and_pocket_center_only",
    }


def collect_pepflow_proposals(
    request: TargetStructureGenerationRequest,
    output_dir: Path,
    *,
    peptide_chain_id: str,
) -> tuple[TargetStructureProposal, ...]:
    summary_path = output_dir / "sequences.jsonl"
    if not summary_path.is_file():
        raise FileNotFoundError(summary_path)
    rows = [
        json.loads(line)
        for line in summary_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != request.requested_proposals:
        raise ValueError(
            "PepFlow proposal count mismatch: "
            f"expected {request.requested_proposals}, got {len(rows)}"
        )
    proposals: list[TargetStructureProposal] = []
    for raw_rank, row in enumerate(rows, start=1):
        if int(row["raw_rank"]) != raw_rank:
            raise ValueError("PepFlow raw ranks are not contiguous")
        sequence = "".join(str(row["sequence"]).split()).upper()
        pdb_path = output_dir / str(row["structure_file_name"])
        if not pdb_path.is_file():
            raise FileNotFoundError(pdb_path)
        parsed = PDBParser(QUIET=True).get_structure("pepflow", pdb_path)
        chains = list(next(parsed.get_models()).get_chains())
        peptide_chains = [chain for chain in chains if chain.id == peptide_chain_id]
        if len(peptide_chains) != 1:
            raise ValueError("PepFlow output lacks the uniquely identified peptide chain")
        pdb_sequence = "".join(
            seq1(residue.get_resname(), undef_code="X")
            for residue in peptide_chains[0]
            if residue.id[0] == " "
        )
        if pdb_sequence != sequence:
            raise ValueError("PepFlow sequence record does not match its structure")
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
                candidate_id=f"{request.target.target_key}-pepflow-{request.seed}-{raw_rank:04d}",
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
