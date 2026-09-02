from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from pepagent.provenance.hashing import sha256_json, sha256_text

GOLD_CANDIDATE_TARGET = 50
MINIMUM_DECISION_BEARING_ROSETTA_DECOYS = 5
CANDIDATE_POOL_A_DG_THRESHOLD_REU = -30.0
CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
TARGET_AGNOSTIC_KEYS = frozenset({"target-agnostic", "target_agnostic", "agnostic"})


class FrozenEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TargetStructureQualificationEvidence(FrozenEvidence):
    """Frozen interpretation boundary for one target's structure evidence.

    An A/B pocket may be used for target-conditioned relative prioritization.
    A C/D/U interface can still support an exploratory complex/refinement run,
    but never a target-binding claim.  Keeping both modes explicit lets the six
    requested branches advance without silently upgrading weak target evidence.
    """

    schema_version: Literal["ampgent.target-structure-qualification.1"] = (
        "ampgent.target-structure-qualification.1"
    )
    target_key: str = Field(min_length=1)
    target_sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_role: Literal["antibiotic_target", "healing_payload"]
    pocket_catalog_version: str = Field(min_length=1)
    pocket_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pocket_key: str = Field(min_length=1)
    pocket_evidence_grade: Literal["A", "B", "C", "D", "U"]
    pocket_conditioning_enabled: bool
    structure_evidence_mode: Literal[
        "admitted_target_conditioned_relative_ranking",
        "exploratory_low_confidence_relative_ranking",
    ]
    limitations: tuple[str, ...] = Field(min_length=1)
    target_binding_claim_forbidden: Literal[True] = True
    absolute_affinity_claim_forbidden: Literal[True] = True

    @model_validator(mode="after")
    def validate_qualification(self) -> TargetStructureQualificationEvidence:
        if tuple(sorted(set(self.limitations))) != self.limitations:
            raise ValueError("target-structure limitations must be sorted and unique")
        if self.structure_evidence_mode == "admitted_target_conditioned_relative_ranking":
            if self.target_role != "antibiotic_target":
                raise ValueError("only an antibiotic target can use admitted conditioning")
            if self.pocket_evidence_grade not in {"A", "B"}:
                raise ValueError("admitted conditioning requires A/B pocket evidence")
            if not self.pocket_conditioning_enabled:
                raise ValueError("admitted conditioning requires an enabled pocket")
        else:
            if self.target_role != "healing_payload":
                raise ValueError("exploratory structure mode is reserved for healing payloads")
            if self.pocket_evidence_grade not in {"C", "D", "U"}:
                raise ValueError("exploratory structure mode must preserve weak evidence grade")
            if self.pocket_conditioning_enabled:
                raise ValueError("an excluded exploratory pocket cannot be conditioning-enabled")
            required = {
                "exploratory_structure_ranking_only",
                "target_interface_mapping_unqualified",
            }
            if not required.issubset(self.limitations):
                raise ValueError("exploratory structure mode omits mandatory limitations")
        return self

    @computed_field(return_type=str)
    @property
    def qualification_sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json", exclude={"qualification_sha256"}))


