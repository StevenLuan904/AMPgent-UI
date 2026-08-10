from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def sequence_sha256(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()


class PortfolioObjective(BaseModel):
    metric_name: str = Field(min_length=1)
    direction: Literal["minimize", "maximize", "target_interval"]
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> PortfolioObjective:
        if self.direction == "target_interval":
            if self.minimum is None or self.maximum is None:
                raise ValueError("target_interval requires minimum and maximum")
            if self.minimum > self.maximum:
                raise ValueError("target_interval minimum exceeds maximum")
        elif self.minimum is not None or self.maximum is not None:
            raise ValueError("only target_interval objectives accept bounds")
        return self


class PortfolioLane(BaseModel):
    name: Literal["membrane", "activity_mic", "risk_control", "balanced"]
    quota: int = Field(ge=1)
    objectives: list[PortfolioObjective] = Field(default_factory=list)


class MultiobjectivePortfolioManifest(BaseModel):
    benchmark_id: Literal["amp_multiobjective_portfolio_v32"]
    version: Literal["v32.0.0"]
    execution_status: Literal[
        "preregistered", "implementation_complete", "running", "completed", "failed"
    ]
    generator_id: Literal["amp_designer"]
    seeds: list[int] = Field(min_length=3, max_length=3)
    raw_proposal_budget_per_seed: Literal[1000]
    evaluated_valid_unique_per_seed: Literal[100]
    minimum_length: Literal[10]
    maximum_length: Literal[25]
    selection_rule: Literal["raw_order_first_k_valid_unique"]
    global_sequence_uniqueness: Literal[True]
    missing_policy: Literal["retain_shortfall_no_refill"]
    charge_policy: Literal["observe_only_defer_optimization_to_v33"]
    risk_guard: dict[str, Any]
    maximum_sequence_similarity: float = Field(gt=0, le=1)
    maximum_per_seed_per_lane: int = Field(ge=1)
    lanes: list[PortfolioLane] = Field(min_length=4, max_length=4)
    required_metric_names: list[str] = Field(min_length=9)
    scientific_contract: dict[str, bool]

    @model_validator(mode="after")
    def validate_contract(self) -> MultiobjectivePortfolioManifest:
        if len(self.seeds) != len(set(self.seeds)):
            raise ValueError("v32 seeds must be unique")
        lane_names = [lane.name for lane in self.lanes]
        if lane_names != ["membrane", "activity_mic", "risk_control", "balanced"]:
            raise ValueError("v32 portfolio lane order is frozen")
        if any(lane.name != "balanced" and not lane.objectives for lane in self.lanes):
            raise ValueError("non-balanced lanes require objectives")
        forbidden_charge_objectives = {
            "net_charge_ph7_4",
            "cationic_residue_fraction",
            "positive_residue_count",
        }
        optimized = {
            objective.metric_name
            for lane in self.lanes
            for objective in lane.objectives
        }
        if optimized & forbidden_charge_objectives:
            raise ValueError("v32 must not optimize positive charge")
        if not self.scientific_contract.get("all_agent_evidence_persisted"):
            raise ValueError("v32 requires all Agent evidence to be persisted")
        if not self.scientific_contract.get("full_replay_required"):
            raise ValueError("v32 requires a complete replay bundle")
        if not self.scientific_contract.get("no_weighted_total_score"):
            raise ValueError("v32 forbids weighted total scores")
        return self


def _objective_value(candidate: dict[str, Any], objective: PortfolioObjective) -> float:
    raw = candidate["metrics"].get(objective.metric_name)
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(
            f"non-finite or missing metric {objective.metric_name} for {candidate['id']}"
        )
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


def _dominates(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
    return all(a <= b for a, b in zip(first, second, strict=True)) and any(
        a < b for a, b in zip(first, second, strict=True)
    )


def pareto_depths(
    candidates: list[dict[str, Any]], objectives: list[PortfolioObjective]
) -> dict[str, int]:
    remaining = {candidate["id"]: candidate for candidate in candidates}
    values = {
        candidate["id"]: tuple(
            _objective_value(candidate, objective) for objective in objectives
        )
        for candidate in candidates
    }
    depths: dict[str, int] = {}
    depth = 1
    while remaining:
        front = [
            candidate_id
            for candidate_id in remaining
            if not any(
                other_id != candidate_id
                and _dominates(values[other_id], values[candidate_id])
                for other_id in remaining
            )
        ]
        if not front:
            raise RuntimeError("Pareto peeling produced an empty front")
        for candidate_id in front:
            depths[candidate_id] = depth
            del remaining[candidate_id]
        depth += 1
    return depths


def normalized_levenshtein_similarity(first: str, second: str) -> float:
    if first == second:
        return 1.0
    previous = list(range(len(second) + 1))
    for row, left in enumerate(first, start=1):
        current = [row]
        for column, right in enumerate(second, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left != right),
                )
            )
        previous = current
    distance = previous[-1]
    return 1.0 - distance / max(len(first), len(second))


