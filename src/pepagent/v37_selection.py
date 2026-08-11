from __future__ import annotations

from collections import defaultdict
from typing import Any

from pepagent.multiobjective_portfolio import (
    PortfolioObjective,
    normalized_levenshtein_similarity,
    pareto_depths,
)
from pepagent.provenance.hashing import sha256_text

TOXICITY_RED_LABEL = "Toxin"
HEMOLYSIS_RED_LABEL = "high"


def _objectives(rows: list[dict[str, Any]]) -> list[PortfolioObjective]:
    return [PortfolioObjective.model_validate(item) for item in rows]


def _stable_source_ordinal(item: dict[str, Any]) -> tuple[str, int, int]:
    """Return a source-stable ordinal that never depends on a candidate UUID."""
    value = item.get("source_ordinal", item.get("raw_rank", 0))
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("v37 source ordinal must be a non-negative integer")
    return (str(item["generator_id"]), int(item["seed"]), value)


def _stable_tie_key(item: dict[str, Any]) -> tuple[str, tuple[str, int, int]]:
    observed_sha = str(item.get("sequence_sha256") or sha256_text(item["sequence"]))
    expected_sha = sha256_text(item["sequence"])
    if observed_sha != expected_sha:
        raise ValueError("v37 candidate sequence SHA does not match its sequence")
    return (observed_sha, _stable_source_ordinal(item))


def _concordant_red(item: dict[str, Any]) -> bool:
    labels = item.get("labels", {})
    return (
        labels.get("toxinpred3_label") == TOXICITY_RED_LABEL
        and labels.get("macrel_hemolysis_label") == HEMOLYSIS_RED_LABEL
    )


def select_v37_lanes(
    candidates: list[dict[str, Any]],
    *,
    lanes: list[dict[str, Any]],
    family_objectives: dict[str, list[dict[str, Any]]],
    maximum_similarity: float,
    maximum_per_generator: int,
    maximum_per_generator_seed: int,
) -> dict[str, Any]:
    """Deterministic lane-local Pareto+maximin selection without scalarization."""
    if not candidates:
        raise ValueError("v37 selection requires candidates")
    ids = [item["id"] for item in candidates]
    sequences = [item["sequence"] for item in candidates]
    if len(ids) != len(set(ids)) or len(sequences) != len(set(sequences)):
        raise ValueError("v37 selection identities and sequences must be unique")
    for item in candidates:
        _stable_tie_key(item)
    excluded = sorted(
        (item for item in candidates if _concordant_red(item)),
        key=_stable_tie_key,
    )
    excluded_ids = {item["id"] for item in excluded}
    globally_eligible = [item for item in candidates if item["id"] not in excluded_ids]
    selected: list[dict[str, Any]] = []
    lane_results = []
    witnesses: dict[str, Any] = {}
    for lane in lanes:
        families = lane["objective_families"]
        objectives = _objectives(
            [item for family in families for item in family_objectives[family]]
        )
        eligible = globally_eligible
        for name, expected in lane.get("required_soft_labels", {}).items():
            eligible = [item for item in eligible if item["labels"].get(name) == expected]
        depths = pareto_depths(eligible, objectives) if eligible else {}
        remaining = sorted(
            eligible,
            key=lambda item: (depths[item["id"]], _stable_tie_key(item)),
        )
        chosen: list[dict[str, Any]] = []
        generator_counts: dict[str, int] = defaultdict(int)
        cell_counts: dict[tuple[str, int], int] = defaultdict(int)
        while remaining and len(chosen) < int(lane["quota"]):
            allowed = []
            for item in remaining:
                generator = str(item["generator_id"])
                cell = (generator, int(item["seed"]))
                if generator_counts[generator] >= maximum_per_generator:
                    continue
                if cell_counts[cell] >= maximum_per_generator_seed:
                    continue
                if any(
                    normalized_levenshtein_similarity(item["sequence"], other["sequence"])
                    > maximum_similarity
                    for other in [*selected, *chosen]
                ):
                    continue
                allowed.append(item)
            if not allowed:
                break
            best_depth = min(depths[item["id"]] for item in allowed)
            front = [item for item in allowed if depths[item["id"]] == best_depth]
            if selected or chosen:
                anchor = [*selected, *chosen]
                pick = min(
                    front,
                    key=lambda item: (
                        -min(
                            1.0
                            - normalized_levenshtein_similarity(
                                item["sequence"], other["sequence"]
                            )
                            for other in anchor
                        ),
                        _stable_tie_key(item),
                    ),
                )
            else:
                pick = min(front, key=_stable_tie_key)
            chosen.append(pick)
            generator = str(pick["generator_id"])
            generator_counts[generator] += 1
            cell_counts[(generator, int(pick["seed"]))] += 1
            remaining = [item for item in remaining if item["id"] != pick["id"]]
        selected.extend(chosen)
        lane_results.extend(
            {
                "lane": lane["name"],
                "rank": rank,
                "candidate_id": item["id"],
                "pareto_depth": depths[item["id"]],
            }
            for rank, item in enumerate(chosen, start=1)
        )
        witnesses[lane["name"]] = {
            "eligible_count": len(eligible),
            "selected_count": len(chosen),
            "shortfall": int(lane["quota"]) - len(chosen),
            "pareto_depths": depths,
        }
    return {
        "selected_ids": [item["id"] for item in selected],
        "excluded_ids": [item["id"] for item in excluded],
        "risk_guard_witness": {
            "policy": "exclude_only_concordant_red_flags_no_refill",
            "toxicity_red": {
                "metric_name": "toxinpred3_label",
                "label": TOXICITY_RED_LABEL,
            },
            "hemolysis_red": {
                "metric_name": "macrel_hemolysis_label",
                "label": HEMOLYSIS_RED_LABEL,
            },
            "input_count": len(candidates),
            "excluded_count": len(excluded),
            "eligible_count": len(globally_eligible),
            "excluded": [
                {
                    "candidate_id": item["id"],
                    "sequence_sha256": _stable_tie_key(item)[0],
                    "source_ordinal": list(_stable_source_ordinal(item)),
                    "toxicity_label": item["labels"]["toxinpred3_label"],
                    "hemolysis_label": item["labels"]["macrel_hemolysis_label"],
                }
                for item in excluded
            ],
            "single_model_warning_remains_eligible": True,
            "no_refill": True,
        },
        "lane_results": lane_results,
        "witnesses": witnesses,
        "selection_complete": all(item["shortfall"] == 0 for item in witnesses.values()),
        "weighted_total_used": False,
    }
