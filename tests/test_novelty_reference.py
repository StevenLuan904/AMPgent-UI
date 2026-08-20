from __future__ import annotations

import json
from pathlib import Path

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


def test_pinned_mmseqs_manifest_binds_runtime_reference_and_smoke() -> None:
    root = Path(__file__).parents[1]
    manifest = json.loads(
        (root / "config/enterprise/mmseqs_reference_index_manifest_v39.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["runtime"]["commit"] == "8cc5ce367b5638c4306c2d7cfc652dd099a4643f"
    assert manifest["reference"]["normalized_fasta_sha256"] == (
        "d1004b1398df723b2e4a044aaab13b6d9628d7fec23042e2cccd88f8534d6787"
    )
    assert manifest["build"]["use_gpu"] is False
    assert manifest["build"]["index_file_set_sha256"] == (
        "9fe1a7eb74832f9b231dc09e5cdcd71524465937b955361e5f5faed3ddef9359"
    )
    assert manifest["smoke"]["status"] == "passed"
    assert manifest["smoke"]["top_hit_identity"] == 1.0
