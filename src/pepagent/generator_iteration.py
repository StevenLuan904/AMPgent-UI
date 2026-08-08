from __future__ import annotations

import hashlib
import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from pepagent.generator_benchmark import (
    CANONICAL_AMINO_ACIDS,
    GeneratorWeightArtifact,
)


def sequence_sha256(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()


class ParentAnchor(BaseModel):
    parent_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    source_benchmark_id: str = Field(min_length=1)
    source_candidate_id: str = Field(min_length=1)
    source_seed: int
    selection_slot: Literal[
        "max_amp_probability",
        "min_llamp_mic",
        "min_amp_read_mic",
        "min_hemolysis_above_median_amp",
    ]
    sequence: str = Field(min_length=10, max_length=25)
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_sequence_identity(self) -> ParentAnchor:
        if self.sequence != self.sequence.strip().upper():
            raise ValueError("parent sequence must already be canonicalized")
        if any(symbol not in CANONICAL_AMINO_ACIDS for symbol in self.sequence):
            raise ValueError("parent sequence contains a noncanonical amino acid")
        if sequence_sha256(self.sequence) != self.sequence_sha256:
            raise ValueError("parent sequence SHA-256 mismatch")
        return self


class HydrAMPRelease(BaseModel):
    generator_id: Literal["hydramp"]
    source_uri: str = Field(pattern=r"^https://")
    source_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    license: str = Field(min_length=1)
    weights: list[GeneratorWeightArtifact] = Field(min_length=1)
    generation_mode: Literal["parent_optimization"]
    amp_condition: Literal[1]
    mic_condition: Literal[1]
    internal_amp_classifier_calls_allowed: Literal[False]
    internal_mic_classifier_calls_allowed: Literal[False]


class ParentSelectionContract(BaseModel):
    source_benchmark_id: Literal["amp_generator_de_novo_v23"]
    source_generator_id: Literal["hydramp"]
    eligibility_max_macrel_hemolysis_probability: float = Field(ge=0, le=1)
    eligibility_max_toxinpred3_ml_score: float = Field(ge=0, le=1)
    anchors_per_source_seed: Literal[4]
    slots_in_order: list[str] = Field(min_length=4, max_length=4)
    duplicate_slot_policy: Literal["take_next_eligible_unselected"]
    score_aggregation: Literal["none"]

    @field_validator("slots_in_order")
    @classmethod
    def require_frozen_slot_order(cls, value: list[str]) -> list[str]:
        expected = [
            "max_amp_probability",
            "min_llamp_mic",
            "min_amp_read_mic",
            "min_hemolysis_above_median_amp",
        ]
        if value != expected:
            raise ValueError("parent-selection slot order does not match v24 preregistration")
        return value


class SafetyNonInferiority(BaseModel):
    paired_median_macrel_hemolysis_margin: float = Field(ge=0, le=0.2)
    paired_median_toxinpred3_ml_margin: float = Field(ge=0, le=0.2)


class ConfirmationRule(BaseModel):
    bootstrap_unit: Literal["parent"]
    confidence_level: float = Field(gt=0.5, lt=1)
    required_signals: list[str] = Field(min_length=4)
    binding_claim_allowed: Literal[False]


class HydrAMPParentOptimizationManifest(BaseModel):
    benchmark_id: Literal["amp_generator_hydramp_analogue_v24"]
    version: str = Field(min_length=1)
    execution_status: Literal[
        "preregistered_development", "development_complete", "completed"
    ]
    track: Literal["parent_optimization"]
    generator: HydrAMPRelease
    parent_selection: ParentSelectionContract
    parents: list[ParentAnchor] = Field(min_length=12, max_length=12)
    development_seeds: list[int] = Field(min_length=2, max_length=2)
    confirmation_seed: int
    development_temperatures: list[float] = Field(min_length=3, max_length=3)
    confirmation_temperature_policy: Literal[
        "freeze_pareto_selected_development_temperature_before_confirmation"
    ]
    raw_proposals_per_parent_temperature_seed: int = Field(ge=1)
    selected_valid_unique_per_parent_temperature_seed: int = Field(ge=1)
    minimum_length: int = Field(ge=5)
    maximum_length: int = Field(le=25)
    selection_rule: Literal["raw_order_first_k_valid_unique"]
    missing_policy: Literal["retain_shortfall_no_refill"]
    cell_seed_derivation: Literal["sha256-v1"]
    essential_metrics: list[str] = Field(min_length=5)
    optional_metrics: list[str] = Field(default_factory=list)
    safety_noninferiority: SafetyNonInferiority
    development_decision_rule: Literal[
        "safety_gate_then_pareto_activity_mic_then_lower_temperature_tiebreak"
    ]
    confirmation_rule: ConfirmationRule
    fail_closed_conditions: list[str] = Field(min_length=1)
    scientific_contract: dict[str, bool]

    @model_validator(mode="after")
    def validate_preregistration(self) -> HydrAMPParentOptimizationManifest:
        if len(set(self.development_seeds)) != len(self.development_seeds):
            raise ValueError("development seeds must be unique")
        if self.confirmation_seed in self.development_seeds:
            raise ValueError("confirmation seed must be held out")
        if any(not math.isfinite(value) or value <= 0 for value in self.development_temperatures):
            raise ValueError("temperatures must be finite and positive")
        if len(set(self.development_temperatures)) != len(self.development_temperatures):
            raise ValueError("temperatures must be unique")
        if self.development_temperatures != [0.5, 2.0, 5.0]:
            raise ValueError("v24 development temperature grid is frozen")
        if self.minimum_length > self.maximum_length:
            raise ValueError("minimum_length cannot exceed maximum_length")
        if (
            self.selected_valid_unique_per_parent_temperature_seed
            > self.raw_proposals_per_parent_temperature_seed
        ):
            raise ValueError("selected count cannot exceed raw proposals per cell")
        parent_ids = [parent.parent_id for parent in self.parents]
        parent_shas = [parent.sequence_sha256 for parent in self.parents]
        source_ids = [parent.source_candidate_id for parent in self.parents]
        if len(parent_ids) != len(set(parent_ids)):
            raise ValueError("parent_id values must be unique")
        if len(parent_shas) != len(set(parent_shas)):
            raise ValueError("parent sequence SHA-256 values must be unique")
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source candidate IDs must be unique")
        source_seeds = {parent.source_seed for parent in self.parents}
        if len(source_seeds) != 3:
            raise ValueError("parents must cover all three v23 source seeds")
        for seed in source_seeds:
            seed_parents = [parent for parent in self.parents if parent.source_seed == seed]
            if len(seed_parents) != self.parent_selection.anchors_per_source_seed:
                raise ValueError("each v23 source seed must contribute exactly four parents")
            if {parent.selection_slot for parent in seed_parents} != set(
                self.parent_selection.slots_in_order
            ):
                raise ValueError("each source seed must cover every frozen selection slot")
        pepmlm_metrics = {"target_specific_delta_nll", "pepmlm_ppl", "conditional_ppl"}
        if pepmlm_metrics.intersection(self.essential_metrics):
            raise ValueError("PepMLM-dependent metrics cannot be essential")
        required_flags = {
            "v23_outputs_immutable",
            "raw_outputs_frozen_before_metrics",
            "no_score_based_refill",
            "generator_internal_scores_not_used",
            "pepmlm_diagnostic_only",
            "no_binding_or_affinity_claim",
        }
        if any(self.scientific_contract.get(flag) is not True for flag in required_flags):
            raise ValueError("scientific_contract is missing a required true flag")
        return self

    @property
    def development_raw_budget(self) -> int:
        return (
            len(self.parents)
            * len(self.development_seeds)
            * len(self.development_temperatures)
            * self.raw_proposals_per_parent_temperature_seed
        )

    @property
    def confirmation_raw_budget(self) -> int:
        return len(self.parents) * self.raw_proposals_per_parent_temperature_seed


class HydrAMPRawAnalogueRequest(BaseModel):
    benchmark_id: Literal["amp_generator_hydramp_analogue_v24"]
    phase: Literal["development", "confirmation"]
    seed: int
    temperatures: list[float] = Field(min_length=1)
    raw_proposals_per_cell: int = Field(ge=1)
    parents: list[ParentAnchor] = Field(min_length=1)
    amp_condition: Literal[1]
    mic_condition: Literal[1]
    cell_seed_derivation: Literal["sha256-v1"]
    internal_amp_classifier_calls_allowed: Literal[False]
    internal_mic_classifier_calls_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_request(self) -> HydrAMPRawAnalogueRequest:
        if any(not math.isfinite(value) or value <= 0 for value in self.temperatures):
            raise ValueError("temperatures must be finite and positive")
        if len(set(self.temperatures)) != len(self.temperatures):
            raise ValueError("request temperatures must be unique")
        parent_ids = [parent.parent_id for parent in self.parents]
        if len(parent_ids) != len(set(parent_ids)):
            raise ValueError("request parent IDs must be unique")
        if self.phase == "confirmation" and len(self.temperatures) != 1:
            raise ValueError("confirmation request must contain one frozen temperature")
        return self


def validate_raw_analogue_request_against_manifest(
    manifest: HydrAMPParentOptimizationManifest,
    request_payload: dict[str, object],
) -> HydrAMPRawAnalogueRequest:
    request = HydrAMPRawAnalogueRequest.model_validate(request_payload)
    if request.raw_proposals_per_cell != (
        manifest.raw_proposals_per_parent_temperature_seed
    ):
        raise ValueError("request raw budget does not match the v24 manifest")
    expected_parents = [
        (
            parent.parent_id,
            parent.sequence,
            parent.sequence_sha256,
            parent.source_candidate_id,
        )
        for parent in manifest.parents
    ]
    request_parents = [
        (
            parent.parent_id,
            parent.sequence,
            parent.sequence_sha256,
            parent.source_candidate_id,
        )
        for parent in request.parents
    ]
    if request_parents != expected_parents:
        raise ValueError("request parents or their frozen order do not match the manifest")
    if request.phase == "development":
        if request.seed not in manifest.development_seeds:
            raise ValueError("development request uses an unregistered seed")
        if request.temperatures != manifest.development_temperatures:
            raise ValueError("development request temperature grid does not match the manifest")
    else:
        if request.seed != manifest.confirmation_seed:
            raise ValueError("confirmation request does not use the held-out seed")
        if request.temperatures[0] not in manifest.development_temperatures:
            raise ValueError("confirmation temperature was not in the development grid")
    return request