class SequenceQualityEvidence(FrozenEvidence):
    """Frozen sequence-level evidence used before structure escalation."""

    schema_version: Literal["ampgent.sequence-quality-evidence.1"] = (
        "ampgent.sequence-quality-evidence.1"
    )
    target_key: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    sequence: str = Field(min_length=1)
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    family_key: str = Field(min_length=1)
    formal_metric_count: Literal[12] = 12
    toxinpred3_label: str = Field(min_length=1)
    macrel_hemolysis_label: str = Field(min_length=1)
    guruprasad_instability_index: float
    activity_model_support_count: int = Field(ge=0, le=3)
    score_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metric_out_of_domain: tuple[str, ...] = ()

    @field_validator("sequence")
    @classmethod
    def normalize_sequence(cls, value: str) -> str:
        normalized = "".join(value.split()).upper()
        if set(normalized) - CANONICAL_AMINO_ACIDS:
            raise ValueError("wetlab candidate sequence contains non-canonical residues")
        return normalized

    @model_validator(mode="after")
    def validate_sequence_evidence(self) -> SequenceQualityEvidence:
        if sha256_text(self.sequence) != self.sequence_sha256:
            raise ValueError("wetlab candidate sequence SHA-256 drifted")
        if not math.isfinite(self.guruprasad_instability_index):
            raise ValueError("Guruprasad instability must be finite")
        if tuple(sorted(set(self.metric_out_of_domain))) != self.metric_out_of_domain:
            raise ValueError("out-of-domain metric names must be sorted and unique")
        return self

    @computed_field(return_type=bool)
    @property
    def strict_display_eligible(self) -> bool:
        return (
            self.toxinpred3_label.strip().lower().replace("_", "-") in {"non-toxin", "nontoxin"}
            and self.macrel_hemolysis_label.strip().lower() == "low"
            and self.guruprasad_instability_index <= 50.0
        )

    @computed_field(return_type=bool)
    @property
    def sequence_quality_eligible(self) -> bool:
        return self.strict_display_eligible and self.activity_model_support_count >= 2


class ChallengerReviewEvidence(FrozenEvidence):
    """Independent-model review; missing runtimes remain explicit limitations."""

    schema_version: Literal["ampgent.challenger-review-evidence.1"] = (
        "ampgent.challenger-review-evidence.1"
    )
    target_key: str = Field(min_length=1)
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verified_models: tuple[str, ...] = Field(min_length=1)
    missing_verified_runtimes: tuple[str, ...] = ()
    conflict_status: Literal[
        "no_conflict",
        "cross_model_disagreement_retained",
        "severe_conflict_unresolved",
    ]
    unresolved_severe_conflict: bool
    candidate_hard_gate_applied: Literal[False] = False
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_review(self) -> ChallengerReviewEvidence:
        if tuple(sorted(set(self.verified_models))) != self.verified_models:
            raise ValueError("verified challenger models must be sorted and unique")
        if tuple(sorted(set(self.missing_verified_runtimes))) != self.missing_verified_runtimes:
            raise ValueError("missing challenger runtimes must be sorted and unique")
        if set(self.verified_models) & set(self.missing_verified_runtimes):
            raise ValueError("one challenger cannot be both verified and missing")
        if tuple(sorted(set(self.limitations))) != self.limitations:
            raise ValueError("challenger limitations must be sorted and unique")
        if self.unresolved_severe_conflict != (
            self.conflict_status == "severe_conflict_unresolved"
        ):
            raise ValueError("challenger conflict status and severe flag disagree")
        return self


