from __future__ import annotations

import pytest

from pepagent.macrel_lineage_audit import (
    LabelledSequence,
    audit_macrel_hemopi_lineage,
    parse_fasta,
    parse_hemopi2_csv,
)


def test_parsers_preserve_labels_and_reject_noncanonical_sequences() -> None:
    assert parse_fasta(b">a\nACDE\n>b\nKKLL\n", label=1) == [
        LabelledSequence("ACDE", 1),
        LabelledSequence("KKLL", 1),
    ]
    assert parse_hemopi2_csv(b"sequence,value,label\nACDE,1.0,0\n") == [
        LabelledSequence("ACDE", 0)
    ]
    with pytest.raises(ValueError, match="noncanonical"):
        parse_fasta(b">a\nACDX\n", label=0)


def test_exact_training_overlap_blocks_independence_and_unfiltered_benchmark() -> None:
    audit = audit_macrel_hemopi_lineage(
        macrel_training=[LabelledSequence("ACDE", 1), LabelledSequence("KKLL", 0)],
        macrel_validation=[LabelledSequence("RRWW", 1)],
        hemopi2_cross_validation=[LabelledSequence("ACDE", 1)],
        hemopi2_independent=[LabelledSequence("KKLL", 1), LabelledSequence("GGVV", 0)],
    )
    assert audit["independence_decision"] == {
        "same_evidence_family": True,
        "second_independent_hemolysis_source_allowed": False,
        "hemopi2_independent_set_valid_for_unfiltered_macrel_benchmark": False,
    }
    overlap = audit["overlaps"]["macrel_training_vs_hemopi2_independent"]
    assert overlap["exact_sequence_overlap_count"] == 1
    assert overlap["label_conflict_count"] == 1


def test_disjoint_sources_remain_eligible_for_later_benchmarking() -> None:
    audit = audit_macrel_hemopi_lineage(
        macrel_training=[LabelledSequence("ACDE", 1)],
        macrel_validation=[LabelledSequence("KKLL", 0)],
        hemopi2_cross_validation=[LabelledSequence("RRWW", 1)],
        hemopi2_independent=[LabelledSequence("GGVV", 0)],
    )
    assert audit["independence_decision"]["same_evidence_family"] is False
    assert (
        audit["independence_decision"][
            "hemopi2_independent_set_valid_for_unfiltered_macrel_benchmark"
        ]
        is True
    )
