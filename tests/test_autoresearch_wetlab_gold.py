from __future__ import annotations

import pytest
from pydantic import ValidationError

from pepagent.autoresearch_wetlab_gold import (
    ChallengerReviewEvidence,
    RosettaDGReceiptEvidence,
    SequenceQualityEvidence,
    TargetStructureQualificationEvidence,
    candidate_pool_a_rosetta_gate,
    hidden_pool_s_gate,
    select_wetlab_gold_candidates,
)
from pepagent.provenance.hashing import sha256_text

ALPHABET = "ACDEFGHIKLMNPQRSTVWY"


def _qualification(*, exploratory: bool = False) -> TargetStructureQualificationEvidence:
    if exploratory:
        return TargetStructureQualificationEvidence(
            target_key="angpt1",
            target_sequence_sha256=sha256_text("angpt1-target"),
            target_role="healing_payload",
            pocket_catalog_version="2026-07-31.1",
            pocket_catalog_sha256=sha256_text("catalog"),
            pocket_key="tie2_binding_interface",
            pocket_evidence_grade="C",
            pocket_conditioning_enabled=False,
            structure_evidence_mode="exploratory_low_confidence_relative_ranking",
            limitations=(
                "exploratory_structure_ranking_only",
                "target_interface_mapping_unqualified",
            ),
        )
    return TargetStructureQualificationEvidence(
        target_key="gyra",
        target_sequence_sha256=sha256_text("gyra-target"),
        target_role="antibiotic_target",
        pocket_catalog_version="2026-07-31.1",
        pocket_catalog_sha256=sha256_text("catalog"),
        pocket_key="lei800_allosteric_dna_surface",
        pocket_evidence_grade="A",
        pocket_conditioning_enabled=True,
        structure_evidence_mode="admitted_target_conditioned_relative_ranking",
        limitations=("relative_computational_evidence_only",),
    )


