from __future__ import annotations

from collections.abc import Iterable
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pepagent.provenance.hashing import sha256_json, sha256_text
from pepagent.v38_sequence_first_multitarget import KnowledgeUseTrace

CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GeneratorCell(FrozenModel):
    ordinal: int = Field(ge=0)
    generator_id: Literal["hydramp", "ampgan_v2", "amp_designer"]
    seed: int
    requested_proposals: int = Field(gt=0)


class V38SequenceExecutionContract(FrozenModel):
    schema_version: Literal["v38.sequence-execution-contract.1"] = (
        "v38.sequence-execution-contract.1"
    )
    cells: tuple[GeneratorCell, ...]
    expected_raw_occurrences: int = Field(gt=0)
    score_all_valid_unique_proposals: Literal[True] = True
    first_k_retention_forbidden: Literal[True] = True
    invalid_and_duplicate_denominator_required: Literal[True] = True
    metric_plugins: tuple[str, ...]
    required_sequence_metrics: frozenset[str]

    @model_validator(mode="after")
    def validate_contract(self) -> V38SequenceExecutionContract:
        if [cell.ordinal for cell in self.cells] != list(range(len(self.cells))):
            raise ValueError("generator cell ordinals must be contiguous")
        identities = [(cell.generator_id, cell.seed) for cell in self.cells]
        if len(identities) != len(set(identities)):
            raise ValueError("generator cell identities must be unique")
        if sum(cell.requested_proposals for cell in self.cells) != self.expected_raw_occurrences:
            raise ValueError("raw occurrence budget does not match generator cells")
        if len(self.metric_plugins) != len(set(self.metric_plugins)):
            raise ValueError("sequence metric plugins must be unique")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


