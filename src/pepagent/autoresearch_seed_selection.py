from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pepagent.provenance.hashing import sha256_text

CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
MINIMUM_WETLAB_LENGTH = 20
MAXIMUM_WETLAB_LENGTH = 30

_REQUIRED_COLUMNS = frozenset(
    {
        "activity_model_support_count",
        "candidate_id",
        "display_eligible",
        "family_key_80_80",
        "family_size_80_80_with_baseline",
        "formal_metric_count",
        "formal_metrics_complete",
        "guruprasad_instability_index",
        "guruprasad_instability_ood",
        "macrel_hemolysis_label",
        "macrel_hemolysis_probability",
        "pareto_depth_within_expansion_target",
        "safety_labels_pass",
        "sequence",
        "sequence_sha256",
        "target_key",
        "toxinpred3_hybrid_score",
        "toxinpred3_label",
        "valid_sequence",
    }
)


@dataclass(frozen=True)
class SeedSelectionResult:
    target_key: str
    requested_count: int
    selected_rows: tuple[dict[str, str], ...]
    consensus_family_count: int
    supplemental_family_count: int
    excluded_structure_history_count: int
    eligible_family_count: int


def _parse_bool(value: Any, *, field: str) -> bool:
    normalized = str(value).strip().casefold()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"{field} is not a canonical boolean")


def _finite_float(row: Mapping[str, Any], field: str) -> float:
    value = float(row[field])
    if not math.isfinite(value):
        raise ValueError(f"{field} is not finite")
    return value


def _integer(row: Mapping[str, Any], field: str) -> int:
    value = float(row[field])
    if not math.isfinite(value) or not value.is_integer():
        raise ValueError(f"{field} is not an integer")
    return int(value)


def _normalize_and_validate(row: Mapping[str, Any], *, row_number: int) -> dict[str, str]:
    missing = _REQUIRED_COLUMNS - set(row)
    if missing:
        raise ValueError(
            f"strict seed row {row_number} is missing columns: {sorted(missing)}"
        )
    normalized = {str(key): str(value) for key, value in row.items()}
    sequence = "".join(normalized["sequence"].split()).upper()
    if not sequence or set(sequence) - CANONICAL_AMINO_ACIDS:
        raise ValueError(f"strict seed row {row_number} has an invalid sequence")
    if sha256_text(sequence) != normalized["sequence_sha256"]:
        raise ValueError(f"strict seed row {row_number} sequence SHA-256 drifted")
    normalized["sequence"] = sequence
    if not normalized["candidate_id"].strip() or not normalized["family_key_80_80"].strip():
        raise ValueError(f"strict seed row {row_number} lacks candidate/family identity")
    if _integer(normalized, "formal_metric_count") != 12 or not _parse_bool(
        normalized["formal_metrics_complete"], field="formal_metrics_complete"
    ):
        raise ValueError(f"strict seed row {row_number} lacks formal 12-score evidence")
    for field in (
        "activity_model_support_count",
        "family_size_80_80_with_baseline",
    ):
        _integer(normalized, field)
    for field in (
        "guruprasad_instability_index",
        "macrel_hemolysis_probability",
        "toxinpred3_hybrid_score",
    ):
        _finite_float(normalized, field)
    pareto = normalized["pareto_depth_within_expansion_target"].strip()
    if pareto:
        _integer(normalized, "pareto_depth_within_expansion_target")
    return normalized


def _is_ood_qualified(row: Mapping[str, str], *, target_key: str) -> bool:
    sequence = row["sequence"]
    return bool(
        row["target_key"].strip().casefold() == target_key
        and MINIMUM_WETLAB_LENGTH <= len(sequence) <= MAXIMUM_WETLAB_LENGTH
        and _parse_bool(row["valid_sequence"], field="valid_sequence")
        and _parse_bool(row["display_eligible"], field="display_eligible")
        and _parse_bool(row["safety_labels_pass"], field="safety_labels_pass")
        and not _parse_bool(
            row["guruprasad_instability_ood"], field="guruprasad_instability_ood"
        )
        and _finite_float(row, "guruprasad_instability_index") < 50.0
        and row["toxinpred3_label"].strip().casefold()
        in {"non-toxin", "non-toxic", "nontoxic"}
        and row["macrel_hemolysis_label"].strip().casefold() == "low"
    )


