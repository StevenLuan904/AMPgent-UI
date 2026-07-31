from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TargetSpec(BaseModel):
    name: str
    sequence: str
    organism: str | None = None
    accession: str | None = None
    source_database: str | None = None
    source_uri: str | None = None
    source_version: str | None = None
    source_retrieved_at: datetime | None = None
    pocket_residues: list[int] = Field(default_factory=list)

    @field_validator("sequence")
    @classmethod
    def normalize_sequence(cls, value: str) -> str:
        normalized = "".join(value.split()).upper()
        allowed = set("ACDEFGHIKLMNPQRSTVWY")
        invalid = sorted(set(normalized) - allowed)
        if invalid:
            raise ValueError(f"invalid amino-acid symbols: {invalid}")
        return normalized


class ExperimentSpec(BaseModel):
    target: TargetSpec
    peptide_lengths: list[int] = Field(default_factory=lambda: [12, 16, 20])
    candidates_per_length: int = 64
    structure_top_k: int = 8
    generations: int = 2
    seed: int = 20260731
    pepmlm_model: str = "ChatterjeeLab/PepMLM-650M"
    boltz_method: str = "boltz2"
    diffusion_samples: int = 5
    boltz_recycling_steps: int = 3
    boltz_sampling_steps: int = 200
    boltz_use_potentials: bool = True
    boltz_no_kernels: bool = True
    use_msa_server: bool = True
    rosetta_enabled: bool = False
    rosetta_top_k: int = Field(default=1, ge=1)
    rosetta_nstruct: int = Field(default=200, ge=1)
    rosetta_pair_iptm_min: float = Field(default=0.5, ge=0, le=1)
    rosetta_score_function: str = "ref2015"
    affinity_evaluators: list[str] = Field(
        default_factory=list,
        description=(
            "Reserved for affinity evaluators that pass the reproducibility admission gate."
        ),
    )

    @field_validator("affinity_evaluators")
    @classmethod
    def reject_frozen_evaluators(cls, value: list[str]) -> list[str]:
        if "peppap" in {name.lower() for name in value}:
            raise ValueError("PepPAP is frozen and is not admitted to the experiment workflow")
        return value

    @field_validator("rosetta_score_function")
    @classmethod
    def require_calibrated_rosetta_score_function(cls, value: str) -> str:
        if value != "ref2015":
            raise ValueError("MVP-v2 admits only the versioned ref2015 Rosetta protocol")
        return value

    @model_validator(mode="after")
    def validate_rosetta_protocol(self) -> "ExperimentSpec":
        if self.rosetta_enabled and self.rosetta_nstruct < 200:
            raise ValueError(
                "decision-bearing FlexPepDock runs require at least 200 refinement decoys"
            )
        if self.rosetta_top_k > self.structure_top_k:
            raise ValueError("rosetta_top_k cannot exceed structure_top_k")
        return self


class CandidateRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    sequence: str
    sequence_sha256: str
    generation: int
    parent_id: UUID | None
    status: str
    created_at: datetime


class ModelEvidence(BaseModel):
    model_name: str
    model_version: str
    weights_sha256: str
    environment_sha256: str
    parameters: dict[str, Any]
    random_seed: int | None = None
    input_sha256: str
    output_sha256: str | None = None
    artifact_uris: list[str] = Field(default_factory=list)
    out_of_domain: bool = False
    limitations: list[str] = Field(default_factory=list)


class GeneratedPeptide(BaseModel):
    sequence: str
    conditional_nll: float
    conditional_ppl: float
    per_residue_log_probabilities: list[float]
    seed: int


class Boltz2Result(BaseModel):
    confidence_score: float | None = None
    iptm: float | None = None
    pair_iptm: float | None = None
    complex_iplddt: float | None = None
    artifacts: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class AffinityPrediction(BaseModel):
    evaluator: str
    value: float | None = None
    unit: str
    derived_kd_molar: float | None = None
    status: str
    out_of_domain: bool = False
    limitations: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class PocketEvidenceSpec(BaseModel):
    evidence_kind: str
    evidence_grade: str
    source_type: str
    source_uri: str
    source_accession: str | None = None
    source_version: str | None = None
    source_revision_date: datetime | None = None
    retrieved_at: datetime
    chain_ids: list[str] = Field(default_factory=list)
    source_residue_indices: list[int] = Field(default_factory=list)
    target_residue_indices: list[int] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    experimental_method: str | None = None
    resolution_angstrom: float | None = Field(default=None, gt=0)
    mapping: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence_grade")
    @classmethod
    def validate_evidence_grade(cls, value: str) -> str:
        if value not in {"A", "B", "C", "D", "U"}:
            raise ValueError("evidence_grade must be one of A, B, C, D, U")
        return value


class TargetPocketSpec(BaseModel):
    key: str
    name: str
    pocket_type: str
    functional_role: str
    status: str
    evidence_grade: str
    evidence_score: float = Field(ge=0, le=1)
    conditioning_priority: str
    conditioning_enabled: bool
    residue_indices: list[int] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    evidence: list[PocketEvidenceSpec] = Field(default_factory=list)

    @field_validator("evidence_grade")
    @classmethod
    def validate_evidence_grade(cls, value: str) -> str:
        if value not in {"A", "B", "C", "D", "U"}:
            raise ValueError("evidence_grade must be one of A, B, C, D, U")
        return value


class PocketCatalogTargetSpec(BaseModel):
    name: str
    organism: str
    accession: str
    sequence: str
    role: str
    source_database: str
    source_uri: str
    source_version: str
    source_retrieved_at: datetime
    source_document_sha256: str | None = None
    pockets: list[TargetPocketSpec]

    @field_validator("sequence")
    @classmethod
    def normalize_sequence(cls, value: str) -> str:
        return TargetSpec.normalize_sequence(value)

    @model_validator(mode="after")
    def validate_residue_numbering(self) -> "PocketCatalogTargetSpec":
        length = len(self.sequence)
        for pocket in self.pockets:
            invalid = [index for index in pocket.residue_indices if index < 1 or index > length]
            if invalid:
                raise ValueError(
                    f"{self.accession}/{pocket.key} has residues outside 1..{length}: {invalid}"
                )
            for evidence in pocket.evidence:
                invalid = [
                    index
                    for index in evidence.target_residue_indices
                    if index < 1 or index > length
                ]
                if invalid:
                    raise ValueError(
                        f"{self.accession}/{pocket.key} evidence has invalid target residues: "
                        f"{invalid}"
                    )
        return self


class PocketCatalogSpec(BaseModel):
    catalog_version: str
    grading_rubric: dict[str, str]
    targets: list[PocketCatalogTargetSpec]
