from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean

ARMS = ("baseline", "cards_only", "pepshot_only", "cards_and_pepshot")


def deterministic_arm_order(parent_id: str, salt: str) -> tuple[str, ...]:
    """Return a stable order without allowing outcome-dependent arm scheduling."""
    ranked = sorted(
        ARMS,
        key=lambda arm: hashlib.sha256(
            f"{salt}\x00{parent_id}\x00{arm}".encode()
        ).hexdigest(),
    )
    return tuple(ranked)


@dataclass(frozen=True)
class FactorialContrasts:
    knowledge_main_effect: float
    pepshot_main_effect: float
    knowledge_by_pepshot_interaction: float
    cards_only_vs_baseline: float
    pepshot_only_vs_baseline: float
    cards_and_pepshot_vs_baseline: float
    parent_count: int


def paired_factorial_contrasts(
    parent_arm_values: Mapping[str, Mapping[str, float]],
    *,
    direction: str,
) -> FactorialContrasts:
    """Compute paired 2x2 effects, oriented so positive always means improvement."""
    if direction not in {"maximize", "minimize"}:
        raise ValueError("direction must be maximize or minimize")
    if not parent_arm_values:
        raise ValueError("at least one complete parent block is required")

    sign = 1.0 if direction == "maximize" else -1.0
    rows: list[tuple[float, float, float, float]] = []
    for parent_id, values in parent_arm_values.items():
        missing = set(ARMS) - set(values)
        extra = set(values) - set(ARMS)
        if missing or extra:
            raise ValueError(
                f"parent {parent_id!r} must have exactly four arms; "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        row = tuple(sign * float(values[arm]) for arm in ARMS)
        rows.append(row)  # type: ignore[arg-type]

    def mean(items: Sequence[float]) -> float:
        return float(fmean(items))

    baseline = [row[0] for row in rows]
    cards = [row[1] for row in rows]
    pepshot = [row[2] for row in rows]
    both = [row[3] for row in rows]
    cards_delta = [c - b for b, c in zip(baseline, cards, strict=True)]
    pepshot_delta = [p - b for b, p in zip(baseline, pepshot, strict=True)]
    both_delta = [x - b for b, x in zip(baseline, both, strict=True)]
    knowledge_main = [
        ((c - b) + (x - p)) / 2
        for b, c, p, x in zip(baseline, cards, pepshot, both, strict=True)
    ]
    pepshot_main = [
        ((p - b) + (x - c)) / 2
        for b, c, p, x in zip(baseline, cards, pepshot, both, strict=True)
    ]
    interaction = [
        (x - c) - (p - b)
        for b, c, p, x in zip(baseline, cards, pepshot, both, strict=True)
    ]
    return FactorialContrasts(
        knowledge_main_effect=mean(knowledge_main),
        pepshot_main_effect=mean(pepshot_main),
        knowledge_by_pepshot_interaction=mean(interaction),
        cards_only_vs_baseline=mean(cards_delta),
        pepshot_only_vs_baseline=mean(pepshot_delta),
        cards_and_pepshot_vs_baseline=mean(both_delta),
        parent_count=len(rows),
    )
