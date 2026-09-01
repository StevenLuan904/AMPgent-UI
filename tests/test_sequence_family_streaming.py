from __future__ import annotations

import hashlib
import random
import tracemalloc
from collections import defaultdict
from collections.abc import Iterable
from itertools import product

import pytest

from pepagent import sequence_family as sequence_family_module
from pepagent.sequence_family import (
    SequenceFamilyAssignment,
    cluster_sequence_families,
    ungapped_identity_and_coverage,
)

AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"


class _ReferenceUnionFind:
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


def _legacy_seeds(sequence: str) -> set[str]:
    width = 2 if len(sequence) < 8 else 3
    return {sequence[index : index + width] for index in range(len(sequence) - width + 1)}


def _legacy_cluster_sequence_families(
    sequences: Iterable[str],
    *,
    minimum_identity: float = 0.8,
    minimum_coverage: float = 0.8,
) -> tuple[SequenceFamilyAssignment, ...]:
    """Frozen pre-streaming implementation used only as an equivalence oracle."""

    unique_sequences = sorted(set(sequences))
    union_find = _ReferenceUnionFind(len(unique_sequences))
    seed_index: dict[str, list[int]] = defaultdict(list)
    candidate_pairs: set[tuple[int, int]] = set()
    for index, sequence in enumerate(unique_sequences):
        for seed in _legacy_seeds(sequence):
            for other in seed_index[seed]:
                if (
                    min(len(sequence), len(unique_sequences[other]))
                    / max(len(sequence), len(unique_sequences[other]))
                    >= minimum_coverage
                ):
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
            key=lambda sequence: (
                hashlib.sha256(sequence.encode("ascii")).hexdigest(),
                sequence,
            ),
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


def _random_peptides(size: int, seed: int, *, length: int = 10) -> set[str]:
    generator = random.Random(seed)
    sequences: set[str] = set()
    while len(sequences) < size:
        sequences.add("".join(generator.choice(AMINO_ACIDS) for _ in range(length)))
    return sequences


def _mutate(sequence: str, positions: tuple[int, ...], salt: int) -> str:
    mutated = list(sequence)
    for offset, position in enumerate(positions):
        current_index = AMINO_ACIDS.index(mutated[position])
        mutated[position] = AMINO_ACIDS[(current_index + salt + offset + 1) % len(AMINO_ACIDS)]
    return "".join(mutated)


def _five_thousand_regression_sequences() -> set[str]:
    sequences = _random_peptides(4_000, 20260829)
    parents = sorted(sequences)[:500]
    for index, parent in enumerate(parents):
        sequences.add(_mutate(parent, (index % 10, (index + 3) % 10), index % 17))
    filler = _random_peptides(5_500, 20260830)
    for sequence in sorted(filler):
        sequences.add(sequence)
        if len(sequences) == 5_000:
            break
    assert len(sequences) == 5_000
    return sequences


def _shared_seed_stress_sequences(size: int) -> set[str]:
    sequences: set[str] = set()
    nonce = 0
    while len(sequences) < size:
        digest = hashlib.sha256(f"stream-memory-{nonce}".encode()).digest()
        sequence = "".join(AMINO_ACIDS[value % len(AMINO_ACIDS)] for value in digest[:20])
        # Every peptide shares this 3-mer, which made the legacy implementation
        # retain all n*(n-1)/2 pairs. Crossing a four-position block boundary keeps
        # this a realistic worst case for the old seed index without gaming the new one.
        sequences.add(sequence[:2] + "AAA" + sequence[5:])
        nonce += 1
    return sequences


def test_streaming_clustering_matches_legacy_fixed_boundaries() -> None:
    sequences = {
        "ACDEFGHIKL",
        "ACDEYGHVKL",  # exactly 80% identity
        "CACDEFGHIKL",  # full-shorter offset and >80% coverage
        "ACDEFGH",
        "ACDEYGH",  # the historical two-residue seed boundary
        "VVVVVVVVVV",
        "A",  # no historical 2-mer seed
        "C",
    }
    assert cluster_sequence_families(sequences) == _legacy_cluster_sequence_families(sequences)


@pytest.mark.parametrize("size", [50, 200, 1_000])
def test_streaming_clustering_matches_legacy_seeded_random(size: int) -> None:
    sequences = _random_peptides(size, 20260829 + size)
    parents = sorted(sequences)[: max(1, size // 10)]
    sequences.update(
        _mutate(parent, (index % 10, (index + 5) % 10), index % 13)
        for index, parent in enumerate(parents)
    )
    assert cluster_sequence_families(sequences) == _legacy_cluster_sequence_families(sequences)


@pytest.mark.parametrize(
    ("minimum_identity", "minimum_coverage"),
    [(0.6, 0.75), (0.75, 0.8), (0.8, 0.8), (0.9, 1.0)],
)
def test_streaming_clustering_matches_legacy_custom_thresholds(
    minimum_identity: float,
    minimum_coverage: float,
) -> None:
    sequences = _random_peptides(200, 20260831, length=12)
    parents = sorted(sequences)[:40]
    sequences.update(
        _mutate(parent, (index % 12, (index + 4) % 12), index % 11)
        for index, parent in enumerate(parents)
    )
    expected = _legacy_cluster_sequence_families(
        sequences,
        minimum_identity=minimum_identity,
        minimum_coverage=minimum_coverage,
    )
    actual = cluster_sequence_families(
        sequences,
        minimum_identity=minimum_identity,
        minimum_coverage=minimum_coverage,
    )
    assert actual == expected


def test_streaming_clustering_matches_legacy_exhaustive_short_sequences() -> None:
    sequences = {"".join(chars) for length in range(1, 9) for chars in product("AC", repeat=length)}
    assert cluster_sequence_families(sequences) == _legacy_cluster_sequence_families(sequences)


def test_streaming_clustering_matches_legacy_five_thousand_sequence_regression() -> None:
    sequences = _five_thousand_regression_sequences()
    assert cluster_sequence_families(sequences) == _legacy_cluster_sequence_families(sequences)


def test_streaming_candidate_memory_stays_bounded_for_shared_seed_stress() -> None:
    sequences = _shared_seed_stress_sequences(5_000)
    tracemalloc.start()
    try:
        assignments = cluster_sequence_families(sequences)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(assignments) == 5_000
    assert peak_bytes < 64 * 1024 * 1024


def test_dense_connected_family_skips_redundant_edge_verification(monkeypatch) -> None:
    parent = "ACDEFGHIKLMNPQRSTVWY"
    sequences = {parent}
    for position, current in enumerate(parent):
        for replacement in AMINO_ACIDS:
            if replacement != current:
                sequences.add(parent[:position] + replacement + parent[position + 1 :])
            if len(sequences) == 200:
                break
        if len(sequences) == 200:
            break

    call_count = 0
    original = sequence_family_module._passes_family_edge

    def counted(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(sequence_family_module, "_passes_family_edge", counted)
    assignments = sequence_family_module.cluster_sequence_families(sequences)

    assert len({item.family_key for item in assignments}) == 1
    assert call_count < len(sequences) * 2
