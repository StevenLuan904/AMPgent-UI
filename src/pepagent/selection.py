from __future__ import annotations

from typing import Any


def _rules_for_stage(
    metric_policy: list[dict[str, Any]] | None, stage: str, role: str | None = None
) -> list[dict[str, Any]]:
    return [
        rule
        for rule in metric_policy or []
        if stage in rule.get("stages", ["research", "final"])
        and (role is None or rule.get("role") == role)
    ]


def qualification_violations(
    candidate: dict[str, Any],
    metric_policy: list[dict[str, Any]] | None,
    stage: str,
) -> list[dict[str, Any]]:
    """Evaluate non-compensatory qualification rules with explicit missing-data behavior."""
    metrics = candidate.get("metrics", {})
    violations: list[dict[str, Any]] = []
    for rule in _rules_for_stage(metric_policy, stage, "qualification"):
        value = metrics.get(rule["metric_name"])
        if value is None:
            if rule.get("missing_policy", "fail") == "fail":
                violations.append({"metric_name": rule["metric_name"], "reason": "missing"})
            continue
        minimum = rule.get("minimum")
        maximum = rule.get("maximum")
        if minimum is not None and float(value) < float(minimum):
            violations.append(
                {
                    "metric_name": rule["metric_name"],
                    "reason": "below_minimum",
                    "value": float(value),
                    "minimum": float(minimum),
                }
            )
        if maximum is not None and float(value) > float(maximum):
            violations.append(
                {
                    "metric_name": rule["metric_name"],
                    "reason": "above_maximum",
                    "value": float(value),
                    "maximum": float(maximum),
                }
            )
    return violations


def hard_qualification_violations(
    candidate: dict[str, Any],
    metric_policy: list[dict[str, Any]] | None,
    stage: str,
) -> list[dict[str, Any]]:
    hard_names = {
        rule["metric_name"]
        for rule in _rules_for_stage(metric_policy, stage, "qualification")
        if rule.get("hard", True)
    }
    return [
        violation
        for violation in qualification_violations(candidate, metric_policy, stage)
        if violation["metric_name"] in hard_names
    ]


def _objective_key(
    candidate: dict[str, Any], metric_policy: list[dict[str, Any]] | None, stage: str
) -> tuple[float, ...]:
    metrics = candidate.get("metrics", {})
    values: list[float] = []
    rules = sorted(
        _rules_for_stage(metric_policy, stage, "objective"),
        key=lambda rule: (int(rule.get("priority", 100)), rule["metric_name"]),
    )
    for rule in rules:
        value = metrics.get(rule["metric_name"])
        if value is None:
            # Missing evidence never receives an availability bonus. It sorts last unless
            # a rule explicitly says to ignore it, in which case it is neutral at this stage.
            values.append(0.0 if rule.get("missing_policy") == "ignore" else float("-inf"))
        else:
            numeric = float(value)
            values.append(numeric if rule["direction"] == "maximize" else -numeric)
    return tuple(values)


def policy_quality_key(
    candidate: dict[str, Any], metric_policy: list[dict[str, Any]], stage: str
) -> tuple[Any, ...]:
    hard_violations = hard_qualification_violations(candidate, metric_policy, stage)
    return (
        int(not hard_violations),
        -len(hard_violations),
        *_objective_key(candidate, metric_policy, stage),
        candidate["sequence"],
    )


def _maximum_similarity(
    metric_policy: list[dict[str, Any]] | None, stage: str, fallback: float
) -> float:
    rules = [
        rule
        for rule in _rules_for_stage(metric_policy, stage, "diversity")
        if rule["metric_name"] == "sequence_similarity"
    ]
    return float(rules[0]["maximum"]) if rules else fallback


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
    candidates: list[dict[str, Any]],
    limit: int,
    maximum_similarity: float,
    metric_policy: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Select low-PPL proposals while enforcing a transparent sequence-diversity cap."""
    if metric_policy:
        ranked = sorted(
            candidates,
            key=lambda item: policy_quality_key(item, metric_policy, "proposal"),
            reverse=True,
        )
        ranked = [
            item
            for item in ranked
            if not hard_qualification_violations(item, metric_policy, "proposal")
        ]
    else:
        ranked = sorted(
            candidates,
            key=lambda item: (float(item["conditional_ppl"]), item["sequence"]),
        )
    maximum_similarity = _maximum_similarity(
        metric_policy, "proposal", maximum_similarity
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


def diagnostic_representative_selection(
    candidates: list[dict[str, Any]],
    comprehensive_count: int,
    diversity_count: int,
    maximum_similarity: float,
    metric_policy: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Choose property leaders plus deliberately distant diagnostic representatives."""
    ranked = cheap_diverse_selection(
        candidates,
        max(len(candidates), comprehensive_count),
        1.0,
        metric_policy,
    )
    selected = ranked[:comprehensive_count]
    remaining = [item for item in ranked if item not in selected]
    while remaining and len(selected) < comprehensive_count + diversity_count:
        if not selected:
            chosen = remaining[0]
        else:
            chosen = max(
                remaining,
                key=lambda item: (
                    min(
                        sequence_distance(item["sequence"], incumbent["sequence"])
                        for incumbent in selected
                    ),
                    -float(item.get("conditional_ppl", float("inf"))),
                    item["sequence"],
                ),
            )
        if all(
            sequence_similarity(chosen["sequence"], incumbent["sequence"])
            <= maximum_similarity
            for incumbent in selected
        ):
            selected.append(chosen)
        remaining.remove(chosen)
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
    candidates: list[dict[str, Any]],
    limit: int,
    maximum_similarity: float,
    metric_policy: list[dict[str, Any]] | None = None,
    stage: str = "research",
) -> list[dict[str, Any]]:
    """Keep the best evidence-bearing candidate in each distinct sequence neighborhood."""
    ranked = sorted(
        candidates,
        key=(
            (lambda item: policy_quality_key(item, metric_policy, stage))
            if metric_policy
            else research_quality_key
        ),
        reverse=True,
    )
    if metric_policy:
        ranked = [
            item
            for item in ranked
            if not hard_qualification_violations(item, metric_policy, stage)
        ]
    maximum_similarity = _maximum_similarity(
        metric_policy, stage, maximum_similarity
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