def _sequence(index: int) -> str:
    return (
        ALPHABET[index % len(ALPHABET)]
        + ALPHABET[(index // len(ALPHABET)) % len(ALPHABET)]
        + "KRWLAKIRKL"
    )


def _quality(index: int, *, family_key: str | None = None) -> SequenceQualityEvidence:
    sequence = _sequence(index)
    return SequenceQualityEvidence(
        target_key="gyra",
        candidate_id=f"candidate-{index:04d}",
        sequence=sequence,
        sequence_sha256=sha256_text(sequence),
        family_key=family_key or f"seqfam80_{index:04d}",
        toxinpred3_label="Non-Toxin",
        macrel_hemolysis_label="low",
        guruprasad_instability_index=20.0 + index / 100,
        activity_model_support_count=2 + index % 2,
        score_receipt_sha256=sha256_text(f"score-{index}"),
    )


def _challenger(
    quality: SequenceQualityEvidence, *, severe_conflict: bool = False
) -> ChallengerReviewEvidence:
    return ChallengerReviewEvidence(
        target_key=quality.target_key,
        sequence_sha256=quality.sequence_sha256,
        review_receipt_sha256=sha256_text(f"challenger-{quality.candidate_id}"),
        verified_models=("hemopi2_v27",),
        missing_verified_runtimes=("apex", "peptiverse"),
        conflict_status=("severe_conflict_unresolved" if severe_conflict else "no_conflict"),
        unresolved_severe_conflict=severe_conflict,
        limitations=("challenger_predictions_are_not_wetlab_measurements",),
    )


def test_instability_hard_gate_accepts_exactly_fifty() -> None:
    boundary = _quality(0).model_copy(update={"guruprasad_instability_index": 50.0})
    above = _quality(0).model_copy(update={"guruprasad_instability_index": 50.000001})

    assert boundary.strict_display_eligible is True
    assert above.strict_display_eligible is False


def _rosetta(
    quality: SequenceQualityEvidence,
    *,
    qualification: TargetStructureQualificationEvidence | None = None,
    primary_dg: float = -10.0,
    nstruct: int = 200,
) -> RosettaDGReceiptEvidence:
    qualification = qualification or _qualification()
    return RosettaDGReceiptEvidence(
        target_key=quality.target_key,
        target_qualification_sha256=qualification.qualification_sha256,
        sequence_sha256=quality.sequence_sha256,
        receipt_sha256=sha256_text(f"receipt-{quality.candidate_id}"),
        result_sha256=sha256_text(f"result-{quality.candidate_id}"),
        manifest_sha256=sha256_text(f"manifest-{quality.candidate_id}"),
        tool_call_id=f"tool-{quality.candidate_id}",
        adapter_version="pepagent-pyrosetta-flexpepdock-v3",
        engine="PyRosetta/FlexPepDock+InterfaceAnalyzer",
        score_function="ref2015",
        unit="REU",
        primary_aggregation=(
            "median_dG_separated_of_all_5_decoys"
            if nstruct == 5
            else "median_dG_separated_of_top_10_reweighted_sc"
        ),
        nstruct=nstruct,
        decoy_count=nstruct,
        decoy_structure_sha256s=tuple(
            sha256_text(f"decoy-{quality.candidate_id}-{index}") for index in range(nstruct)
        ),
        input_sha256=sha256_text(f"input-{quality.candidate_id}"),
        prepared_input_sha256=sha256_text(f"prepared-{quality.candidate_id}"),
        prepacked_input_sha256=sha256_text(f"prepacked-{quality.candidate_id}"),
        primary_dg_separated_reu=primary_dg,
        limitations=(
            "reu_is_not_experimental_affinity",
            "starting_pose_must_be_near_binding_site",
        ),
    )


def test_selects_fifty_family_diverse_candidates_without_weighted_total() -> None:
    quality = [_quality(index) for index in range(60)]
    selection = select_wetlab_gold_candidates(
        target_key="gyra",
        target_qualification=_qualification(),
        sequence_evidence=quality,
        challenger_reviews=[_challenger(item) for item in quality],
        rosetta_receipts=[
            _rosetta(item, primary_dg=-31.0 - index) for index, item in enumerate(quality)
        ],
    )

    assert len(selection.selected) == 50
    assert selection.shortfall == 0
    assert selection.eligible_candidate_count == 60
    assert selection.eligible_family_count == 60
    assert len({item.family_key for item in selection.selected}) == 50
    assert {item.selection_front for item in selection.selected} == {
        "activity_consensus",
        "rosetta_interface",
        "stability",
    }
    assert selection.no_weighted_total_score is True
    assert selection.absolute_affinity_claim_forbidden is True
    assert len(selection.selection_sha256) == 64


def test_reports_shortfall_after_family_and_evidence_gates() -> None:
    quality = [_quality(index, family_key=f"family-{index // 2}") for index in range(12)]
    reviews = [_challenger(item, severe_conflict=index == 0) for index, item in enumerate(quality)]
    receipts = [
        _rosetta(item, primary_dg=-30.0 if index == 1 else -35.0)
        for index, item in enumerate(quality)
    ]

    selection = select_wetlab_gold_candidates(
        target_key="gyra",
        target_qualification=_qualification(),
        sequence_evidence=quality,
        challenger_reviews=reviews,
        rosetta_receipts=receipts,
    )

    assert selection.eligible_candidate_count == 10
    assert selection.eligible_family_count == 5
    assert len(selection.selected) == 5
    assert selection.shortfall == 45


def test_pool_a_uses_strict_minus_thirty_threshold_and_targetless_exemption() -> None:
    quality = _quality(1)

    assert candidate_pool_a_rosetta_gate(
        target_key="gyra",
        rosetta_receipt=_rosetta(quality, primary_dg=-30.0001),
    )
    assert not candidate_pool_a_rosetta_gate(
        target_key="gyra",
        rosetta_receipt=_rosetta(quality, primary_dg=-30.0),
    )
    assert not candidate_pool_a_rosetta_gate(target_key="gyra", rosetta_receipt=None)
    assert candidate_pool_a_rosetta_gate(
        target_key="target-agnostic",
        rosetta_receipt=None,
    )


def test_hidden_pool_s_requires_completed_passing_md_but_does_not_start_it() -> None:
    assert not hidden_pool_s_gate(
        pool_a_eligible=True,
        md_status="not_started",
        md_prespecified_gate_pass=None,
    )
    assert not hidden_pool_s_gate(
        pool_a_eligible=True,
        md_status="succeeded",
        md_prespecified_gate_pass=False,
    )
    assert hidden_pool_s_gate(
        pool_a_eligible=True,
        md_status="succeeded",
        md_prespecified_gate_pass=True,
    )


def test_rejects_incomplete_decision_bearing_rosetta_receipt() -> None:
    quality = _quality(1)
    with pytest.raises(ValidationError, match="greater than or equal to 5"):
        _rosetta(quality, nstruct=4)


def test_accepts_five_decoy_pool_a_receipt() -> None:
    quality = _quality(1)
    receipt = _rosetta(quality, nstruct=5, primary_dg=-31.0)

    assert receipt.primary_aggregation == "median_dG_separated_of_all_5_decoys"
    assert receipt.qualifies_for_candidate_pool_a


def test_rejects_cross_target_evidence() -> None:
    quality = _quality(1)
    review = _challenger(quality).model_copy(update={"target_key": "pbp2a"})

    with pytest.raises(ValueError, match="crossed target branches"):
        select_wetlab_gold_candidates(
            target_key="gyra",
            target_qualification=_qualification(),
            sequence_evidence=[quality],
            challenger_reviews=[review],
            rosetta_receipts=[_rosetta(quality)],
        )


def test_exploratory_target_cannot_silently_claim_admitted_binding_evidence() -> None:
    payload = _qualification(exploratory=True).model_dump(exclude_computed_fields=True)
    payload["structure_evidence_mode"] = "admitted_target_conditioned_relative_ranking"
    with pytest.raises(ValidationError, match="only an antibiotic target"):
        TargetStructureQualificationEvidence.model_validate(payload)
