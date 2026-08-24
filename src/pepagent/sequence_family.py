from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class SequenceFamilyAssignment:
    sequence: str
    family_key: str
    representative_sequence: str
    family_size: int


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def ungapped_identity_and_coverage(left: str, right: str) -> tuple[float, float]:
    """Return best full-shorter ungapped identity and length coverage."""

    if not left or not right:
        raise ValueError("sequence-family comparison requires non-empty sequences")
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    coverage = len(shorter) / len(longer)
    best_matches = max(
        sum(a == b for a, b in zip(shorter, longer[offset : offset + len(shorter)], strict=True))
        for offset in range(len(longer) - len(shorter) + 1)
    )
    return best_matches / len(shorter), coverage


def _seeds(sequence: str) -> set[str]:
    width = 2 if len(sequence) < 8 else 3
    return {sequence[index : index + width] for index in range(len(sequence) - width + 1)}


def cluster_sequence_families(
    sequences: Iterable[str],
    *,
    minimum_identity: float = 0.8,
    minimum_coverage: float = 0.8,
) -> tuple[SequenceFamilyAssignment, ...]:
    """Cluster complete peptide sequences with deterministic identity/coverage edges.

    Candidate pairs are retrieved by shared contiguous seeds, then verified using the
    full shorter sequence. Connected components form families. This is intentionally
    stricter and easier to audit than prefix or motif-only grouping.
    """

    if not 0 < minimum_identity <= 1 or not 0 < minimum_coverage <= 1:
        raise ValueError("identity and coverage must be in (0, 1]")
    unique_sequences = sorted(set(sequences))
    if any(not sequence for sequence in unique_sequences):
        raise ValueError("sequence families require non-empty sequences")
    if not unique_sequences:
        return ()

    union_find = _UnionFind(len(unique_sequences))
    seed_index: dict[str, list[int]] = defaultdict(list)
    candidate_pairs: set[tuple[int, int]] = set()
    for index, sequence in enumerate(unique_sequences):
        for seed in _seeds(sequence):
            for other in seed_index[seed]:
                if min(len(sequence), len(unique_sequences[other])) / max(
                    len(sequence), len(unique_sequences[other])
                ) >= minimum_coverage:
                    candidate_pairs.add((other, index))
            seed_index[seed].append(index)

    for left_index, right_index in sorted(candidate_pairs):
        identity, coverage = ungapped_identity_and_coverage(
            unique_sequences[left_index], unique_sequences[right_index]
        )
        if identity >= minimum_identity and coverage >= minimum_coverage:
            union_find.union(left_index, right_index)

    components: dict[int, list[int]] = defaultdict(list)
    for index in range(len(unique_sequences)):
        components[union_find.find(index)].append(index)

    assignments: list[SequenceFamilyAssignment] = []
    for members in components.values():
        member_sequences = sorted(unique_sequences[index] for index in members)
        representative = min(
            member_sequences,
            key=lambda sequence: (hashlib.sha256(sequence.encode("ascii")).hexdigest(), sequence),
        )
        family_key = "seqfam80_" + hashlib.sha256(representative.encode("ascii")).hexdigest()[:20]
        for sequence in member_sequences:
            assignments.append(
                SequenceFamilyAssignment(
                    sequence=sequence,
                    family_key=family_key,
                    representative_sequence=representative,
                    family_size=len(member_sequences),
                )
            )
    return tuple(sorted(assignments, key=lambda item: item.sequence))
