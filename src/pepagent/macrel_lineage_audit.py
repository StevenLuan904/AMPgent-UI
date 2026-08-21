from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

STANDARD_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")


@dataclass(frozen=True)
class LabelledSequence:
    sequence: str
    label: int


def parse_fasta(payload: bytes, *, label: int) -> list[LabelledSequence]:
    if label not in {0, 1}:
        raise ValueError("FASTA label must be zero or one")
    records: list[LabelledSequence] = []
    current: list[str] = []
    for raw_line in payload.decode("utf-8-sig").splitlines():
        line = raw_line.strip()
        if line.startswith(">"):
            if current:
                records.append(_record("".join(current), label))
                current = []
        elif line:
            current.append(line)
    if current:
        records.append(_record("".join(current), label))
    if not records:
        raise ValueError("FASTA contains no sequences")
    if len({record.sequence for record in records}) != len(records):
        raise ValueError("FASTA contains duplicate sequences")
    return records


def parse_hemopi2_csv(payload: bytes) -> list[LabelledSequence]:
    rows = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))
    records: list[LabelledSequence] = []
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) < 3:
            raise ValueError(f"HemoPI2 row {row_number} is incomplete")
        try:
            label = int(row[-1])
        except ValueError as exc:
            raise ValueError(f"HemoPI2 row {row_number} label is invalid") from exc
        records.append(_record(row[0], label))
    if len({record.sequence for record in records}) != len(records):
        raise ValueError("HemoPI2 dataset contains duplicate sequences")
    return records


def _record(sequence: str, label: int) -> LabelledSequence:
    normalized = sequence.strip().upper()
    if not normalized or set(normalized) - STANDARD_AMINO_ACIDS:
        raise ValueError("sequence contains noncanonical amino acids")
    if label not in {0, 1}:
        raise ValueError("sequence label must be zero or one")
    return LabelledSequence(sequence=normalized, label=label)


def _mapping(records: Sequence[LabelledSequence]) -> dict[str, int]:
    result: dict[str, int] = {}
    for record in records:
        previous = result.setdefault(record.sequence, record.label)
        if previous != record.label:
            raise ValueError("the same sequence has conflicting labels within one source")
    return result


def _overlap(left: Mapping[str, int], right: Mapping[str, int]) -> dict[str, Any]:
    shared = sorted(set(left) & set(right))
    agreement = sum(left[sequence] == right[sequence] for sequence in shared)
    return {
        "exact_sequence_overlap_count": len(shared),
        "label_agreement_count": agreement,
        "label_conflict_count": len(shared) - agreement,
        "shared_sequence_set_sha256": hashlib.sha256(
            (("\n".join(shared) + "\n") if shared else "").encode()
        ).hexdigest(),
    }


def audit_macrel_hemopi_lineage(
    *,
    macrel_training: Sequence[LabelledSequence],
    macrel_validation: Sequence[LabelledSequence],
    hemopi2_cross_validation: Sequence[LabelledSequence],
    hemopi2_independent: Sequence[LabelledSequence],
) -> dict[str, Any]:
    training = _mapping(macrel_training)
    validation = _mapping(macrel_validation)
    cross_validation = _mapping(hemopi2_cross_validation)
    independent = _mapping(hemopi2_independent)
    if set(training) & set(validation):
        raise ValueError("HemoPI-1 training and validation overlap exactly")
    train_cross = _overlap(training, cross_validation)
    train_independent = _overlap(training, independent)
    validation_cross = _overlap(validation, cross_validation)
    validation_independent = _overlap(validation, independent)
    same_family = bool(
        train_cross["exact_sequence_overlap_count"]
        or train_independent["exact_sequence_overlap_count"]
        or validation_cross["exact_sequence_overlap_count"]
        or validation_independent["exact_sequence_overlap_count"]
    )
    return {
        "schema_version": "ampgent.macrel-hemopi-lineage-audit.1",
        "macrel_training_count": len(training),
        "macrel_validation_count": len(validation),
        "hemopi2_cross_validation_count": len(cross_validation),
        "hemopi2_independent_count": len(independent),
        "overlaps": {
            "macrel_training_vs_hemopi2_cross_validation": train_cross,
            "macrel_training_vs_hemopi2_independent": train_independent,
            "macrel_validation_vs_hemopi2_cross_validation": validation_cross,
            "macrel_validation_vs_hemopi2_independent": validation_independent,
        },
        "independence_decision": {
            "same_evidence_family": same_family,
            "second_independent_hemolysis_source_allowed": not same_family,
            "hemopi2_independent_set_valid_for_unfiltered_macrel_benchmark": (
                train_independent["exact_sequence_overlap_count"] == 0
            ),
        },
    }