class RosettaDGReceiptEvidence(FrozenEvidence):
    """Decision-bearing FlexPepDock/InterfaceAnalyzer completion witness."""

    schema_version: Literal["ampgent.rosetta-dg-receipt-evidence.1"] = (
        "ampgent.rosetta-dg-receipt-evidence.1"
    )
    status: Literal["succeeded"] = "succeeded"
    target_key: str = Field(min_length=1)
    target_qualification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_call_id: str = Field(min_length=1)
    adapter_version: Literal["pepagent-pyrosetta-flexpepdock-v3"]
    engine: Literal["PyRosetta/FlexPepDock+InterfaceAnalyzer"]
    score_function: Literal["ref2015"]
    unit: Literal["REU"]
    primary_aggregation: Literal[
        "median_dG_separated_of_all_5_decoys",
        "median_dG_separated_of_top_10_reweighted_sc",
    ]
    nstruct: int = Field(ge=MINIMUM_DECISION_BEARING_ROSETTA_DECOYS)
    decoy_count: int = Field(ge=MINIMUM_DECISION_BEARING_ROSETTA_DECOYS)
    decoy_structure_sha256s: tuple[str, ...]
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prepared_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prepacked_input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_dg_separated_reu: float
    limitations: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_complete_rosetta_receipt(self) -> RosettaDGReceiptEvidence:
        if self.nstruct == 5 and self.primary_aggregation != "median_dG_separated_of_all_5_decoys":
            raise ValueError("five-decoy Pool A receipts must aggregate all five decoys")
        if self.nstruct > 5 and self.primary_aggregation != "median_dG_separated_of_top_10_reweighted_sc":
            raise ValueError("legacy receipts must use the frozen top-ten aggregation")
        if self.decoy_count != self.nstruct:
            raise ValueError("Rosetta receipt must cover every preregistered decoy")
        if len(self.decoy_structure_sha256s) != self.nstruct:
            raise ValueError("Rosetta receipt is missing decoy structure hashes")
        if len(set(self.decoy_structure_sha256s)) != self.nstruct:
            raise ValueError("Rosetta decoy structure hashes must be unique")
        if any(
            len(value) != 64 or set(value) - set("0123456789abcdef")
            for value in self.decoy_structure_sha256s
        ):
            raise ValueError("Rosetta decoy structure SHA-256 is invalid")
        if not math.isfinite(self.primary_dg_separated_reu):
            raise ValueError("Rosetta primary dG must be finite")
        required_limitations = {
            "reu_is_not_experimental_affinity",
            "starting_pose_must_be_near_binding_site",
        }
        if not required_limitations.issubset(self.limitations):
            raise ValueError("Rosetta receipt omits required interpretation limitations")
        return self

    @computed_field(return_type=bool)
    @property
    def favorable_same_protocol_dg(self) -> bool:
        # This is only a same-protocol directional gate.  It is not kcal/mol or Kd.
        return self.primary_dg_separated_reu < 0.0

    @computed_field(return_type=bool)
    @property
    def qualifies_for_candidate_pool_a(self) -> bool:
        # Pool A uses the frozen, strict same-protocol threshold requested by the user.
        # REU must never be re-labelled as kcal/mol, affinity, or an experimental result.
        return self.primary_dg_separated_reu < CANDIDATE_POOL_A_DG_THRESHOLD_REU


class WetlabGoldCandidate(FrozenEvidence):
    schema_version: Literal["ampgent.wetlab-gold-candidate.1"] = "ampgent.wetlab-gold-candidate.1"
    target_key: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    sequence: str = Field(min_length=1)
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    family_key: str = Field(min_length=1)
    activity_model_support_count: int = Field(ge=2, le=3)
    guruprasad_instability_index: float
    primary_dg_separated_reu: float
    score_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    challenger_review_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rosetta_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_qualification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    structure_evidence_mode: Literal[
        "admitted_target_conditioned_relative_ranking",
        "exploratory_low_confidence_relative_ranking",
    ]
    target_binding_claim_forbidden: Literal[True] = True
    selection_front: Literal["activity_consensus", "rosetta_interface", "stability"]
    candidate_pool: Literal["A"] = "A"
    hidden_pool_s_eligible: Literal[False] = False
    md_status: Literal["not_started"] = "not_started"


class WetlabGoldSelection(FrozenEvidence):
    schema_version: Literal["ampgent.wetlab-gold-selection.1"] = "ampgent.wetlab-gold-selection.1"
    target_key: str = Field(min_length=1)
    target_qualification: TargetStructureQualificationEvidence
    required_count: int = Field(default=GOLD_CANDIDATE_TARGET, ge=GOLD_CANDIDATE_TARGET)
    selected: tuple[WetlabGoldCandidate, ...]
    eligible_candidate_count: int = Field(ge=0)
    eligible_family_count: int = Field(ge=0)
    shortfall: int = Field(ge=0)
    no_weighted_total_score: Literal[True] = True
    absolute_affinity_claim_forbidden: Literal[True] = True
    candidate_pool: Literal["A"] = "A"
    targeted_rosetta_dg_threshold_reu: Literal[-30.0] = CANDIDATE_POOL_A_DG_THRESHOLD_REU
    target_agnostic_rosetta_exempt: Literal[True] = True
    hidden_pool_s_requires_completed_passing_md: Literal[True] = True
    md_started_by_selection: Literal[False] = False

    @computed_field(return_type=str)
    @property
    def selection_sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json", exclude={"selection_sha256"}))


