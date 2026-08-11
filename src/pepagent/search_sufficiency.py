from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, model_validator

from pepagent.multiobjective_portfolio import PortfolioObjective, pareto_depths

if TYPE_CHECKING:
    from pepagent.v33_preregistration import V33Preregistration

SEARCH_SUFFICIENCY_VERSION = "v33-search-sufficiency-v1"


class EpsilonObjective(PortfolioObjective):
    epsilon: float = Field(gt=0)


class ParetoFamily(BaseModel):
    name: str = Field(min_length=1)
    objectives: list[EpsilonObjective] = Field(min_length=1)


class ArchiveSnapshot(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    method_version: Literal["v33-search-sufficiency-v1"] = SEARCH_SUFFICIENCY_VERSION
    seed: int
    family: str
    checkpoint: int
    previous_checkpoint: int | None
    input_candidate_ids: list[str]
    archive_candidate_ids: list[str]
    epsilon_cells: list[list[int]]
    added_candidate_ids: list[str]
    removed_candidate_ids: list[str]
    added_candidate_reasons: dict[str, str]
    removed_candidate_dominance_witnesses: dict[str, list[str]]
    new_epsilon_cells: list[list[int]]
    archive_turnover_fraction: float
    new_nondominated_candidate_rate: float
    new_family_local_epsilon_cells_per_candidate: float


class SaturationGate(BaseModel):
    assessment_checkpoints: tuple[int, ...] = (150, 200)
    maximum_new_epsilon_cells_per_increment: int = Field(default=1, ge=0)
    maximum_archive_turnover_fraction: float = Field(default=0.10, ge=0, le=1)


class SaturationAssessment(BaseModel):
    verdict: Literal[
        "saturated_within_protocol_and_budget",
        "not_saturated_within_protocol_and_budget",
        "inconclusive_due_to_preregistered_shortfall",
    ]
    assessed_seed_count: int
    assessed_family_count: int
    failing_seed_family_checkpoints: list[str]
    missing_seed_family_checkpoints: list[str]

    @model_validator(mode="after")
    def validate_verdict_evidence(self) -> SaturationAssessment:
        if self.verdict == "saturated_within_protocol_and_budget" and (
            self.failing_seed_family_checkpoints
            or self.missing_seed_family_checkpoints
        ):
            raise ValueError("saturation verdict conflicts with checkpoint evidence")
        return self


def pareto_families_from_preregistration(
    manifest: V33Preregistration,
) -> dict[str, ParetoFamily]:
    """Bind archive analysis to the validated family-local v33 objectives."""
    return {
        name: ParetoFamily(
            name=name,
            objectives=[
                EpsilonObjective.model_validate(
                    {
                        "metric_name": item["metric"],
                        **{key: value for key, value in item.items() if key != "metric"},
                    }
                )
                for item in objectives
            ],
        )
        for name, objectives in manifest.pareto_families.items()
    }


def _objective_distance(value: float, objective: EpsilonObjective) -> float:
    if not math.isfinite(value):
        raise ValueError(f"non-finite metric {objective.metric_name}")
    if objective.direction == "maximize":
        return -value
    if objective.direction == "minimize":
        return value
    assert objective.minimum is not None and objective.maximum is not None
    if value < objective.minimum:
        return objective.minimum - value
    if value > objective.maximum:
        return value - objective.maximum
    return 0.0


def epsilon_cell(candidate: dict[str, Any], family: ParetoFamily) -> tuple[int, ...]:
    """Return a family-local, direction-normalized epsilon cell."""
    cells: list[int] = []
    for objective in family.objectives:
        try:
            value = float(candidate["metrics"][objective.metric_name])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"missing metric {objective.metric_name} for {candidate.get('id')}"
            ) from error
        cells.append(math.floor(_objective_distance(value, objective) / objective.epsilon))
    return tuple(cells)


def _family_vector(candidate: dict[str, Any], family: ParetoFamily) -> tuple[float, ...]:
    return tuple(
        _objective_distance(float(candidate["metrics"][objective.metric_name]), objective)
        for objective in family.objectives
    )


