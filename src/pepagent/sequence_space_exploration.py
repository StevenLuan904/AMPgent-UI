from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pepagent.provenance.hashing import sha256_json


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExplorationCell(FrozenModel):
    round_ordinal: int = Field(ge=0)
    cell_ordinal: int = Field(ge=0)
    generator_id: Literal["hydramp", "ampgan_v2", "amp_designer"]
    seed: int
    requested_occurrences: int = Field(gt=0)


class SequenceSpaceExplorationContract(FrozenModel):
    schema_version: Literal["ampgent.sequence-space-exploration.1"] = (
        "ampgent.sequence-space-exploration.1"
    )
    policy_version: Literal["v39.0.0"] = "v39.0.0"
    cells: tuple[ExplorationCell, ...]
    maximum_rounds: int = Field(ge=1)
    expected_maximum_raw_occurrences: int = Field(gt=0)
    persist_every_raw_occurrence: Literal[True] = True
    score_all_valid_unique_sequences: Literal[True] = True
    exclude_historical_exact_duplicates_from_new_unique_pool: Literal[True] = True
    preserve_historical_duplicates_as_occurrence_evidence: Literal[True] = True
    plateau_window_batches: Literal[2] = 2
    pareto_does_not_stop_exploration: Literal[True] = True

    @model_validator(mode="after")
    def validate_cells(self) -> SequenceSpaceExplorationContract:
        identities = [(cell.generator_id, cell.seed) for cell in self.cells]
        if len(identities) != len(set(identities)):
            raise ValueError("generator and seed identities must be globally unique")
        for round_ordinal in range(self.maximum_rounds):
            round_cells = [
                cell for cell in self.cells if cell.round_ordinal == round_ordinal
            ]
            if [cell.cell_ordinal for cell in round_cells] != list(range(len(round_cells))):
                raise ValueError("cell ordinals must be contiguous within every round")
            if {cell.generator_id for cell in round_cells} != {
                "hydramp",
                "ampgan_v2",
                "amp_designer",
            }:
                raise ValueError("every exploration round must cover all generators")
        budget = sum(cell.requested_occurrences for cell in self.cells)
        if budget != self.expected_maximum_raw_occurrences:
            raise ValueError("exploration occurrence budget differs from cells")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class ExplorationBatchObservation(FrozenModel):
    batch_ordinal: int = Field(ge=0)
    raw_occurrences: int = Field(ge=0)
    valid_unique_sequences: int = Field(ge=0)
    historically_novel_sequences: int = Field(ge=0)
    sequence_family_count: int = Field(ge=0)
    safety_admissible_sequences: int = Field(ge=0)
    activity_supported_sequences: int = Field(ge=0)
    new_pareto_extensions: int = Field(ge=0)


ControllerAction = Literal[
    "continue_diverse_generation",
    "switch_generators_seeds_and_underexplored_families",
    "launch_frontier_refinement_with_parent_controls",
    "freeze_successor_exploration_contract",
]


def next_exploration_action(
    observations: tuple[ExplorationBatchObservation, ...],
    *,
    maximum_batches: int,
) -> ControllerAction:
    """Choose a forward action from durable yield, never from a weighted score."""

    if not observations:
        return "continue_diverse_generation"
    if len(observations) >= maximum_batches:
        return "freeze_successor_exploration_contract"
    recent = observations[-2:]
    if len(recent) == 2 and all(
        item.historically_novel_sequences == 0 for item in recent
    ):
        return "switch_generators_seeds_and_underexplored_families"
    if len(recent) == 2 and all(item.new_pareto_extensions == 0 for item in recent):
        return "launch_frontier_refinement_with_parent_controls"
    return "continue_diverse_generation"


def build_default_v39_exploration_contract() -> SequenceSpaceExplorationContract:
    generators = ("hydramp", "ampgan_v2", "amp_designer")
    cells: list[ExplorationCell] = []
    # Four independently evidenced rounds, twice the cell breadth of v38 per round.
    for round_ordinal in range(4):
        for cell_ordinal in range(18):
            global_ordinal = round_ordinal * 18 + cell_ordinal
            cells.append(
                ExplorationCell(
                    round_ordinal=round_ordinal,
                    cell_ordinal=cell_ordinal,
                    generator_id=generators[cell_ordinal // 6],
                    seed=20270801 + global_ordinal,
                    requested_occurrences=100,
                )
            )
    return SequenceSpaceExplorationContract(
        cells=tuple(cells),
        maximum_rounds=4,
        expected_maximum_raw_occurrences=7200,
    )