def _index_unique_by_sequence(
    rows: Iterable[ChallengerReviewEvidence | RosettaDGReceiptEvidence],
    *,
    label: str,
) -> dict[str, ChallengerReviewEvidence | RosettaDGReceiptEvidence]:
    result: dict[str, ChallengerReviewEvidence | RosettaDGReceiptEvidence] = {}
    for row in rows:
        if row.sequence_sha256 in result:
            raise ValueError(f"duplicate {label} evidence for one sequence")
        result[row.sequence_sha256] = row
    return result


def candidate_pool_a_rosetta_gate(
    *,
    target_key: str | None,
    rosetta_receipt: RosettaDGReceiptEvidence | None,
) -> bool:
    """Return the frozen Pool-A structure verdict without starting computation.

    Target-agnostic candidates are exempt because no target complex exists.  Every
    targeted candidate requires a complete, identity-matched receipt with strict
    ``dG_separated < -30 REU`` under the admitted aggregation protocol.
    """

    normalized_target = (target_key or "").strip().lower()
    if not normalized_target or normalized_target in TARGET_AGNOSTIC_KEYS:
        return True
    return bool(
        rosetta_receipt is not None
        and rosetta_receipt.target_key.strip().lower() == normalized_target
        and rosetta_receipt.qualifies_for_candidate_pool_a
    )


def hidden_pool_s_gate(
    *,
    pool_a_eligible: bool,
    md_status: Literal["not_started", "running", "succeeded", "failed"],
    md_prespecified_gate_pass: bool | None,
) -> bool:
    """Keep hidden Pool S closed until a Pool-A candidate passes completed MD."""

    return pool_a_eligible and md_status == "succeeded" and md_prespecified_gate_pass is True


