from __future__ import annotations

import pytest

from pepagent.esm2_training_overlap import decide_esm2_training_overlap_audit


def test_unverified_smaller_mirror_never_substitutes_for_official_corpus() -> None:
    decision = decide_esm2_training_overlap_audit(
        official_archive_bytes=169_188_630_923,
        maximum_download_bytes=25_000_000_000,
        official_archive_available=True,
        alternative_source_available=True,
        alternative_source_equivalence_verified=False,
        training_membership_semantics_reproducible=False,
    )
    assert decision.audit_status == "exact_membership_audit_not_feasible"
    assert decision.model_usage == "shadow_diagnostic_only"
    assert decision.formal_ood_label_allowed is False
    assert decision.reasons == (
        "official_archive_exceeds_download_budget",
        "alternative_source_equivalence_unverified",
        "ur50d_training_membership_sampling_not_reproducible",
    )


def test_exact_source_still_requires_reproducible_membership_semantics() -> None:
    decision = decide_esm2_training_overlap_audit(
        official_archive_bytes=10,
        maximum_download_bytes=10,
        official_archive_available=True,
        alternative_source_available=False,
        alternative_source_equivalence_verified=False,
        training_membership_semantics_reproducible=False,
    )
    assert decision.exact_membership_audit_allowed is False
    assert decision.formal_ood_label_allowed is False


def test_ready_audit_does_not_prematurely_authorize_formal_ood_labels() -> None:
    decision = decide_esm2_training_overlap_audit(
        official_archive_bytes=10,
        maximum_download_bytes=10,
        official_archive_available=True,
        alternative_source_available=False,
        alternative_source_equivalence_verified=False,
        training_membership_semantics_reproducible=True,
    )
    assert decision.exact_membership_audit_allowed is True
    assert decision.model_usage == "shadow_until_overlap_results_pass"
    assert decision.formal_ood_label_allowed is False


def test_invalid_byte_budget_fails_closed() -> None:
    with pytest.raises(ValueError, match="byte budgets"):
        decide_esm2_training_overlap_audit(
            official_archive_bytes=0,
            maximum_download_bytes=0,
            official_archive_available=True,
            alternative_source_available=False,
            alternative_source_equivalence_verified=False,
            training_membership_semantics_reproducible=True,
        )
