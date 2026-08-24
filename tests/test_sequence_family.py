from __future__ import annotations

from pepagent.sequence_family import (
    cluster_sequence_families,
    ungapped_identity_and_coverage,
)


def test_ungapped_identity_uses_full_shorter_sequence() -> None:
    identity, coverage = ungapped_identity_and_coverage("ACDEFGHI", "XXACDEYGHI")
    assert identity == 0.875
    assert coverage == 0.8


def test_family_clustering_groups_close_sequences_and_separates_distant_ones() -> None:
    assignments = cluster_sequence_families(
        ["ACDEFGHI", "ACDEYGHI", "TTTTTTTT", "ACDEFGHIK"]
    )
    family_by_sequence = {item.sequence: item.family_key for item in assignments}
    assert family_by_sequence["ACDEFGHI"] == family_by_sequence["ACDEYGHI"]
    assert family_by_sequence["ACDEFGHI"] == family_by_sequence["ACDEFGHIK"]
    assert family_by_sequence["ACDEFGHI"] != family_by_sequence["TTTTTTTT"]


def test_short_sequences_use_two_residue_seed_before_full_verification() -> None:
    assignments = cluster_sequence_families(["ABCDE", "ABXDE", "VWXYZ"])
    family_by_sequence = {item.sequence: item.family_key for item in assignments}
    assert family_by_sequence["ABCDE"] == family_by_sequence["ABXDE"]
    assert family_by_sequence["ABCDE"] != family_by_sequence["VWXYZ"]


def test_family_assignment_is_order_independent() -> None:
    sequences = ["ACDEFGHI", "ACDEYGHI", "TTTTTTTT", "ACDEFGHIK"]
    forward = cluster_sequence_families(sequences)
    reverse = cluster_sequence_families(reversed(sequences))
    assert forward == reverse