def select_wetlab_gold_candidates(
    *,
    target_key: str,
    target_qualification: TargetStructureQualificationEvidence,
    sequence_evidence: Sequence[SequenceQualityEvidence],
    challenger_reviews: Sequence[ChallengerReviewEvidence],
    rosetta_receipts: Sequence[RosettaDGReceiptEvidence],
    required_count: int = GOLD_CANDIDATE_TARGET,
) -> WetlabGoldSelection:
    """Select a family-diverse wetlab shortlist without scalarizing objectives.

    The three independent orderings are consumed round-robin.  A candidate may
    enter only once and at most one sequence may enter from an 80/80 family.
    """

    if required_count < GOLD_CANDIDATE_TARGET:
        raise ValueError("each target requires at least 50 wetlab-gold candidates")
    if not target_key:
        raise ValueError("wetlab-gold selection requires one target key")
    if target_qualification.target_key != target_key:
        raise ValueError("target qualification crossed target branches")
    reviews = _index_unique_by_sequence(challenger_reviews, label="challenger")
    receipts = _index_unique_by_sequence(rosetta_receipts, label="Rosetta")
    quality_by_sha: dict[str, SequenceQualityEvidence] = {}
    for item in sequence_evidence:
        if item.target_key != target_key:
            raise ValueError("sequence evidence crossed target branches")
        if item.sequence_sha256 in quality_by_sha:
            raise ValueError("duplicate sequence-quality evidence")
        quality_by_sha[item.sequence_sha256] = item

    eligible: list[
        tuple[SequenceQualityEvidence, ChallengerReviewEvidence, RosettaDGReceiptEvidence]
    ] = []
    for digest, quality in quality_by_sha.items():
        review = reviews.get(digest)
        receipt = receipts.get(digest)
        if review is None or receipt is None:
            continue
        if review.target_key != target_key or receipt.target_key != target_key:
            raise ValueError("wetlab evidence crossed target branches")
        if receipt.target_qualification_sha256 != target_qualification.qualification_sha256:
            raise ValueError("Rosetta receipt target qualification drifted")
        if (
            quality.sequence_quality_eligible
            and not review.unresolved_severe_conflict
            and candidate_pool_a_rosetta_gate(
                target_key=target_key,
                rosetta_receipt=receipt,
            )
        ):
            eligible.append((quality, review, receipt))

    fronts = {
        "activity_consensus": sorted(
            eligible,
            key=lambda row: (
                -row[0].activity_model_support_count,
                row[2].primary_dg_separated_reu,
                row[0].guruprasad_instability_index,
                row[0].candidate_id,
            ),
        ),
        "rosetta_interface": sorted(
            eligible,
            key=lambda row: (
                row[2].primary_dg_separated_reu,
                -row[0].activity_model_support_count,
                row[0].guruprasad_instability_index,
                row[0].candidate_id,
            ),
        ),
        "stability": sorted(
            eligible,
            key=lambda row: (
                row[0].guruprasad_instability_index,
                row[2].primary_dg_separated_reu,
                -row[0].activity_model_support_count,
                row[0].candidate_id,
            ),
        ),
    }
    cursors = {name: 0 for name in fronts}
    selected: list[WetlabGoldCandidate] = []
    selected_sequences: set[str] = set()
    selected_families: set[str] = set()
    front_names = tuple(fronts)
    while len(selected) < required_count:
        progress = False
        for front_name in front_names:
            rows = fronts[front_name]
            while cursors[front_name] < len(rows):
                quality, review, receipt = rows[cursors[front_name]]
                cursors[front_name] += 1
                if quality.sequence_sha256 in selected_sequences:
                    continue
                if quality.family_key in selected_families:
                    continue
                selected.append(
                    WetlabGoldCandidate(
                        target_key=target_key,
                        candidate_id=quality.candidate_id,
                        sequence=quality.sequence,
                        sequence_sha256=quality.sequence_sha256,
                        family_key=quality.family_key,
                        activity_model_support_count=quality.activity_model_support_count,
                        guruprasad_instability_index=quality.guruprasad_instability_index,
                        primary_dg_separated_reu=receipt.primary_dg_separated_reu,
                        score_receipt_sha256=quality.score_receipt_sha256,
                        challenger_review_receipt_sha256=review.review_receipt_sha256,
                        rosetta_receipt_sha256=receipt.receipt_sha256,
                        target_qualification_sha256=(target_qualification.qualification_sha256),
                        structure_evidence_mode=(target_qualification.structure_evidence_mode),
                        selection_front=front_name,
                    )
                )
                selected_sequences.add(quality.sequence_sha256)
                selected_families.add(quality.family_key)
                progress = True
                break
            if len(selected) >= required_count:
                break
        if not progress:
            break

    eligible_families = {row[0].family_key for row in eligible}
    return WetlabGoldSelection(
        target_key=target_key,
        target_qualification=target_qualification,
        required_count=required_count,
        selected=tuple(selected),
        eligible_candidate_count=len(eligible),
        eligible_family_count=len(eligible_families),
        shortfall=max(required_count - len(selected), 0),
    )


__all__ = [
    "CANDIDATE_POOL_A_DG_THRESHOLD_REU",
    "ChallengerReviewEvidence",
    "GOLD_CANDIDATE_TARGET",
    "MINIMUM_DECISION_BEARING_ROSETTA_DECOYS",
    "RosettaDGReceiptEvidence",
    "SequenceQualityEvidence",
    "TargetStructureQualificationEvidence",
    "WetlabGoldCandidate",
    "WetlabGoldSelection",
    "candidate_pool_a_rosetta_gate",
    "hidden_pool_s_gate",
    "select_wetlab_gold_candidates",
]
