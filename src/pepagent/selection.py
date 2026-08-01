from __future__ import annotations

from typing import Any


def sequence_distance(first: str, second: str) -> int:
    """Return deterministic Levenshtein distance for short peptide sequences."""
    if len(first) < len(second):
        first, second = second, first
    previous = list(range(len(second) + 1))
    for row, first_residue in enumerate(first, start=1):
        current = [row]
        for column, second_residue in enumerate(second, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (first_residue != second_residue),
                )
            )
        previous = current
    return previous[-1]


def sequence_similarity(first: str, second: str) -> float:
    scale = max(len(first), len(second))
    return 1.0 if scale == 0 else 1.0 - sequence_distance(first, second) / scale


def cheap_diverse_selection(
    candidates: list[dict[str, Any]], limit: int, maximum_similarity: float
) -> list[dict[str, Any]]:
    """Select low-PPL proposals while enforcing a transparent sequence-diversity cap."""
    ranked = sorted(
        candidates,
        key=lambda item: (float(item["conditional_ppl"]), item["sequence"]),
    )
    selected: list[dict[str, Any]] = []
    for candidate in ranked:
        if all(
            sequence_similarity(candidate["sequence"], incumbent["sequence"]) <= maximum_similarity
            for incumbent in selected
        ):
            selected.append(candidate)
        if len(selected) == limit:
            break
    return selected


def research_quality_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    """A staged comparator; no weighted pseudo-precision and no Pareto terminology."""
    metrics = candidate.get("metrics", {})
    gate_pass = float(metrics.get("interface_gate_pass", 0.0)) >= 0.5
    rosetta = metrics.get("rosetta_dg_separated_reu")
    favorable_rosetta = rosetta is not None and float(rosetta) < 0.0
    pocket_consistency = float(metrics.get("pocket_contact_consistency", 0.0))
    pair_iptm = float(metrics.get("boltz2_pair_iptm_median", 0.0))
    ppl = float(metrics.get("conditional_ppl", float("inf")))
    # Higher tuples are better. A computed but unfavorable Rosetta result must not
    # outrank an otherwise stronger candidate merely because it was the one expensive
    # candidate selected for refinement.
    return (
        int(gate_pass),
        int(favorable_rosetta),
        -float(rosetta) if favorable_rosetta else float("-inf"),
        pocket_consistency,
        pair_iptm,
        -ppl,
        candidate["sequence"],
    )


def diversity_constrained_elites(
    candidates: list[dict[str, Any]], limit: int, maximum_similarity: float
) -> list[dict[str, Any]]:
    """Keep the best evidence-bearing candidate in each distinct sequence neighborhood."""
    ranked = sorted(candidates, key=research_quality_key, reverse=True)
    selected: list[dict[str, Any]] = []
    for candidate in ranked:
        if all(
            sequence_similarity(candidate["sequence"], incumbent["sequence"]) <= maximum_similarity
            for incumbent in selected
        ):
            selected.append(candidate)
        if len(selected) == limit:
            break
    return selected