def _dominates(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
    return all(left <= right for left, right in zip(first, second, strict=True)) and any(
        left < right for left, right in zip(first, second, strict=True)
    )


def build_archive_snapshots(
    *,
    seed: int,
    candidates_in_stream_order: list[dict[str, Any]],
    family: ParetoFamily,
    checkpoints: tuple[int, ...] = (25, 50, 100, 150, 200),
) -> list[ArchiveSnapshot]:
    """Build immutable full-budget archive snapshots for one seed and family."""
    if list(checkpoints) != sorted(set(checkpoints)) or checkpoints[0] <= 0:
        raise ValueError("checkpoints must be positive, unique, and increasing")
    identities = [str(candidate["id"]) for candidate in candidates_in_stream_order]
    if len(identities) != len(set(identities)):
        raise ValueError("candidate IDs must be unique within a seed stream")

    snapshots: list[ArchiveSnapshot] = []
    previous_ids: set[str] = set()
    seen_cells: set[tuple[int, ...]] = set()
    previous_checkpoint: int | None = None
    for checkpoint in checkpoints:
        if len(candidates_in_stream_order) < checkpoint:
            break
        candidates = candidates_in_stream_order[:checkpoint]
        objectives = [
            PortfolioObjective(
                metric_name=objective.metric_name,
                direction=objective.direction,
                minimum=objective.minimum,
                maximum=objective.maximum,
            )
            for objective in family.objectives
        ]
        depths = pareto_depths(candidates, objectives)
        archive_ids = sorted(
            candidate_id for candidate_id, depth in depths.items() if depth == 1
        )
        by_id = {str(candidate["id"]): candidate for candidate in candidates}
        cells = {epsilon_cell(by_id[candidate_id], family) for candidate_id in archive_ids}
        current_ids = set(archive_ids)
        added = current_ids - previous_ids
        removed = previous_ids - current_ids
        novel_cells = cells - seen_cells
        union = current_ids | previous_ids
        vectors = {
            candidate_id: _family_vector(candidate, family)
            for candidate_id, candidate in by_id.items()
        }
        removal_witnesses = {
            candidate_id: sorted(
                front_id
                for front_id in current_ids
                if _dominates(vectors[front_id], vectors[candidate_id])
            )
            for candidate_id in sorted(removed)
        }
        increment = checkpoint if previous_checkpoint is None else checkpoint - previous_checkpoint
        snapshots.append(
            ArchiveSnapshot(
                seed=seed,
                family=family.name,
                checkpoint=checkpoint,
                previous_checkpoint=previous_checkpoint,
                input_candidate_ids=identities[:checkpoint],
                archive_candidate_ids=archive_ids,
                epsilon_cells=[list(cell) for cell in sorted(cells)],
                added_candidate_ids=sorted(added),
                removed_candidate_ids=sorted(removed),
                added_candidate_reasons={
                    candidate_id: "nondominated_at_checkpoint"
                    for candidate_id in sorted(added)
                },
                removed_candidate_dominance_witnesses=removal_witnesses,
                new_epsilon_cells=[list(cell) for cell in sorted(novel_cells)],
                archive_turnover_fraction=(
                    len(added | removed) / len(union) if union else 0.0
                ),
                new_nondominated_candidate_rate=len(added) / increment,
                new_family_local_epsilon_cells_per_candidate=len(novel_cells) / increment,
            )
        )
        previous_ids = current_ids
        seen_cells |= cells
        previous_checkpoint = checkpoint
    return snapshots


def assess_saturation(
    snapshots: list[ArchiveSnapshot],
    *,
    required_seeds: set[int],
    required_families: set[str],
    gate: SaturationGate | None = None,
) -> SaturationAssessment:
    """Apply the preregistered all-seed/all-family saturation conjunction."""
    gate = gate or SaturationGate()
    indexed = {
        (snapshot.seed, snapshot.family, snapshot.checkpoint): snapshot
        for snapshot in snapshots
    }
    missing: list[str] = []
    failing: list[str] = []
    for seed in sorted(required_seeds):
        for family in sorted(required_families):
            for checkpoint in gate.assessment_checkpoints:
                label = f"seed={seed};family={family};checkpoint={checkpoint}"
                snapshot = indexed.get((seed, family, checkpoint))
                if snapshot is None:
                    missing.append(label)
                    continue
                if (
                    len(snapshot.new_epsilon_cells)
                    > gate.maximum_new_epsilon_cells_per_increment
                    or snapshot.archive_turnover_fraction
                    > gate.maximum_archive_turnover_fraction
                ):
                    failing.append(label)
    if missing:
        verdict = "inconclusive_due_to_preregistered_shortfall"
    elif failing:
        verdict = "not_saturated_within_protocol_and_budget"
    else:
        verdict = "saturated_within_protocol_and_budget"
    return SaturationAssessment(
        verdict=verdict,
        assessed_seed_count=len(required_seeds),
        assessed_family_count=len(required_families),
        failing_seed_family_checkpoints=failing,
        missing_seed_family_checkpoints=missing,
    )
