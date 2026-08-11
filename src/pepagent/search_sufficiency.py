from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, model_validator

from pepagent.multiobjective_portfolio import PortfolioObjective, pareto_depths

if TYPE_CHECKING:
    from pepagent.v33_preregistration import V33Preregistration

SEARCH_SUFFICIENCY_VERSION = "v33-search-sufficiency-v2"


class EpsilonObjective(PortfolioObjective):
    epsilon: float = Field(gt=0)


class ParetoFamily(BaseModel):
    name: str = Field(min_length=1)
    objectives: list[EpsilonObjective] = Field(min_length=1)


class ArchiveSnapshot(BaseModel):
    schema_version: Literal["2.0"] = "2.0"
    method_version: Literal["v33-search-sufficiency-v2"] = SEARCH_SUFFICIENCY_VERSION
    seed: int
    family: str
    checkpoint: int = Field(gt=0)
    previous_checkpoint: int | None
    increment_candidate_count: int = Field(gt=0)
    input_candidate_ids: list[str]
    archive_candidate_ids: list[str]
    epsilon_cells: list[list[int]]
    cumulative_epsilon_cells: list[list[int]]
    added_candidate_ids: list[str]
    removed_candidate_ids: list[str]
    added_candidate_reasons: dict[str, str]
    removed_candidate_dominance_witnesses: dict[str, list[str]]
    added_epsilon_cells: list[list[int]]
    removed_epsilon_cells: list[list[int]]
    new_epsilon_cells: list[list[int]]
    archive_turnover_fraction: float = Field(ge=0, le=1)
    epsilon_cell_turnover_fraction: float = Field(ge=0, le=1)
    new_nondominated_candidate_rate: float = Field(ge=0)
    new_family_local_epsilon_cells_per_candidate: float = Field(ge=0)
    increment_cost_units: float | None = Field(default=None, ge=0)
    cost_per_new_epsilon_cell: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_snapshot(self) -> ArchiveSnapshot:
        expected_increment = (
            self.checkpoint
            if self.previous_checkpoint is None
            else self.checkpoint - self.previous_checkpoint
        )
        if expected_increment != self.increment_candidate_count:
            raise ValueError("archive increment does not match checkpoint interval")
        if self.previous_checkpoint is not None and self.previous_checkpoint >= self.checkpoint:
            raise ValueError("previous checkpoint must precede checkpoint")
        if len(self.input_candidate_ids) != self.checkpoint:
            raise ValueError("archive input identity count does not match checkpoint")
        for name, cells in (
            ("epsilon_cells", self.epsilon_cells),
            ("cumulative_epsilon_cells", self.cumulative_epsilon_cells),
            ("added_epsilon_cells", self.added_epsilon_cells),
            ("removed_epsilon_cells", self.removed_epsilon_cells),
            ("new_epsilon_cells", self.new_epsilon_cells),
        ):
            tuples = [tuple(cell) for cell in cells]
            if tuples != sorted(set(tuples)):
                raise ValueError(f"{name} must be sorted and unique")
        if not set(map(tuple, self.epsilon_cells)).issubset(
            set(map(tuple, self.cumulative_epsilon_cells))
        ):
            raise ValueError("active epsilon cells must be present in cumulative history")
        if not set(map(tuple, self.new_epsilon_cells)).issubset(
            set(map(tuple, self.cumulative_epsilon_cells))
        ):
            raise ValueError("new epsilon cells must be present in cumulative history")
        if self.increment_cost_units is None:
            if self.cost_per_new_epsilon_cell is not None:
                raise ValueError("cost efficiency requires an increment cost observation")
        elif self.new_epsilon_cells:
            expected = self.increment_cost_units / len(self.new_epsilon_cells)
            if self.cost_per_new_epsilon_cell is None or not math.isclose(
                self.cost_per_new_epsilon_cell, expected, rel_tol=1e-12, abs_tol=1e-12
            ):
                raise ValueError("cost per new epsilon cell is inconsistent")
        elif self.cost_per_new_epsilon_cell is not None:
            raise ValueError("cost per new epsilon cell is undefined when no cell is found")
        return self


