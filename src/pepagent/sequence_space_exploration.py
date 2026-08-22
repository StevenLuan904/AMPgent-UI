from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pepagent.provenance.hashing import sha256_json
from pepagent.v38_science_execution import (
    GeneratorCell,
    V38SequenceExecutionContract,
    build_default_v38_sequence_contract,
)


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


class V39ExplorationRoundBinding(FrozenModel):
    """Bind one independently reserved science run to one frozen exploration round."""

    schema_version: Literal["ampgent.sequence-space-exploration-round.1"] = (
        "ampgent.sequence-space-exploration-round.1"
    )
    policy_version: Literal["v39.0.0"] = "v39.0.0"
    exploration_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    round_ordinal: int = Field(ge=0)
    maximum_rounds: int = Field(ge=1)
    expected_raw_occurrences: int = Field(gt=0)
    execution_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    independent_frozen_run_identity_required: Literal[True] = True
    defer_structure_until_exploration_complete: Literal[True] = True

    @model_validator(mode="after")
    def validate_round(self) -> V39ExplorationRoundBinding:
        if self.round_ordinal >= self.maximum_rounds:
            raise ValueError("exploration round ordinal exceeds maximum rounds")
        return self


class V39ExplorationRoundRequest(FrozenModel):
    """One pre-reserved, independently frozen child run in the outer schedule."""

    run_id: UUID
    workflow_id: str = Field(min_length=1)
    request: dict[str, Any]

    @model_validator(mode="after")
    def validate_request_binding(self) -> V39ExplorationRoundRequest:
        if str(self.request.get("run_id")) != str(self.run_id):
            raise ValueError("v39 round request run identity drifted")
        V39ExplorationRoundBinding.model_validate(self.request.get("exploration_round"))
        return self


class V39ExplorationSchedule(FrozenModel):
    """Frozen parent schedule; no child identity may be allocated during replay."""

    schema_version: Literal["ampgent.sequence-space-exploration-schedule.1"] = (
        "ampgent.sequence-space-exploration-schedule.1"
    )
    controller_run_id: UUID
    exploration_contract: SequenceSpaceExplorationContract
    rounds: tuple[V39ExplorationRoundRequest, ...]

    @model_validator(mode="after")
    def validate_rounds(self) -> V39ExplorationSchedule:
        if len(self.rounds) != self.exploration_contract.maximum_rounds:
            raise ValueError("v39 schedule must pre-freeze every exploration round")
        run_ids = [item.run_id for item in self.rounds]
        workflow_ids = [item.workflow_id for item in self.rounds]
        if len(run_ids) != len(set(run_ids)) or len(workflow_ids) != len(
            set(workflow_ids)
        ):
            raise ValueError("v39 child run and workflow identities must be unique")
        bindings = tuple(
            V39ExplorationRoundBinding.model_validate(
                item.request["exploration_round"]
            )
            for item in self.rounds
        )
        if [item.round_ordinal for item in bindings] != list(
            range(self.exploration_contract.maximum_rounds)
        ):
            raise ValueError("v39 rounds must be frozen in contiguous order")
        expected_sha = self.exploration_contract.sha256()
        if any(item.exploration_contract_sha256 != expected_sha for item in bindings):
            raise ValueError("v39 round is bound to another exploration contract")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


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


def build_v39_round_execution_contract(
    contract: SequenceSpaceExplorationContract,
    *,
    round_ordinal: int,
) -> tuple[V39ExplorationRoundBinding, V38SequenceExecutionContract]:
    """Project one v39 round onto the tested score-all execution contract.

    The projection changes only generator breadth and seeds. Metric identities and score-all
    semantics remain inherited from the executable sequence contract. The returned binding is
    included in the Temporal request so an 18-cell run cannot be mistaken for historical v38.
    """

    if round_ordinal < 0 or round_ordinal >= contract.maximum_rounds:
        raise ValueError("exploration round ordinal is outside the frozen contract")
    round_cells = tuple(
        cell for cell in contract.cells if cell.round_ordinal == round_ordinal
    )
    if len(round_cells) != 18:
        raise ValueError("v39 exploration round requires exactly eighteen generator cells")
    base = build_default_v38_sequence_contract()
    execution = V38SequenceExecutionContract(
        cells=tuple(
            GeneratorCell(
                ordinal=cell.cell_ordinal,
                generator_id=cell.generator_id,
                seed=cell.seed,
                requested_proposals=cell.requested_occurrences,
            )
            for cell in round_cells
        ),
        expected_raw_occurrences=sum(
            cell.requested_occurrences for cell in round_cells
        ),
        metric_plugins=base.metric_plugins,
        required_sequence_metrics=base.required_sequence_metrics,
    )
    binding = V39ExplorationRoundBinding(
        exploration_contract_sha256=contract.sha256(),
        round_ordinal=round_ordinal,
        maximum_rounds=contract.maximum_rounds,
        expected_raw_occurrences=execution.expected_raw_occurrences,
        execution_contract_sha256=execution.sha256(),
    )
    return binding, execution
