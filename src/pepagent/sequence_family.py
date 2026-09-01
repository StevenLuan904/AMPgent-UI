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


def _maximum_mismatches(length: int, minimum_identity: float) -> int:
    """Return the largest integer mismatch count accepted by the identity threshold."""

    return max(
        mismatches
        for mismatches in range(length + 1)
        if (length - mismatches) / length >= minimum_identity
    )


def _position_blocks(length: int, count: int) -> tuple[tuple[int, int], ...]:
    """Partition sequence positions into deterministic non-empty contiguous blocks."""

    if not 1 <= count <= length:
        raise ValueError("block count must be in [1, length]")
    base_width, remainder = divmod(length, count)
    blocks: list[tuple[int, int]] = []
    start = 0
    for index in range(count):
        width = base_width + (1 if index < remainder else 0)
        blocks.append((start, start + width))
        start += width
    return tuple(blocks)


def _passes_family_edge(
    shorter: str,
    longer: str,
    *,
    minimum_identity: float,
    minimum_coverage: float,
) -> bool:
    """Apply the historical seed and full-sequence edge predicates exactly."""

    # The shared 2-mer/3-mer condition is part of the historical family contract,
    # including its behaviour for one-residue sequences (which have no seeds).
    if not (_seeds(shorter) & _seeds(longer)):
        return False
    identity, coverage = ungapped_identity_and_coverage(shorter, longer)
    return identity >= minimum_identity and coverage >= minimum_coverage


def cluster_sequence_families(
    sequences: Iterable[str],
    *,
    minimum_identity: float = 0.8,
    minimum_coverage: float = 0.8,
) -> tuple[SequenceFamilyAssignment, ...]:
    """Cluster complete peptide sequences with deterministic identity/coverage edges.

    Candidate pairs are retrieved with exact-match blocks, then verified using the
    historical shared-seed and full-shorter-sequence predicates. A sequence with at
    most ``k`` accepted mismatches must match exactly in at least one of ``k + 1``
    position blocks, so the retriever cannot miss an accepted edge. Candidate memory
    is bounded to one right-hand sequence at a time instead of retaining every shared-
    seed pair. Connected components form families.
    """

    if not 0 < minimum_identity <= 1 or not 0 < minimum_coverage <= 1:
        raise ValueError("identity and coverage must be in (0, 1]")
    unique_sequences = sorted(set(sequences))
    if any(not sequence for sequence in unique_sequences):
        raise ValueError("sequence families require non-empty sequences")
    if not unique_sequences:
        return ()

    by_length: dict[int, list[int]] = defaultdict(list)
    for index, sequence in enumerate(unique_sequences):
        by_length[len(sequence)].append(index)

    union_find = _UnionFind(len(unique_sequences))
    lengths = sorted(by_length)
    for shorter_length in lengths:
        mismatch_limit = _maximum_mismatches(shorter_length, minimum_identity)
        blocks = _position_blocks(shorter_length, mismatch_limit + 1)
        shorter_indices = by_length[shorter_length]

        for longer_length in lengths:
            if longer_length < shorter_length:
                continue
            if shorter_length / longer_length < minimum_coverage:
                continue
            longer_indices = by_length[longer_length]

            if shorter_length == longer_length:
                prior_by_signature: dict[tuple[int, str], list[int]] = defaultdict(list)
                for right_index in longer_indices:
                    right = unique_sequences[right_index]
                    candidates: set[int] = set()
                    for block_index, (start, end) in enumerate(blocks):
                        candidates.update(
                            prior_by_signature.get((block_index, right[start:end]), ())
                        )
                    for left_index in sorted(candidates):
                        if union_find.find(left_index) == union_find.find(right_index):
                            continue
                        if _passes_family_edge(
                            unique_sequences[left_index],
                            right,
                            minimum_identity=minimum_identity,
                            minimum_coverage=minimum_coverage,
                        ):
                            union_find.union(left_index, right_index)
                    for block_index, (start, end) in enumerate(blocks):
                        prior_by_signature[(block_index, right[start:end])].append(right_index)
                continue

            shorter_by_signature: dict[tuple[int, str], list[int]] = defaultdict(list)
            for left_index in shorter_indices:
                left = unique_sequences[left_index]
                for block_index, (start, end) in enumerate(blocks):
                    shorter_by_signature[(block_index, left[start:end])].append(left_index)

            for right_index in longer_indices:
                right = unique_sequences[right_index]
                candidates = set()
                for offset in range(longer_length - shorter_length + 1):
                    for block_index, (start, end) in enumerate(blocks):
                        candidates.update(
                            shorter_by_signature.get(
                                (block_index, right[offset + start : offset + end]), ()
                            )
                        )
                for left_index in sorted(candidates):
                    if union_find.find(left_index) == union_find.find(right_index):
                        continue
                    if _passes_family_edge(
                        unique_sequences[left_index],
                        right,
                        minimum_identity=minimum_identity,
                        minimum_coverage=minimum_coverage,
                    ):
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