class CrossSeedAttainmentAssessment(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    family: str
    checkpoint: int = Field(gt=0)
    development_seeds: list[int] = Field(min_length=1)
    confirmation_seeds: list[int] = Field(min_length=1)
    development_strict_majority: int = Field(ge=1)
    confirmation_strict_majority: int = Field(ge=1)
    development_consensus_cells: list[list[int]]
    confirmation_consensus_cells: list[list[int]]
    development_cells_not_attained_by_every_confirmation_seed: list[list[int]]
    confirmation_cells_not_attained_by_every_development_seed: list[list[int]]
    symmetric_recurrence_passed: bool

    @model_validator(mode="after")
    def validate_attainment(self) -> CrossSeedAttainmentAssessment:
        if self.development_strict_majority != len(self.development_seeds) // 2 + 1:
            raise ValueError("development attainment threshold is not a strict majority")
        if self.confirmation_strict_majority != len(self.confirmation_seeds) // 2 + 1:
            raise ValueError("confirmation attainment threshold is not a strict majority")
        expected = bool(
            self.development_consensus_cells
            and self.confirmation_consensus_cells
            and not self.development_cells_not_attained_by_every_confirmation_seed
            and not self.confirmation_cells_not_attained_by_every_development_seed
        )
        if self.symmetric_recurrence_passed != expected:
            raise ValueError("cross-seed recurrence verdict conflicts with its evidence")
        return self


class LeaveOneObjectiveOutAssessment(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    seed: int
    family: str
    checkpoint: int = Field(gt=0)
    omitted_metric: str
    full_archive_candidate_ids: list[str]
    reduced_archive_candidate_ids: list[str]
    selection_jaccard: float = Field(ge=0, le=1)


class SaturationGate(BaseModel):
    assessment_checkpoints: tuple[int, ...] = (150, 200)
    maximum_new_epsilon_cells_per_increment: int = Field(default=1, ge=0)
    maximum_epsilon_cell_turnover_fraction: float = Field(default=0.10, ge=0, le=1)
    require_cross_seed_attainment: bool = True
    require_cost_observations: bool = True
    require_leave_one_objective_out_reporting: bool = True
    model_fragility_warning_jaccard_below: float = Field(default=0.50, ge=0, le=1)


class SaturationAssessment(BaseModel):
    schema_version: Literal["2.0"] = "2.0"
    method_version: Literal["v33-search-sufficiency-v2"] = SEARCH_SUFFICIENCY_VERSION
    verdict: Literal[
        "saturated_within_protocol_and_budget",
        "not_saturated_within_protocol_and_budget",
        "inconclusive_due_to_preregistered_shortfall",
    ]
    claim_scope: Literal[
        "empirical_stability_within_frozen_generator_metrics_seeds_and_budget_not_global_optimality"
    ] = "empirical_stability_within_frozen_generator_metrics_seeds_and_budget_not_global_optimality"
    assessed_seed_count: int
    assessed_family_count: int
    failing_seed_family_checkpoints: list[str]
    missing_seed_family_checkpoints: list[str]
    failed_dimensions: list[str] = Field(default_factory=list)
    cross_seed_attainment_assessments: list[CrossSeedAttainmentAssessment] = Field(
        default_factory=list
    )
    leave_one_objective_out_assessments: list[LeaveOneObjectiveOutAssessment] = Field(
        default_factory=list
    )
    model_fragility_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_verdict_evidence(self) -> SaturationAssessment:
        if self.verdict == "saturated_within_protocol_and_budget" and (
            self.failing_seed_family_checkpoints
            or self.missing_seed_family_checkpoints
            or self.failed_dimensions
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


def _cell_attains(first: tuple[int, ...], second: tuple[int, ...]) -> bool:
    """Return whether a normalized epsilon cell weakly attains another cell."""
    return all(left <= right for left, right in zip(first, second, strict=True))


def _nondominated_cells(cells: set[tuple[int, ...]]) -> list[tuple[int, ...]]:
    return sorted(
        cell
        for cell in cells
        if not any(other != cell and _cell_attains(other, cell) for other in cells)
    )


def build_archive_snapshots(
    *,
    seed: int,
    candidates_in_stream_order: list[dict[str, Any]],
    family: ParetoFamily,
    checkpoints: tuple[int, ...] = (25, 50, 100, 150, 200),
    cumulative_cost_by_checkpoint: Mapping[int, float] | None = None,
) -> list[ArchiveSnapshot]:
    """Build immutable full-budget archive snapshots for one seed and family."""
    if list(checkpoints) != sorted(set(checkpoints)) or checkpoints[0] <= 0:
        raise ValueError("checkpoints must be positive, unique, and increasing")
    identities = [str(candidate["id"]) for candidate in candidates_in_stream_order]
    if len(identities) != len(set(identities)):
        raise ValueError("candidate IDs must be unique within a seed stream")
    if cumulative_cost_by_checkpoint is not None:
        unknown = set(cumulative_cost_by_checkpoint) - set(checkpoints)
        if unknown:
            raise ValueError(f"cost observations reference unknown checkpoints: {sorted(unknown)}")

    snapshots: list[ArchiveSnapshot] = []
    previous_ids: set[str] = set()
    previous_cells: set[tuple[int, ...]] = set()
    seen_cells: set[tuple[int, ...]] = set()
    previous_checkpoint: int | None = None
    previous_cost = 0.0
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
        added_cells = cells - previous_cells
        removed_cells = previous_cells - cells
        novel_cells = cells - seen_cells
        union = current_ids | previous_ids
        cell_union = cells | previous_cells
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
        increment_cost: float | None = None
        if cumulative_cost_by_checkpoint is not None:
            if checkpoint not in cumulative_cost_by_checkpoint:
                raise ValueError(f"missing cumulative cost at checkpoint {checkpoint}")
            cumulative_cost = float(cumulative_cost_by_checkpoint[checkpoint])
            if not math.isfinite(cumulative_cost) or cumulative_cost < previous_cost:
                raise ValueError("cumulative search cost must be finite and nondecreasing")
            increment_cost = cumulative_cost - previous_cost
            previous_cost = cumulative_cost
        cumulative_cells = seen_cells | cells
        snapshots.append(
            ArchiveSnapshot(
                seed=seed,
                family=family.name,
                checkpoint=checkpoint,
                previous_checkpoint=previous_checkpoint,
                increment_candidate_count=increment,
                input_candidate_ids=identities[:checkpoint],
                archive_candidate_ids=archive_ids,
                epsilon_cells=[list(cell) for cell in sorted(cells)],
                cumulative_epsilon_cells=[list(cell) for cell in sorted(cumulative_cells)],
                added_candidate_ids=sorted(added),
                removed_candidate_ids=sorted(removed),
                added_candidate_reasons={
                    candidate_id: "nondominated_at_checkpoint"
                    for candidate_id in sorted(added)
                },
                removed_candidate_dominance_witnesses=removal_witnesses,
                added_epsilon_cells=[list(cell) for cell in sorted(added_cells)],
                removed_epsilon_cells=[list(cell) for cell in sorted(removed_cells)],
                new_epsilon_cells=[list(cell) for cell in sorted(novel_cells)],
                archive_turnover_fraction=(
                    len(added | removed) / len(union) if union else 0.0
                ),
                epsilon_cell_turnover_fraction=(
                    len(added_cells | removed_cells) / len(cell_union) if cell_union else 0.0
                ),
                new_nondominated_candidate_rate=len(added) / increment,
                new_family_local_epsilon_cells_per_candidate=len(novel_cells) / increment,
                increment_cost_units=increment_cost,
                cost_per_new_epsilon_cell=(
                    increment_cost / len(novel_cells)
                    if increment_cost is not None and novel_cells
                    else None
                ),
            )
        )
        previous_ids = current_ids
        previous_cells = cells
        seen_cells = cumulative_cells
        previous_checkpoint = checkpoint
    return snapshots


def build_cross_seed_attainment_assessments(
    snapshots: list[ArchiveSnapshot],
    *,
    development_seeds: set[int],
    confirmation_seeds: set[int],
    required_families: set[str],
    checkpoint: int,
) -> list[CrossSeedAttainmentAssessment]:
    """Compare strict-majority attainment surfaces across independent seed cohorts."""
    if not development_seeds or not confirmation_seeds:
        raise ValueError("cross-seed attainment requires development and confirmation seeds")
    if development_seeds & confirmation_seeds:
        raise ValueError("development and confirmation seed sets must be disjoint")
    indexed = {
        (snapshot.seed, snapshot.family, snapshot.checkpoint): {
            tuple(cell) for cell in snapshot.epsilon_cells
        }
        for snapshot in snapshots
    }

    def consensus(seed_set: set[int], family: str) -> list[tuple[int, ...]]:
        threshold = len(seed_set) // 2 + 1
        union = set().union(*(indexed[(seed, family, checkpoint)] for seed in seed_set))
        supported = {
            cell
            for cell in union
            if sum(
                any(
                    _cell_attains(seed_cell, cell)
                    for seed_cell in indexed[(seed, family, checkpoint)]
                )
                for seed in seed_set
            )
            >= threshold
        }
        return _nondominated_cells(supported)

    results: list[CrossSeedAttainmentAssessment] = []
    for family in sorted(required_families):
        missing = [
            seed
            for seed in sorted(development_seeds | confirmation_seeds)
            if (seed, family, checkpoint) not in indexed
        ]
        if missing:
            raise ValueError(
                f"missing cross-seed checkpoint for family={family}: seeds={missing}"
            )
        development = consensus(development_seeds, family)
        confirmation = consensus(confirmation_seeds, family)
        development_misses = [
            cell
            for cell in development
            if not all(
                any(
                    _cell_attains(seed_cell, cell)
                    for seed_cell in indexed[(seed, family, checkpoint)]
                )
                for seed in confirmation_seeds
            )
        ]
        confirmation_misses = [
            cell
            for cell in confirmation
            if not all(
                any(
                    _cell_attains(seed_cell, cell)
                    for seed_cell in indexed[(seed, family, checkpoint)]
                )
                for seed in development_seeds
            )
        ]
        results.append(
            CrossSeedAttainmentAssessment(
                family=family,
                checkpoint=checkpoint,
                development_seeds=sorted(development_seeds),
                confirmation_seeds=sorted(confirmation_seeds),
                development_strict_majority=len(development_seeds) // 2 + 1,
                confirmation_strict_majority=len(confirmation_seeds) // 2 + 1,
                development_consensus_cells=[list(cell) for cell in development],
                confirmation_consensus_cells=[list(cell) for cell in confirmation],
                development_cells_not_attained_by_every_confirmation_seed=[
                    list(cell) for cell in development_misses
                ],
                confirmation_cells_not_attained_by_every_development_seed=[
                    list(cell) for cell in confirmation_misses
                ],
                symmetric_recurrence_passed=bool(
                    development
                    and confirmation
                    and not development_misses
                    and not confirmation_misses
                ),
            )
        )
    return results


def build_leave_one_objective_out_assessments(
    *,
    seed: int,
    candidates_in_stream_order: list[dict[str, Any]],
    family: ParetoFamily,
    checkpoint: int,
    omitted_metrics: set[str],
) -> list[LeaveOneObjectiveOutAssessment]:
    """Measure portfolio identity dependence without turning it into a scalar winner."""
    if len(candidates_in_stream_order) < checkpoint:
        raise ValueError("leave-one-objective-out assessment lacks the frozen checkpoint")
    candidates = candidates_in_stream_order[:checkpoint]
    full_objectives = [
        PortfolioObjective(
            metric_name=item.metric_name,
            direction=item.direction,
            minimum=item.minimum,
            maximum=item.maximum,
        )
        for item in family.objectives
    ]
    full_depths = pareto_depths(candidates, full_objectives)
    full_ids = {candidate_id for candidate_id, depth in full_depths.items() if depth == 1}
    available = {item.metric_name for item in family.objectives}
    unknown = omitted_metrics - available
    if unknown:
        raise ValueError(f"unknown omitted metrics for {family.name}: {sorted(unknown)}")
    results: list[LeaveOneObjectiveOutAssessment] = []
    for omitted in sorted(omitted_metrics):
        reduced = [item for item in full_objectives if item.metric_name != omitted]
        if not reduced:
            raise ValueError("cannot omit the only objective in a Pareto family")
        reduced_depths = pareto_depths(candidates, reduced)
        reduced_ids = {
            candidate_id for candidate_id, depth in reduced_depths.items() if depth == 1
        }
        union = full_ids | reduced_ids
        results.append(
            LeaveOneObjectiveOutAssessment(
                seed=seed,
                family=family.name,
                checkpoint=checkpoint,
                omitted_metric=omitted,
                full_archive_candidate_ids=sorted(full_ids),
                reduced_archive_candidate_ids=sorted(reduced_ids),
                selection_jaccard=len(full_ids & reduced_ids) / len(union) if union else 1.0,
            )
        )
    return results


def assess_saturation(
    snapshots: list[ArchiveSnapshot],
    *,
    required_seeds: set[int],
    required_families: set[str],
    gate: SaturationGate | None = None,
    development_seeds: set[int] | None = None,
    confirmation_seeds: set[int] | None = None,
    leave_one_objective_out_assessments: list[LeaveOneObjectiveOutAssessment] | None = None,
    required_omitted_metrics_by_family: Mapping[str, set[str]] | None = None,
) -> SaturationAssessment:
    """Apply a conjunctive, fixed-budget empirical-sufficiency evidence contract."""
    gate = gate or SaturationGate()
    indexed = {
        (snapshot.seed, snapshot.family, snapshot.checkpoint): snapshot
        for snapshot in snapshots
    }
    missing: list[str] = []
    failing: list[str] = []
    failed_dimensions: list[str] = []
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
                    or snapshot.epsilon_cell_turnover_fraction
                    > gate.maximum_epsilon_cell_turnover_fraction
                ):
                    failing.append(label)
                if gate.require_cost_observations and snapshot.increment_cost_units is None:
                    missing.append(f"{label};dimension=cost")

    cross_seed: list[CrossSeedAttainmentAssessment] = []
    if gate.require_cross_seed_attainment:
        if development_seeds is None or confirmation_seeds is None:
            missing.append("dimension=cross_seed_attainment;seed_cohorts=missing")
        elif required_seeds != development_seeds | confirmation_seeds:
            missing.append("dimension=cross_seed_attainment;seed_partition=invalid")
        elif not any(
            (seed, family, gate.assessment_checkpoints[-1]) not in indexed
            for seed in required_seeds
            for family in required_families
        ):
            cross_seed = build_cross_seed_attainment_assessments(
                snapshots,
                development_seeds=development_seeds,
                confirmation_seeds=confirmation_seeds,
                required_families=required_families,
                checkpoint=gate.assessment_checkpoints[-1],
            )
            failed_dimensions.extend(
                f"cross_seed_attainment;family={item.family}"
                for item in cross_seed
                if not item.symmetric_recurrence_passed
            )

    loo = leave_one_objective_out_assessments or []
    if gate.require_leave_one_objective_out_reporting:
        if required_omitted_metrics_by_family is None:
            missing.append("dimension=leave_one_objective_out;contract=missing")
        else:
            loo_index = {
                (item.seed, item.family, item.checkpoint, item.omitted_metric)
                for item in loo
            }
            final_checkpoint = gate.assessment_checkpoints[-1]
            for seed in sorted(required_seeds):
                for family, metrics in sorted(required_omitted_metrics_by_family.items()):
                    for metric in sorted(metrics):
                        if (seed, family, final_checkpoint, metric) not in loo_index:
                            missing.append(
                                f"seed={seed};family={family};checkpoint={final_checkpoint};"
                                f"dimension=leave_one_objective_out;metric={metric}"
                            )
    fragility = [
        f"seed={item.seed};family={item.family};metric={item.omitted_metric};"
        f"jaccard={item.selection_jaccard:.6f}"
        for item in loo
        if item.selection_jaccard < gate.model_fragility_warning_jaccard_below
    ]

    if missing:
        verdict = "inconclusive_due_to_preregistered_shortfall"
    elif failing or failed_dimensions:
        verdict = "not_saturated_within_protocol_and_budget"
    else:
        verdict = "saturated_within_protocol_and_budget"
    return SaturationAssessment(
        verdict=verdict,
        assessed_seed_count=len(required_seeds),
        assessed_family_count=len(required_families),
        failing_seed_family_checkpoints=sorted(failing),
        missing_seed_family_checkpoints=sorted(missing),
        failed_dimensions=sorted(failed_dimensions),
        cross_seed_attainment_assessments=cross_seed,
        leave_one_objective_out_assessments=loo,
        model_fragility_warnings=sorted(fragility),
    )