def _risk_guard(candidate: dict[str, Any], manifest: MultiobjectivePortfolioManifest) -> bool:
    policy = manifest.risk_guard
    labels = candidate.get("labels", {})
    return not (
        labels.get(policy["toxicity_label_metric"]) == policy["toxicity_red_label"]
        and labels.get(policy["hemolysis_label_metric"]) == policy["hemolysis_red_label"]
    )


def _diverse_take(
    ordered: list[dict[str, Any]],
    quota: int,
    maximum_similarity: float,
    maximum_per_seed: int,
    already_selected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    already_selected_ids = {candidate["id"] for candidate in already_selected}
    seed_counts: dict[int, int] = defaultdict(int)
    for candidate in ordered:
        if candidate["id"] in already_selected_ids:
            continue
        seed = int(candidate["seed"])
        if seed_counts[seed] >= maximum_per_seed:
            continue
        if any(
            normalized_levenshtein_similarity(candidate["sequence"], item["sequence"])
            > maximum_similarity
            for item in [*already_selected, *selected]
        ):
            continue
        selected.append(candidate)
        seed_counts[seed] += 1
        if len(selected) == quota:
            break
    return selected


def build_portfolio(
    candidates: list[dict[str, Any]], manifest: MultiobjectivePortfolioManifest
) -> dict[str, Any]:
    if not candidates:
        raise ValueError("v32 portfolio requires candidates")
    identities = [candidate["id"] for candidate in candidates]
    sequences = [candidate["sequence"] for candidate in candidates]
    if len(identities) != len(set(identities)):
        raise ValueError("candidate IDs must be unique")
    if len(sequences) != len(set(sequences)):
        raise ValueError("candidate sequences must be globally unique")
    for candidate in candidates:
        if sequence_sha256(candidate["sequence"]) != candidate["sequence_sha256"]:
            raise ValueError(f"sequence SHA mismatch for {candidate['id']}")
        missing = set(manifest.required_metric_names) - set(candidate["metrics"])
        if missing:
            raise ValueError(f"missing metrics for {candidate['id']}: {sorted(missing)}")

    eligible = [candidate for candidate in candidates if _risk_guard(candidate, manifest)]
    excluded = [candidate for candidate in candidates if candidate not in eligible]
    family_lanes = {lane.name: lane for lane in manifest.lanes if lane.name != "balanced"}
    family_depths = {
        name: pareto_depths(eligible, lane.objectives)
        for name, lane in family_lanes.items()
    }
    selected_candidates: list[dict[str, Any]] = []
    lane_results: list[dict[str, Any]] = []

    for lane in manifest.lanes:
        if lane.name == "balanced":
            ordered = sorted(
                eligible,
                key=lambda candidate: (
                    max(depths[candidate["id"]] for depths in family_depths.values()),
                    -sum(
                        depths[candidate["id"]] == 1
                        for depths in family_depths.values()
                    ),
                    tuple(
                        family_depths[name][candidate["id"]]
                        for name in ("membrane", "activity_mic", "risk_control")
                    ),
                    candidate["id"],
                ),
            )
        else:
            depths = family_depths[lane.name]
            ordered = sorted(
                eligible,
                key=lambda candidate: (depths[candidate["id"]], candidate["id"]),
            )
        chosen = _diverse_take(
            ordered,
            lane.quota,
            manifest.maximum_sequence_similarity,
            manifest.maximum_per_seed_per_lane,
            selected_candidates,
        )
        for rank, candidate in enumerate(chosen, start=1):
            candidate_id = candidate["id"]
            selected_candidates.append(candidate)
            lane_results.append(
                {
                    "candidate_id": candidate_id,
                    "sequence": candidate["sequence"],
                    "sequence_sha256": candidate["sequence_sha256"],
                    "seed": candidate["seed"],
                    "lane": lane.name,
                    "lane_rank": rank,
                    "family_depths": {
                        name: depths[candidate_id]
                        for name, depths in family_depths.items()
                    },
                    "metrics": candidate["metrics"],
                    "labels": candidate.get("labels", {}),
                    "claim_scope": "computational_multiobjective_hypothesis_only",
                }
            )

    return {
        "schema_version": "1.0",
        "policy": "evidence-governed-pareto-portfolio-v1",
        "input_count": len(candidates),
        "eligible_count": len(eligible),
        "concordant_risk_red_count": len(excluded),
        "selected_count": len(lane_results),
        "lane_results": lane_results,
        "excluded_risk_red_candidate_ids": [candidate["id"] for candidate in excluded],
        "selection_complete": all(
            sum(item["lane"] == lane.name for item in lane_results) == lane.quota
            for lane in manifest.lanes
        ),
        "weighted_total_score_used": False,
        "charge_optimized": False,
    }