def _front_key(row: Mapping[str, str]) -> tuple[Any, ...]:
    """Order by explicit fronts/ties without a weighted aggregate score."""

    pareto_raw = row["pareto_depth_within_expansion_target"].strip()
    pareto_depth = int(float(pareto_raw)) if pareto_raw else 2**31 - 1
    return (
        pareto_depth,
        _integer(row, "family_size_80_80_with_baseline"),
        -_integer(row, "activity_model_support_count"),
        _finite_float(row, "macrel_hemolysis_probability"),
        _finite_float(row, "toxinpred3_hybrid_score"),
        _finite_float(row, "guruprasad_instability_index"),
        row["sequence_sha256"],
    )


def select_ood_qualified_seed_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    target_key: str,
    count: int,
    excluded_sequence_sha256s: Sequence[str] = (),
    excluded_family_keys: Sequence[str] = (),
) -> SeedSelectionResult:
    """Select family-diverse wetlab seeds and exclude prior structure history.

    Consensus-supported families are exhausted before supplemental families. Within
    each lane, existing non-weighted Pareto depth and family rarity are explicit
    lexicographic fronts; no scalarized or weighted total score is calculated.
    The returned row dictionaries retain all source fields for the formal 12-score
    source split and PostgreSQL import.
    """

    normalized_target = target_key.strip().casefold()
    if not normalized_target:
        raise ValueError("target_key is required")
    if count < 1:
        raise ValueError("seed selection count must be positive")
    excluded_sequences = {str(item).strip().lower() for item in excluded_sequence_sha256s}
    excluded_families = {str(item).strip() for item in excluded_family_keys}
    invalid_excluded = {
        item
        for item in excluded_sequences
        if len(item) != 64 or set(item) - set("0123456789abcdef")
    }
    if invalid_excluded:
        raise ValueError("structure-history exclusion contains an invalid sequence SHA-256")

    by_family: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen_sequences: set[str] = set()
    excluded_structure_history_count = 0
    for row_number, source_row in enumerate(rows, start=2):
        row = _normalize_and_validate(source_row, row_number=row_number)
        if row["target_key"].strip().casefold() != normalized_target:
            continue
        digest = row["sequence_sha256"]
        family = row["family_key_80_80"]
        if digest in excluded_sequences:
            excluded_structure_history_count += 1
            continue
        if family in excluded_families or not _is_ood_qualified(
            row, target_key=normalized_target
        ):
            continue
        if digest in seen_sequences:
            raise ValueError("strict target split contains duplicate sequence identity")
        seen_sequences.add(digest)
        by_family[family].append(row)

    consensus: list[dict[str, str]] = []
    supplemental: list[dict[str, str]] = []
    for family_rows in by_family.values():
        supported = [
            row for row in family_rows if _integer(row, "activity_model_support_count") >= 2
        ]
        if supported:
            consensus.append(min(supported, key=_front_key))
        else:
            supplemental.append(min(family_rows, key=_front_key))
    consensus.sort(key=_front_key)
    supplemental.sort(key=_front_key)
    selected = [*consensus[:count]]
    if len(selected) < count:
        selected.extend(supplemental[: count - len(selected)])
    if len(selected) != count:
        raise ValueError(
            f"target {normalized_target} has only {len(selected)} eligible unique families; "
            f"{count} required"
        )
    selected_sequence_ids = {row["sequence_sha256"] for row in selected}
    selected_family_ids = {row["family_key_80_80"] for row in selected}
    if len(selected_sequence_ids) != count or len(selected_family_ids) != count:
        raise AssertionError("seed selection uniqueness invariant failed")
    consensus_selected = min(len(consensus), count)
    return SeedSelectionResult(
        target_key=normalized_target,
        requested_count=count,
        selected_rows=tuple(dict(row) for row in selected),
        consensus_family_count=consensus_selected,
        supplemental_family_count=count - consensus_selected,
        excluded_structure_history_count=excluded_structure_history_count,
        eligible_family_count=len(by_family),
    )


__all__ = ["SeedSelectionResult", "select_ood_qualified_seed_rows"]
