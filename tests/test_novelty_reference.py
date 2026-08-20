from __future__ import annotations

import pytest

from pepagent.novelty_reference import (
    normalize_fasta_reference,
    require_candidate_independent_threshold_policy,
)


def test_reference_normalization_is_deduplicated_and_deterministic() -> None:
    fasta = ">b\nACDEFGHIK\n>a\nACDEFGHIK\n>c\nKKLLKKLL\n"
    normalized, witness = normalize_fasta_reference(fasta)
    assert witness.source_record_count == 3
    assert witness.accepted_record_count == 3
    assert witness.unique_sequence_count == 2
    assert witness.duplicate_record_count == 1
    assert normalized.count(">ampref_") == 2


def test_reference_normalization_audits_domain_rejections() -> None:
    fasta = ">short\nACD\n>modified\nACDEXGHIK\n>valid\nACDEFGHIK\n"
    _normalized, witness = normalize_fasta_reference(fasta)
    assert witness.rejected_length_count == 1
    assert witness.rejected_noncanonical_count == 1
    assert witness.unique_sequence_count == 1


def test_threshold_policy_fails_closed_without_external_holdout() -> None:
    with pytest.raises(ValueError, match="external holdout"):
        require_candidate_independent_threshold_policy(
            {
                "schema_version": "ampgent.novelty-ood-threshold-policy.1",
                "current_candidate_batch_used_for_threshold_fit": False,
                "external_holdout_calibration_status": "pending",
            }
        )


def test_threshold_policy_rejects_current_batch_fit() -> None:
    with pytest.raises(ValueError, match="current candidate batch"):
        require_candidate_independent_threshold_policy(
            {
                "schema_version": "ampgent.novelty-ood-threshold-policy.1",
                "current_candidate_batch_used_for_threshold_fit": True,
                "external_holdout_calibration_status": "passed",
                "external_holdout_calibration_artifact_sha256": "a" * 64,
            }
        )


def test_threshold_policy_accepts_pinned_independent_calibration() -> None:
    require_candidate_independent_threshold_policy(
        {
            "schema_version": "ampgent.novelty-ood-threshold-policy.1",
            "current_candidate_batch_used_for_threshold_fit": False,
            "external_holdout_calibration_status": "passed",
            "external_holdout_calibration_artifact_sha256": "a" * 64,
        }
    )