def build_default_v38_sequence_contract() -> V38SequenceExecutionContract:
    generators = ("hydramp", "ampgan_v2", "amp_designer")
    seeds = (
        20270371,
        20270372,
        20270373,
        20270374,
        20270375,
        20270376,
        20270377,
        20270378,
        20270379,
    )
    cells = tuple(
        GeneratorCell(
            ordinal=ordinal,
            generator_id=generators[ordinal // 3],
            seed=seed,
            requested_proposals=100,
        )
        for ordinal, seed in enumerate(seeds)
    )
    return V38SequenceExecutionContract(
        cells=cells,
        expected_raw_occurrences=900,
        metric_plugins=(
            "physicochemical_developability",
            "hemolysis_risk",
            "mic_potency",
            "mic_potency_amp_read",
            "toxicity_risk",
        ),
        required_sequence_metrics=frozenset(
            {
                "hydrophobicity",
                "hydrophobic_moment",
                "net_charge",
                "instability_index",
                "hemolysis_risk",
                "llamp_log10_mic_um",
                "amp_read_log10_mic_um",
                "toxinpred3_hybrid_score",
                "toxinpred3_label",
            }
        ),
    )


class RawProposal(FrozenModel):
    generator_id: str
    seed: int
    raw_rank: int = Field(ge=1)
    sequence: str


class ProposalOccurrenceDecision(FrozenModel):
    source_ordinal: int = Field(ge=1)
    generator_id: str
    seed: int
    raw_rank: int = Field(ge=1)
    normalized_sequence: str
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    disposition: Literal["promoted_for_scoring", "invalid", "duplicate"]
    promoted_for_scoring: bool
    duplicate_of_source_ordinal: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_disposition(self) -> ProposalOccurrenceDecision:
        if self.promoted_for_scoring != (self.disposition == "promoted_for_scoring"):
            raise ValueError("proposal promotion flag conflicts with disposition")
        if (self.disposition == "duplicate") != (self.duplicate_of_source_ordinal is not None):
            raise ValueError("duplicate occurrence requires exactly one earlier source ordinal")
        return self


class ScoreAllProposalCohort(FrozenModel):
    schema_version: Literal["v38.score-all-proposal-cohort.1"] = (
        "v38.score-all-proposal-cohort.1"
    )
    execution_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    occurrences: tuple[ProposalOccurrenceDecision, ...]
    promoted_sequence_sha256: tuple[str, ...]
    raw_occurrence_count: int = Field(ge=0)
    promoted_unique_count: int = Field(ge=0)
    invalid_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    first_k_retention_used: Literal[False] = False

    @model_validator(mode="after")
    def validate_counts(self) -> ScoreAllProposalCohort:
        if self.raw_occurrence_count != len(self.occurrences):
            raise ValueError("raw occurrence count drifted")
        disposition_counts = {
            disposition: sum(item.disposition == disposition for item in self.occurrences)
            for disposition in ("promoted_for_scoring", "invalid", "duplicate")
        }
        if self.promoted_unique_count != disposition_counts["promoted_for_scoring"]:
            raise ValueError("promoted occurrence count drifted")
        if self.invalid_count != disposition_counts["invalid"]:
            raise ValueError("invalid occurrence count drifted")
        if self.duplicate_count != disposition_counts["duplicate"]:
            raise ValueError("duplicate occurrence count drifted")
        promoted = tuple(
            item.sequence_sha256
            for item in self.occurrences
            if item.disposition == "promoted_for_scoring"
        )
        if promoted != self.promoted_sequence_sha256:
            raise ValueError("promoted sequence order drifted")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


def build_score_all_proposal_cohort(
    contract: V38SequenceExecutionContract,
    proposals: Iterable[RawProposal],
) -> ScoreAllProposalCohort:
    expected_cells = {(cell.generator_id, cell.seed): cell for cell in contract.cells}
    cell_counts = {identity: 0 for identity in expected_cells}
    first_source_by_sequence: dict[str, int] = {}
    occurrences: list[ProposalOccurrenceDecision] = []
    for source_ordinal, proposal in enumerate(proposals, start=1):
        identity = (proposal.generator_id, proposal.seed)
        if identity not in expected_cells:
            raise ValueError("proposal references an undeclared generator cell")
        cell_counts[identity] += 1
        if proposal.raw_rank != cell_counts[identity]:
            raise ValueError("proposal raw ranks must be contiguous within each cell")
        sequence = "".join(proposal.sequence.split()).upper()
        valid = bool(sequence) and 10 <= len(sequence) <= 25 and not (
            set(sequence) - CANONICAL_AMINO_ACIDS
        )
        duplicate_of = first_source_by_sequence.get(sequence) if valid else None
        if not valid:
            disposition = "invalid"
        elif duplicate_of is not None:
            disposition = "duplicate"
        else:
            disposition = "promoted_for_scoring"
            first_source_by_sequence[sequence] = source_ordinal
        occurrences.append(
            ProposalOccurrenceDecision(
                source_ordinal=source_ordinal,
                generator_id=proposal.generator_id,
                seed=proposal.seed,
                raw_rank=proposal.raw_rank,
                normalized_sequence=sequence,
                sequence_sha256=sha256_text(sequence),
                disposition=disposition,
                promoted_for_scoring=disposition == "promoted_for_scoring",
                duplicate_of_source_ordinal=duplicate_of,
            )
        )
    if cell_counts != {
        identity: cell.requested_proposals for identity, cell in expected_cells.items()
    }:
        raise ValueError("proposal set does not exactly cover the frozen generator budget")
    return ScoreAllProposalCohort(
        execution_contract_sha256=contract.sha256(),
        occurrences=tuple(occurrences),
        promoted_sequence_sha256=tuple(
            item.sequence_sha256
            for item in occurrences
            if item.disposition == "promoted_for_scoring"
        ),
        raw_occurrence_count=len(occurrences),
        promoted_unique_count=sum(
            item.disposition == "promoted_for_scoring" for item in occurrences
        ),
        invalid_count=sum(item.disposition == "invalid" for item in occurrences),
        duplicate_count=sum(item.disposition == "duplicate" for item in occurrences),
    )


class RefinementChildProposal(FrozenModel):
    parent_candidate_id: UUID
    parent_sequence: str
    child_sequence: str
    refinement_round: int = Field(ge=1, le=5)
    mutation_rationale: str = Field(min_length=1)
    knowledge_traces: tuple[KnowledgeUseTrace, ...] = Field(min_length=1)
    unchanged_parent_control_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_child(self) -> RefinementChildProposal:
        parent = "".join(self.parent_sequence.split()).upper()
        child = "".join(self.child_sequence.split()).upper()
        if child == parent:
            raise ValueError("refinement child must differ from its parent")
        if not 10 <= len(child) <= 25 or set(child) - CANONICAL_AMINO_ACIDS:
            raise ValueError("refinement child is not a valid short peptide")
        if not any(trace.decision == "adopt" for trace in self.knowledge_traces):
            raise ValueError("refinement child requires an adopted knowledge trace")
        expected_control = sha256_json(
            {
                "parent_candidate_id": str(self.parent_candidate_id),
                "parent_sequence": parent,
                "control": "unchanged_parent",
                "refinement_round": self.refinement_round,
            }
        )
        if self.unchanged_parent_control_sha256 != expected_control:
            raise ValueError("unchanged parent control identity drifted")
        return self


def unchanged_parent_control_sha256(
    *, parent_candidate_id: UUID, parent_sequence: str, refinement_round: int
) -> str:
    return sha256_json(
        {
            "parent_candidate_id": str(parent_candidate_id),
            "parent_sequence": "".join(parent_sequence.split()).upper(),
            "control": "unchanged_parent",
            "refinement_round": refinement_round,
        }
    )
