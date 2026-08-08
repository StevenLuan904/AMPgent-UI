from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

CANONICAL_AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"


class GeneratorWeightArtifact(BaseModel):
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=1)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    upstream_digest: str | None = Field(default=None, min_length=8)

    @model_validator(mode="after")
    def require_a_digest(self) -> GeneratorWeightArtifact:
        if self.sha256 is None and self.upstream_digest is None:
            raise ValueError("a weight artifact needs sha256 or an upstream digest")
        return self


class GeneratorReleaseSpec(BaseModel):
    generator_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    display_name: str = Field(min_length=1)
    paper_title: str = Field(min_length=1)
    venue: str = Field(min_length=1)
    publication_year: int = Field(ge=2000, le=2100)
    paper_uri: str = Field(pattern=r"^https://")
    source_uri: str = Field(pattern=r"^https://")
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: str = Field(min_length=1)
    generation_mode: Literal["de_novo", "parent_optimization", "enumeration"]
    weights: list[GeneratorWeightArtifact] = Field(min_length=1)
    internal_score_filtering_enabled: bool = False
    limitations: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_generator_specific_filtering(self) -> GeneratorReleaseSpec:
        if self.internal_score_filtering_enabled:
            raise ValueError(
                "generator-specific AMP/MIC filtering is forbidden in the raw benchmark"
            )
        paths = [item.path for item in self.weights]
        if len(paths) != len(set(paths)):
            raise ValueError("weight artifact paths must be unique")
        return self


class BenchmarkMetricSpec(BaseModel):
    name: str = Field(min_length=1)
    role: Literal["qualification", "profile", "safety", "diagnostic"]
    direction: Literal["minimize", "maximize", "none"]
    evidence_class: Literal["descriptor", "soft", "low_confidence_proxy"]

    @model_validator(mode="after")
    def protect_pepmlm_metrics(self) -> BenchmarkMetricSpec:
        pepmlm_dependent = {
            "conditional_ppl",
            "pepmlm_ppl",
            "target_specific_delta_nll",
        }
        if self.name in pepmlm_dependent and (
            self.role != "diagnostic" or self.evidence_class != "low_confidence_proxy"
        ):
            raise ValueError("PepMLM-dependent metrics are diagnostic-only")
        return self


class GeneratorBenchmarkManifest(BaseModel):
    benchmark_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    version: str = Field(min_length=1)
    execution_status: Literal["weights_pending", "ready", "completed"]
    track: Literal["de_novo"]
    generators: list[GeneratorReleaseSpec] = Field(min_length=2)
    seeds: list[int] = Field(min_length=3)
    raw_proposal_budget_per_seed: int = Field(ge=100)
    selected_valid_unique_per_seed: int = Field(ge=10)
    minimum_length: int = Field(ge=5)
    maximum_length: int = Field(le=100)
    canonical_amino_acids: str = CANONICAL_AMINO_ACIDS
    selection_rule: Literal["raw_order_first_k_valid_unique"]
    missing_policy: Literal["retain_shortfall_no_refill"]
    ranking_method: Literal["pareto_then_lexicographic"]
    bootstrap_unit: Literal["generator_seed"]
    structure_enabled: bool = False
    metrics: list[BenchmarkMetricSpec] = Field(min_length=1)
    scientific_contract: dict[str, bool]

    @field_validator("canonical_amino_acids")
    @classmethod
    def require_canonical_alphabet(cls, value: str) -> str:
        if value != CANONICAL_AMINO_ACIDS:
            raise ValueError("benchmark alphabet must be the 20 canonical amino acids")
        return value

    @model_validator(mode="after")
    def validate_benchmark_contract(self) -> GeneratorBenchmarkManifest:
        if len(self.seeds) != len(set(self.seeds)):
            raise ValueError("benchmark seeds must be unique")
        if self.minimum_length > self.maximum_length:
            raise ValueError("minimum_length cannot exceed maximum_length")
        if self.selected_valid_unique_per_seed > self.raw_proposal_budget_per_seed:
            raise ValueError("selected cohort cannot exceed the raw proposal budget")
        if self.structure_enabled:
            raise ValueError("first-round generator benchmark keeps structure disabled")
        generator_ids = [item.generator_id for item in self.generators]
        if len(generator_ids) != len(set(generator_ids)):
            raise ValueError("generator_id values must be unique")
        if any(item.generation_mode != "de_novo" for item in self.generators):
            raise ValueError("de_novo track cannot mix parent optimization or enumeration")
        metric_names = [item.name for item in self.metrics]
        if len(metric_names) != len(set(metric_names)):
            raise ValueError("benchmark metric names must be unique")
        if self.execution_status in {"ready", "completed"}:
            missing = [
                f"{generator.generator_id}:{artifact.path}"
                for generator in self.generators
                for artifact in generator.weights
                if artifact.sha256 is None
            ]
            if missing:
                raise ValueError(
                    "ready benchmark requires local SHA-256 for every weight: "
                    + ", ".join(missing)
                )
        required_flags = {
            "raw_outputs_frozen_before_metrics",
            "no_score_based_refill",
            "generator_internal_scores_not_used_for_selection",
            "pepmlm_not_used_to_select_winner",
            "no_binding_or_affinity_claim",
        }
        if any(self.scientific_contract.get(flag) is not True for flag in required_flags):
            raise ValueError("scientific_contract is missing a required true flag")
        return self


def audit_raw_generator_cohort(
    records: list[dict[str, Any]],
    *,
    raw_budget: int,
    selected_k: int,
    minimum_length: int,
    maximum_length: int,
) -> dict[str, Any]:
    """Fail closed, then select by raw order without looking at model or metric scores."""
    if len(records) != raw_budget:
        raise ValueError(f"expected exactly {raw_budget} raw records, received {len(records)}")
    ranks = [item.get("raw_rank") for item in records]
    if ranks != list(range(1, raw_budget + 1)):
        raise ValueError("raw_rank must be contiguous, unique, and one-based")

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    invalid_symbol_count = 0
    out_of_length_count = 0
    duplicate_count = 0
    empty_count = 0
    for item in records:
        raw_sequence = item.get("sequence")
        if not isinstance(raw_sequence, str):
            raise ValueError("every raw record must contain a string sequence")
        sequence = "".join(raw_sequence.split()).upper()
        if not sequence:
            empty_count += 1
            continue
        if any(symbol not in CANONICAL_AMINO_ACIDS for symbol in sequence):
            invalid_symbol_count += 1
            continue
        if not minimum_length <= len(sequence) <= maximum_length:
            out_of_length_count += 1
            continue
        digest = hashlib.sha256(sequence.encode("utf-8")).hexdigest()
        if digest in seen:
            duplicate_count += 1
            continue
        seen.add(digest)
        if len(selected) < selected_k:
            selected.append(
                {
                    "raw_rank": item["raw_rank"],
                    "sequence": sequence,
                    "sequence_sha256": digest,
                }
            )

    valid_unique_count = len(seen)
    return {
        "raw_count": len(records),
        "valid_unique_count": valid_unique_count,
        "valid_unique_yield": valid_unique_count / raw_budget,
        "selected_count": len(selected),
        "shortfall_count": max(0, selected_k - len(selected)),
        "empty_count": empty_count,
        "invalid_symbol_count": invalid_symbol_count,
        "out_of_length_count": out_of_length_count,
        "duplicate_count": duplicate_count,
        "selected": selected,
        "selection_rule": "raw_order_first_k_valid_unique",
        "score_fields_ignored": True,
    }
