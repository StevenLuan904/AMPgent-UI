from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
