from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StructureScoreEvidence(FrozenModel):
    candidate_id: UUID
    target_key: str = Field(min_length=1)
    control_lane: Literal["native", "wrong_pocket"]
    boltz_seed: int
    decoy_ordinal: int = Field(ge=0)
    total_score: float

    @model_validator(mode="after")
    def finite_score(self) -> StructureScoreEvidence:
        if not math.isfinite(self.total_score):
            raise ValueError("v38 structure score must be finite")
        return self


class CandidatePortfolioRow(FrozenModel):
    candidate_id: UUID
    sequence_pareto_front: int = Field(ge=1)
    native_median_by_target: dict[str, float]
    wrong_pocket_median_by_target: dict[str, float]
    specificity_margin_by_target: dict[str, float]
    per_target_pareto_front: dict[str, int] = Field(default_factory=dict)
    cross_target_pareto_front: int = Field(ge=1)


class V38FinalPortfolio(FrozenModel):
    schema_version: Literal["v38.final-multiview-portfolio.1"] = (
        "v38.final-multiview-portfolio.1"
    )
    target_keys: tuple[str, ...]
    expected_seeds: tuple[int, ...]
    decoys_per_seed: int = Field(gt=0)
    rows: tuple[CandidatePortfolioRow, ...]
    target_agnostic_front_one_candidate_ids: tuple[UUID, ...]
    per_target_front_one_candidate_ids: dict[str, tuple[UUID, ...]]
    cross_target_front_one_candidate_ids: tuple[UUID, ...]
    weighted_total_used: Literal[False] = False
    structure_used_as_hard_safety_gate: Literal[False] = False


def _dominates(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return all(a <= b for a, b in zip(left, right, strict=True)) and any(
        a < b for a, b in zip(left, right, strict=True)
    )


def _pareto_fronts(values: dict[UUID, tuple[float, ...]]) -> dict[UUID, int]:
    remaining = dict(values)
    fronts: dict[UUID, int] = {}
    front = 1
    while remaining:
        current = sorted(
            candidate_id
            for candidate_id, candidate_values in remaining.items()
            if not any(
                other_id != candidate_id
                and _dominates(other_values, candidate_values)
                for other_id, other_values in remaining.items()
            )
        )
        if not current:
            raise RuntimeError("v38 Pareto peeling produced an empty front")
        for candidate_id in current:
            fronts[candidate_id] = front
            del remaining[candidate_id]
        front += 1
    return fronts


def build_v38_final_portfolio(
    *,
    sequence_pareto_fronts: dict[UUID, int],
    evidence: tuple[StructureScoreEvidence, ...],
    target_keys: tuple[str, ...],
    expected_seeds: tuple[int, ...],
    decoys_per_seed: int,
) -> V38FinalPortfolio:
    if not sequence_pareto_fronts:
        raise ValueError("v38 final portfolio requires admitted candidates")
    if not target_keys or len(target_keys) != len(set(target_keys)):
        raise ValueError("v38 final portfolio target keys must be non-empty and unique")
    if not expected_seeds or len(expected_seeds) != len(set(expected_seeds)):
        raise ValueError("v38 final portfolio seeds must be non-empty and unique")
    candidate_ids = set(sequence_pareto_fronts)
    grouped: dict[tuple[UUID, str, str], list[StructureScoreEvidence]] = defaultdict(list)
    identities: set[tuple[UUID, str, str, int, int]] = set()
    for item in evidence:
        identity = (
            item.candidate_id,
            item.target_key,
            item.control_lane,
            item.boltz_seed,
            item.decoy_ordinal,
        )
        if identity in identities:
            raise ValueError("duplicate v38 structure score evidence")
        identities.add(identity)
        if item.candidate_id not in candidate_ids or item.target_key not in target_keys:
            raise ValueError("v38 structure evidence escaped the admitted target graph")
        grouped[(item.candidate_id, item.target_key, item.control_lane)].append(item)

    expected_ordinals = set(range(decoys_per_seed))
    native: dict[UUID, dict[str, float]] = defaultdict(dict)
    wrong: dict[UUID, dict[str, float]] = defaultdict(dict)
    for candidate_id in sorted(candidate_ids):
        for target_key in target_keys:
            for lane, destination in (("native", native), ("wrong_pocket", wrong)):
                items = grouped.get((candidate_id, target_key, lane), [])
                by_seed: dict[int, set[int]] = defaultdict(set)
                for item in items:
                    by_seed[item.boltz_seed].add(item.decoy_ordinal)
                if set(by_seed) != set(expected_seeds) or any(
                    by_seed[seed] != expected_ordinals for seed in expected_seeds
                ):
                    raise ValueError("incomplete v38 target/control/seed/decoy evidence")
                destination[candidate_id][target_key] = statistics.median(
                    item.total_score for item in items
                )

    per_target_fronts: dict[str, dict[UUID, int]] = {}
    for target_key in target_keys:
        per_target_fronts[target_key] = _pareto_fronts(
            {
                candidate_id: (
                    native[candidate_id][target_key],
                    native[candidate_id][target_key] - wrong[candidate_id][target_key],
                )
                for candidate_id in candidate_ids
            }
        )
    cross_target_fronts = _pareto_fronts(
        {
            candidate_id: tuple(
                value
                for target_key in target_keys
                for value in (
                    native[candidate_id][target_key],
                    native[candidate_id][target_key] - wrong[candidate_id][target_key],
                )
            )
            for candidate_id in candidate_ids
        }
    )
    rows = tuple(
        CandidatePortfolioRow(
            candidate_id=candidate_id,
            sequence_pareto_front=sequence_pareto_fronts[candidate_id],
            native_median_by_target=native[candidate_id],
            wrong_pocket_median_by_target=wrong[candidate_id],
            specificity_margin_by_target={
                key: native[candidate_id][key] - wrong[candidate_id][key]
                for key in target_keys
            },
            per_target_pareto_front={
                key: per_target_fronts[key][candidate_id] for key in target_keys
            },
            cross_target_pareto_front=cross_target_fronts[candidate_id],
        )
        for candidate_id in sorted(candidate_ids)
    )
    return V38FinalPortfolio(
        target_keys=target_keys,
        expected_seeds=expected_seeds,
        decoys_per_seed=decoys_per_seed,
        rows=rows,
        target_agnostic_front_one_candidate_ids=tuple(
            sorted(item for item, front in sequence_pareto_fronts.items() if front == 1)
        ),
        per_target_front_one_candidate_ids={
            key: tuple(sorted(item for item, front in fronts.items() if front == 1))
            for key, fronts in per_target_fronts.items()
        },
        cross_target_front_one_candidate_ids=tuple(
            sorted(item for item, front in cross_target_fronts.items() if front == 1)
        ),
    )
