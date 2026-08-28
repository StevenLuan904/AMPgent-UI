from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    computed_field,
    field_validator,
    model_validator,
)

from pepagent.provenance.hashing import sha256_json, sha256_text

CANONICAL_AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
MINIMUM_PEPTIDE_LENGTH = 10
MAXIMUM_PEPTIDE_LENGTH = 30
OOD_QUALIFIED_MINIMUM_PEPTIDE_LENGTH = 20

ArchiveName = Literal[
    "activity_consensus",
    "amp_read_endpoint",
    "llamp_endpoint",
    "macrel_endpoint",
    "activity_safety_balance",
    "stability_degradation",
    "novel_family",
    "model_disagreement",
]
ARCHIVE_NAMES: tuple[ArchiveName, ...] = (
    "activity_consensus",
    "amp_read_endpoint",
    "llamp_endpoint",
    "macrel_endpoint",
    "activity_safety_balance",
    "stability_degradation",
    "novel_family",
    "model_disagreement",
)
FORBIDDEN_SCALAR_METRIC_NAMES = frozenset(
    {"weighted_score", "weighted_total", "total_score", "composite_score"}
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricObservation(FrozenModel):
    numeric_value: float | None
    direction: Literal["minimize", "maximize"]
    unit: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: Literal["succeeded", "failed", "unavailable"] = "succeeded"
    out_of_domain: bool = False

    @model_validator(mode="after")
    def validate_numeric_value(self) -> MetricObservation:
        if self.status == "succeeded":
            if self.numeric_value is None or not math.isfinite(self.numeric_value):
                raise ValueError("a succeeded metric requires one finite numeric value")
        elif self.numeric_value is not None and not math.isfinite(self.numeric_value):
            raise ValueError("metric numeric values must be finite")
        return self


class CandidateEvidence(FrozenModel):
    candidate_id: str = Field(min_length=1)
    sequence: str = Field(min_length=MINIMUM_PEPTIDE_LENGTH, max_length=MAXIMUM_PEPTIDE_LENGTH)
    sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    family_key: str = Field(min_length=1)
    metrics: dict[str, MetricObservation]
    archive_eligible: bool = True

    @field_validator("sequence")
    @classmethod
    def normalize_sequence(cls, value: str) -> str:
        normalized = "".join(value.split()).upper()
        if not MINIMUM_PEPTIDE_LENGTH <= len(normalized) <= MAXIMUM_PEPTIDE_LENGTH:
            raise ValueError("candidate is outside the frozen short-peptide length range")
        if set(normalized) - CANONICAL_AMINO_ACIDS:
            raise ValueError("candidate contains non-canonical amino acids")
        return normalized

    @model_validator(mode="after")
    def validate_identity_and_metrics(self) -> CandidateEvidence:
        if sha256_text(self.sequence) != self.sequence_sha256:
            raise ValueError("candidate sequence SHA-256 drifted")
        forbidden = FORBIDDEN_SCALAR_METRIC_NAMES & set(self.metrics)
        if forbidden:
            raise ValueError(f"weighted scalar metrics are forbidden: {sorted(forbidden)}")
        return self


def is_ood_qualified_wetlab_candidate(candidate: CandidateEvidence) -> bool:
    """Return whether a literal hard-gate candidate is usable for wet-lab selection.

    Guruprasad values below 20 aa are explicitly OOD and therefore cannot satisfy
    the wet-lab quota even when the numeric instability value is below 50.
    """

    instability = candidate.metrics.get("guruprasad_instability_index")
    return bool(
        candidate.archive_eligible
        and OOD_QUALIFIED_MINIMUM_PEPTIDE_LENGTH
        <= len(candidate.sequence)
        <= MAXIMUM_PEPTIDE_LENGTH
        and instability is not None
        and instability.status == "succeeded"
        and not instability.out_of_domain
    )


class ArchiveObjective(FrozenModel):
    metric_name: str = Field(min_length=1)
    direction: Literal["minimize", "maximize"]


DEFAULT_ACTIVITY_OBJECTIVES = (
    ArchiveObjective(metric_name="amp_read_log10_mic_um", direction="minimize"),
    ArchiveObjective(metric_name="llamp_log10_mic_um", direction="minimize"),
    ArchiveObjective(metric_name="macrel_amp_probability", direction="maximize"),
)
DEFAULT_SAFETY_OBJECTIVES = (
    ArchiveObjective(metric_name="macrel_hemolysis_probability", direction="minimize"),
    ArchiveObjective(metric_name="toxinpred3_hybrid_score", direction="minimize"),
)
DEFAULT_STABILITY_OBJECTIVE = ArchiveObjective(
    metric_name="guruprasad_instability_index", direction="minimize"
)


class MultiFrontArchivePolicy(FrozenModel):
    schema_version: Literal["ampgent.autoresearch-archive-policy.1"] = (
        "ampgent.autoresearch-archive-policy.1"
    )
    activity_objectives: tuple[ArchiveObjective, ...] = DEFAULT_ACTIVITY_OBJECTIVES
    safety_objectives: tuple[ArchiveObjective, ...] = DEFAULT_SAFETY_OBJECTIVES
    stability_objective: ArchiveObjective = DEFAULT_STABILITY_OBJECTIVE
    consensus_rank_fraction: float = Field(default=0.5, ge=0, le=1)
    endpoint_rank_fraction: float = Field(default=0.1, ge=0, le=1)
    model_disagreement_rank_span: float = Field(default=0.5, ge=0, le=1)
    known_family_keys: tuple[str, ...] = ()
    no_weighted_total_score: Literal[True] = True

    @model_validator(mode="after")
    def validate_policy(self) -> MultiFrontArchivePolicy:
        activity_names = [item.metric_name for item in self.activity_objectives]
        if len(activity_names) != 3 or len(activity_names) != len(set(activity_names)):
            raise ValueError("activity archive requires exactly three unique model objectives")
        all_names = activity_names + [item.metric_name for item in self.safety_objectives]
        all_names.append(self.stability_objective.metric_name)
        if FORBIDDEN_SCALAR_METRIC_NAMES & set(all_names):
            raise ValueError("archive policy cannot consume a weighted scalar score")
        if tuple(sorted(set(self.known_family_keys))) != self.known_family_keys:
            raise ValueError("known family keys must be sorted and unique")
        return self

    def sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json"))


class MultiFrontArchiveSnapshot(FrozenModel):
    schema_version: Literal["ampgent.autoresearch-archive-snapshot.1"] = (
        "ampgent.autoresearch-archive-snapshot.1"
    )
    generation: int = Field(ge=0)
    policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_candidate_ids: tuple[str, ...]
    archive_members: dict[str, tuple[str, ...]]
    member_reasons: dict[str, dict[str, str]]
    known_family_keys: tuple[str, ...]

    @model_validator(mode="after")
    def validate_archive(self) -> MultiFrontArchiveSnapshot:
        expected = set(ARCHIVE_NAMES)
        if set(self.archive_members) != expected or set(self.member_reasons) != expected:
            raise ValueError("archive snapshot must contain every frozen multi-front lane")
        source_ids = set(self.source_candidate_ids)
        if len(source_ids) != len(self.source_candidate_ids):
            raise ValueError("archive source candidate IDs must be unique")
        for name in ARCHIVE_NAMES:
            members = self.archive_members[name]
            if tuple(sorted(set(members))) != members:
                raise ValueError(f"archive members must be sorted and unique: {name}")
            if not set(members).issubset(source_ids):
                raise ValueError(f"archive contains an unknown candidate: {name}")
            if set(self.member_reasons[name]) != set(members):
                raise ValueError(f"archive member reasons differ from membership: {name}")
        return self

    @computed_field(return_type=str)
    @property
    def archive_sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json", exclude={"archive_sha256"}))


def parse_persisted_archive_snapshot(value: Mapping[str, Any]) -> MultiFrontArchiveSnapshot:
    """Validate a persisted snapshot and its redundant content-hash witness."""

    payload = dict(value)
    claimed_sha256 = payload.pop("archive_sha256", None)
    snapshot = MultiFrontArchiveSnapshot.model_validate(payload)
    if claimed_sha256 is not None:
        if not isinstance(claimed_sha256, str) or claimed_sha256 != snapshot.archive_sha256:
            raise ValueError("persisted AutoResearch archive SHA-256 witness drifted")
    return snapshot


def _metric_value(
    candidate: CandidateEvidence, objective: ArchiveObjective
) -> float | None:
    observation = candidate.metrics.get(objective.metric_name)
    if (
        observation is None
        or observation.status != "succeeded"
        or observation.numeric_value is None
        or observation.direction != objective.direction
    ):
        return None
    return float(observation.numeric_value)


def _objective_vector(
    candidate: CandidateEvidence, objectives: Sequence[ArchiveObjective]
) -> tuple[float, ...] | None:
    values: list[float] = []
    for objective in objectives:
        value = _metric_value(candidate, objective)
        if value is None:
            return None
        values.append(value if objective.direction == "minimize" else -value)
    return tuple(values)


def _dominates(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
    return all(left <= right for left, right in zip(first, second, strict=True)) and any(
        left < right for left, right in zip(first, second, strict=True)
    )


def _pareto_front_ids(
    candidates: Sequence[CandidateEvidence], objectives: Sequence[ArchiveObjective]
) -> tuple[str, ...]:
    vectors = {
        candidate.candidate_id: vector
        for candidate in candidates
        if (vector := _objective_vector(candidate, objectives)) is not None
    }
    return tuple(
        sorted(
            candidate_id
            for candidate_id, vector in vectors.items()
            if not any(
                other_id != candidate_id and _dominates(other, vector)
                for other_id, other in vectors.items()
            )
        )
    )


def _rank_fractions(
    candidates: Sequence[CandidateEvidence], objective: ArchiveObjective
) -> dict[str, float]:
    values = {
        candidate.candidate_id: value
        for candidate in candidates
        if (value := _metric_value(candidate, objective)) is not None
    }
    if not values:
        return {}
    denominator = max(len(values) - 1, 1)
    fractions: dict[str, float] = {}
    for candidate_id, value in values.items():
        if objective.direction == "minimize":
            better = sum(other < value for other in values.values())
        else:
            better = sum(other > value for other in values.values())
        fractions[candidate_id] = better / denominator
    return fractions


def build_multi_front_archive(
    candidates: Sequence[CandidateEvidence],
    policy: MultiFrontArchivePolicy,
    *,
    generation: int,
) -> MultiFrontArchiveSnapshot:
    """Build overlapping, non-scalarized fronts from one globally deduplicated cohort."""

    ordered = sorted(candidates, key=lambda item: item.candidate_id)
    ids = [item.candidate_id for item in ordered]
    sequence_hashes = [item.sequence_sha256 for item in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("archive candidates must have unique IDs")
    if len(sequence_hashes) != len(set(sequence_hashes)):
        raise ValueError("archive candidates must be globally sequence-deduplicated")
    eligible = [item for item in ordered if item.archive_eligible]
    activity_ranks = {
        objective.metric_name: _rank_fractions(eligible, objective)
        for objective in policy.activity_objectives
    }

    consensus_candidates = [
        candidate
        for candidate in eligible
        if all(
            candidate.candidate_id in activity_ranks[objective.metric_name]
            and activity_ranks[objective.metric_name][candidate.candidate_id]
            <= policy.consensus_rank_fraction
            for objective in policy.activity_objectives
        )
    ]
    consensus_ids = _pareto_front_ids(consensus_candidates, policy.activity_objectives)

    endpoint_names: tuple[ArchiveName, ...] = (
        "amp_read_endpoint",
        "llamp_endpoint",
        "macrel_endpoint",
    )
    endpoint_members: dict[ArchiveName, tuple[str, ...]] = {}
    for archive_name, objective in zip(
        endpoint_names, policy.activity_objectives, strict=True
    ):
        ranks = activity_ranks[objective.metric_name]
        endpoint_members[archive_name] = tuple(
            sorted(
                candidate_id
                for candidate_id, rank_fraction in ranks.items()
                if rank_fraction <= policy.endpoint_rank_fraction
            )
        )

    balance_objectives = (*policy.activity_objectives, *policy.safety_objectives)
    stability_objectives = (*policy.activity_objectives, policy.stability_objective)
    balance_ids = _pareto_front_ids(eligible, balance_objectives)
    stability_ids = _pareto_front_ids(eligible, stability_objectives)
    known_families = set(policy.known_family_keys)
    novel_ids = tuple(
        sorted(
            candidate.candidate_id
            for candidate in eligible
            if candidate.family_key not in known_families
        )
    )
    disagreement_ids: list[str] = []
    for candidate in eligible:
        ranks = [
            activity_ranks[objective.metric_name].get(candidate.candidate_id)
            for objective in policy.activity_objectives
        ]
        if any(rank is None for rank in ranks):
            continue
        numeric_ranks = [float(rank) for rank in ranks if rank is not None]
        if max(numeric_ranks) - min(numeric_ranks) >= policy.model_disagreement_rank_span:
            disagreement_ids.append(candidate.candidate_id)

    archive_members: dict[str, tuple[str, ...]] = {
        "activity_consensus": consensus_ids,
        **endpoint_members,
        "activity_safety_balance": balance_ids,
        "stability_degradation": stability_ids,
        "novel_family": novel_ids,
        "model_disagreement": tuple(sorted(disagreement_ids)),
    }
    reasons: dict[str, dict[str, str]] = {
        "activity_consensus": {
            candidate_id: "within_each_activity_model_rank_band_and_nondominated"
            for candidate_id in consensus_ids
        },
        "amp_read_endpoint": {
            candidate_id: "amp_read_single_model_endpoint"
            for candidate_id in endpoint_members["amp_read_endpoint"]
        },
        "llamp_endpoint": {
            candidate_id: "llamp_single_model_endpoint"
            for candidate_id in endpoint_members["llamp_endpoint"]
        },
        "macrel_endpoint": {
            candidate_id: "macrel_single_model_endpoint"
            for candidate_id in endpoint_members["macrel_endpoint"]
        },
        "activity_safety_balance": {
            candidate_id: "nondominated_on_activity_and_safety_axes"
            for candidate_id in balance_ids
        },
        "stability_degradation": {
            candidate_id: "nondominated_on_activity_and_instability_axes"
            for candidate_id in stability_ids
        },
        "novel_family": {
            candidate_id: "family_absent_from_frozen_known_family_set"
            for candidate_id in novel_ids
        },
        "model_disagreement": {
            candidate_id: "activity_model_rank_span_exceeds_frozen_threshold"
            for candidate_id in disagreement_ids
        },
    }
    return MultiFrontArchiveSnapshot(
        generation=generation,
        policy_sha256=policy.sha256(),
        source_candidate_ids=tuple(ids),
        archive_members=archive_members,
        member_reasons=reasons,
        known_family_keys=policy.known_family_keys,
    )


class ResidueSubstitution(FrozenModel):
    position_zero_based: int = Field(ge=0)
    from_residue: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")
    to_residue: str = Field(pattern=r"^[ACDEFGHIKLMNPQRSTVWY]$")

    @model_validator(mode="after")
    def validate_edit(self) -> ResidueSubstitution:
        if self.from_residue == self.to_residue:
            raise ValueError("a substitution must change its residue")
        return self


class CrossoverFragment(FrozenModel):
    source_role: Literal["primary_parent", "donor_parent"]
    source_start_zero_based: int = Field(ge=0)
    source_end_exclusive: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_fragment(self) -> CrossoverFragment:
        if self.source_end_exclusive <= self.source_start_zero_based:
            raise ValueError("crossover fragment end must follow its start")
        return self


class EvolutionActionBase(FrozenModel):
    schema_version: Literal["ampgent.autoresearch-action.1"] = (
        "ampgent.autoresearch-action.1"
    )
    action_type: str
    branch_key: str = Field(min_length=1)
    generation: int = Field(ge=1)
    seed: int = Field(ge=0)
    operator_id: str = Field(min_length=1)
    operator_release_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_improvement_metrics: tuple[str, ...] = Field(min_length=1)
    protected_metrics: tuple[str, ...] = Field(min_length=1)
    evidence_sha256s: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_common_action(self) -> EvolutionActionBase:
        for name, values in (
            ("expected improvement metrics", self.expected_improvement_metrics),
            ("protected metrics", self.protected_metrics),
            ("evidence SHA-256s", self.evidence_sha256s),
        ):
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"{name} must be sorted and unique")
        if set(self.expected_improvement_metrics) & set(self.protected_metrics):
            raise ValueError("expected and protected metric sets must be disjoint")
        if any(
            len(value) != 64 or set(value) - set("0123456789abcdef")
            for value in self.evidence_sha256s
        ):
            raise ValueError("action evidence SHA-256 is invalid")
        if FORBIDDEN_SCALAR_METRIC_NAMES & (
            set(self.expected_improvement_metrics) | set(self.protected_metrics)
        ):
            raise ValueError("actions cannot optimize or protect a weighted scalar score")
        return self

    @computed_field(return_type=str)
    @property
    def action_sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json", exclude={"action_sha256"}))


class MaskedSubstitutionAction(EvolutionActionBase):
    action_type: Literal["masked_substitution"] = "masked_substitution"
    parent_candidate_id: str = Field(min_length=1)
    parent_sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    substitutions: tuple[ResidueSubstitution, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_substitutions(self) -> MaskedSubstitutionAction:
        positions = tuple(item.position_zero_based for item in self.substitutions)
        if tuple(sorted(set(positions))) != positions:
            raise ValueError("masked substitution positions must be sorted and unique")
        return self


class ControlledCrossoverAction(EvolutionActionBase):
    action_type: Literal["controlled_crossover"] = "controlled_crossover"
    parent_candidate_id: str = Field(min_length=1)
    parent_sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    donor_candidate_id: str = Field(min_length=1)
    donor_sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fragments: tuple[CrossoverFragment, ...] = Field(min_length=2)
    post_crossover_substitutions: tuple[ResidueSubstitution, ...] = ()

    @model_validator(mode="after")
    def validate_crossover(self) -> ControlledCrossoverAction:
        if self.parent_candidate_id == self.donor_candidate_id:
            raise ValueError("controlled crossover requires two distinct parents")
        if {item.source_role for item in self.fragments} != {
            "primary_parent",
            "donor_parent",
        }:
            raise ValueError("controlled crossover must consume both parent and donor")
        positions = tuple(
            item.position_zero_based for item in self.post_crossover_substitutions
        )
        if tuple(sorted(set(positions))) != positions:
            raise ValueError("post-crossover substitution positions must be sorted and unique")
        return self


class DeNovoAction(EvolutionActionBase):
    action_type: Literal["de_novo"] = "de_novo"
    peptide_length: int = Field(ge=MINIMUM_PEPTIDE_LENGTH, le=MAXIMUM_PEPTIDE_LENGTH)
    proposed_sequence: str

    @field_validator("proposed_sequence")
    @classmethod
    def normalize_proposed_sequence(cls, value: str) -> str:
        normalized = "".join(value.split()).upper()
        if set(normalized) - CANONICAL_AMINO_ACIDS:
            raise ValueError("de-novo proposal contains non-canonical amino acids")
        return normalized

    @model_validator(mode="after")
    def validate_de_novo_length(self) -> DeNovoAction:
        if len(self.proposed_sequence) != self.peptide_length:
            raise ValueError("de-novo proposal length differs from its frozen action")
        return self


class PepMLMCrossoverWindow(FrozenModel):
    """One-based inclusive donor-segment replacement understood by PepMLM."""

    primary_start: int = Field(ge=1)
    primary_end: int = Field(ge=1)
    donor_start: int = Field(ge=1)
    donor_end: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_window(self) -> PepMLMCrossoverWindow:
        if self.primary_end < self.primary_start:
            raise ValueError("PepMLM primary crossover end precedes its start")
        if self.donor_end < self.donor_start:
            raise ValueError("PepMLM donor crossover end precedes its start")
        return self


class PepMLMTargetedAction(EvolutionActionBase):
    """A target-conditioned action whose exact residues are materialized by PepMLM.

    The action freezes every sampling control and every biological source before
    execution.  The generated sequence remains an executor result rather than being
    smuggled into the planning action.
    """

    action_type: Literal["pepmlm_targeted"] = "pepmlm_targeted"
    target_sequence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_mode: Literal[
        "masked_substitution", "controlled_crossover", "de_novo"
    ]
    parent_candidate_id: str | None = None
    parent_sequence_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    parent_length: int | None = Field(
        default=None, ge=MINIMUM_PEPTIDE_LENGTH, le=MAXIMUM_PEPTIDE_LENGTH
    )
    donor_candidate_id: str | None = None
    donor_sequence_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    donor_length: int | None = Field(
        default=None, ge=MINIMUM_PEPTIDE_LENGTH, le=MAXIMUM_PEPTIDE_LENGTH
    )
    mutation_positions_one_based: tuple[int, ...] = ()
    crossover: PepMLMCrossoverWindow | None = None
    peptide_length: int | None = Field(
        default=None, ge=MINIMUM_PEPTIDE_LENGTH, le=MAXIMUM_PEPTIDE_LENGTH
    )
    top_k: int = Field(default=5, ge=1, le=20)
    temperature: float = Field(default=1.0, gt=0, le=10)
    max_attempts: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_targeted_action(self) -> PepMLMTargetedAction:
        positions = self.mutation_positions_one_based
        if tuple(sorted(set(positions))) != positions:
            raise ValueError("PepMLM mutation positions must be sorted and unique")
        has_parent = all(
            value is not None
            for value in (
                self.parent_candidate_id,
                self.parent_sequence_sha256,
                self.parent_length,
            )
        )
        has_donor = all(
            value is not None
            for value in (
                self.donor_candidate_id,
                self.donor_sequence_sha256,
                self.donor_length,
            )
        )
        if self.proposal_mode == "masked_substitution":
            if not has_parent or has_donor or self.crossover is not None:
                raise ValueError("PepMLM masked substitution requires only one parent")
            if not positions or self.peptide_length is not None:
                raise ValueError("PepMLM masked substitution requires explicit positions")
            assert self.parent_length is not None
            if positions[-1] > self.parent_length:
                raise ValueError("PepMLM mutation position lies outside its parent")
        elif self.proposal_mode == "controlled_crossover":
            if not has_parent or not has_donor or self.crossover is None:
                raise ValueError("PepMLM controlled crossover requires parent and donor")
            if self.parent_candidate_id == self.donor_candidate_id:
                raise ValueError("PepMLM controlled crossover requires distinct parents")
            if self.peptide_length is not None:
                raise ValueError("PepMLM crossover length is derived from its sources")
            assert self.parent_length is not None and self.donor_length is not None
            if self.crossover.primary_end > self.parent_length:
                raise ValueError("PepMLM primary crossover window lies outside its parent")
            if self.crossover.donor_end > self.donor_length:
                raise ValueError("PepMLM donor crossover window lies outside its donor")
            child_length = (
                self.parent_length
                - (self.crossover.primary_end - self.crossover.primary_start + 1)
                + (self.crossover.donor_end - self.crossover.donor_start + 1)
            )
            if not MINIMUM_PEPTIDE_LENGTH <= child_length <= MAXIMUM_PEPTIDE_LENGTH:
                raise ValueError("PepMLM crossover child is outside the peptide length range")
            if positions and positions[-1] > child_length:
                raise ValueError("PepMLM refinement position lies outside the crossover child")
        else:
            if has_parent or has_donor or positions or self.crossover is not None:
                raise ValueError("PepMLM de-novo action cannot declare biological parents")
            if self.peptide_length is None:
                raise ValueError("PepMLM de-novo action requires a peptide length")
        return self


EvolutionAction = Annotated[
    MaskedSubstitutionAction
    | ControlledCrossoverAction
    | DeNovoAction
    | PepMLMTargetedAction,
    Field(discriminator="action_type"),
]
_ACTION_ADAPTER = TypeAdapter(EvolutionAction)


def parse_evolution_action(payload: Mapping[str, Any]) -> EvolutionAction:
    """Validate a serialized action and verify its embedded replay identity."""

    normalized = dict(payload)
    claimed_sha = normalized.pop("action_sha256", None)
    action = _ACTION_ADAPTER.validate_python(normalized)
    if claimed_sha is not None and claimed_sha != action.action_sha256:
        raise ValueError("evolution action SHA-256 drifted")
    return action


def _require_candidate(
    candidates_by_id: Mapping[str, CandidateEvidence],
    candidate_id: str,
    expected_sha256: str,
    role: str,
) -> CandidateEvidence:
    candidate = candidates_by_id.get(candidate_id)
    if candidate is None or candidate.sequence_sha256 != expected_sha256:
        raise ValueError(f"{role} candidate identity drifted")
    return candidate


def _apply_substitutions(
    sequence: str, substitutions: Sequence[ResidueSubstitution]
) -> str:
    residues = list(sequence)
    for substitution in substitutions:
        position = substitution.position_zero_based
        if position >= len(residues):
            raise ValueError("substitution position is outside the peptide")
        if residues[position] != substitution.from_residue:
            raise ValueError("substitution source residue differs from the frozen action")
        residues[position] = substitution.to_residue
    return "".join(residues)


def apply_evolution_action(
    action: EvolutionAction,
    candidates_by_id: Mapping[str, CandidateEvidence],
) -> str:
    """Replay a frozen action without consulting mutable policy or random state."""

    if isinstance(action, MaskedSubstitutionAction):
        parent = _require_candidate(
            candidates_by_id,
            action.parent_candidate_id,
            action.parent_sequence_sha256,
            "primary parent",
        )
        child = _apply_substitutions(parent.sequence, action.substitutions)
    elif isinstance(action, ControlledCrossoverAction):
        parent = _require_candidate(
            candidates_by_id,
            action.parent_candidate_id,
            action.parent_sequence_sha256,
            "primary parent",
        )
        donor = _require_candidate(
            candidates_by_id,
            action.donor_candidate_id,
            action.donor_sequence_sha256,
            "donor parent",
        )
        sources = {"primary_parent": parent.sequence, "donor_parent": donor.sequence}
        pieces: list[str] = []
        for fragment in action.fragments:
            source = sources[fragment.source_role]
            if fragment.source_end_exclusive > len(source):
                raise ValueError("crossover fragment is outside its source peptide")
            pieces.append(
                source[
                    fragment.source_start_zero_based : fragment.source_end_exclusive
                ]
            )
        child = _apply_substitutions(
            "".join(pieces), action.post_crossover_substitutions
        )
    elif isinstance(action, DeNovoAction):
        child = action.proposed_sequence
    else:
        raise ValueError("PepMLM-targeted action requires its frozen model executor")
    if not MINIMUM_PEPTIDE_LENGTH <= len(child) <= MAXIMUM_PEPTIDE_LENGTH:
        raise ValueError("action produced a peptide outside the frozen length range")
    if set(child) - CANONICAL_AMINO_ACIDS:
        raise ValueError("action produced non-canonical amino acids")
    return child


def validate_action_child(
    action: EvolutionAction,
    candidates_by_id: Mapping[str, CandidateEvidence],
    observed_sequence: str,
) -> str:
    normalized = "".join(observed_sequence.split()).upper()
    if isinstance(action, PepMLMTargetedAction):
        if set(normalized) - CANONICAL_AMINO_ACIDS:
            raise ValueError("PepMLM-targeted child contains non-canonical residues")
        if action.proposal_mode == "de_novo":
            if len(normalized) != action.peptide_length:
                raise ValueError("PepMLM-targeted child length differs from its action")
            return normalized
        assert action.parent_candidate_id is not None
        assert action.parent_sequence_sha256 is not None
        parent = _require_candidate(
            candidates_by_id,
            action.parent_candidate_id,
            action.parent_sequence_sha256,
            "primary parent",
        )
        if action.proposal_mode == "masked_substitution":
            if len(normalized) != len(parent.sequence):
                raise ValueError("PepMLM-targeted child length differs from its parent")
            mutable = {item - 1 for item in action.mutation_positions_one_based}
            if any(
                residue != parent.sequence[index]
                for index, residue in enumerate(normalized)
                if index not in mutable
            ):
                raise ValueError("PepMLM changed a residue outside the frozen positions")
            if normalized == parent.sequence:
                raise ValueError("PepMLM-targeted substitution did not change its parent")
            return normalized
        assert action.donor_candidate_id is not None
        assert action.donor_sequence_sha256 is not None
        assert action.crossover is not None
        donor = _require_candidate(
            candidates_by_id,
            action.donor_candidate_id,
            action.donor_sequence_sha256,
            "donor parent",
        )
        window = action.crossover
        base = (
            parent.sequence[: window.primary_start - 1]
            + donor.sequence[window.donor_start - 1 : window.donor_end]
            + parent.sequence[window.primary_end :]
        )
        if len(normalized) != len(base):
            raise ValueError("PepMLM-targeted crossover child length drifted")
        mutable = {item - 1 for item in action.mutation_positions_one_based}
        if any(
            residue != base[index]
            for index, residue in enumerate(normalized)
            if index not in mutable
        ):
            raise ValueError("PepMLM changed crossover residues outside frozen positions")
        if normalized == parent.sequence:
            raise ValueError("PepMLM-targeted crossover did not change its parent")
        return normalized
    expected = apply_evolution_action(action, candidates_by_id)
    if normalized != expected:
        raise ValueError("observed child does not replay from the frozen action")
    return normalized


class MetricDelta(FrozenModel):
    metric_name: str
    comparable: bool
    reason: str
    direction: Literal["minimize", "maximize"] | None = None
    unit: str | None = None
    version: str | None = None
    parent_value: float | None = None
    child_value: float | None = None
    raw_delta_child_minus_parent: float | None = None
    improvement_delta: float | None = None
    out_of_domain: bool | None = None


class BaselineMetricDeltas(FrozenModel):
    baseline_role: Literal["primary_parent", "donor_parent"]
    baseline_candidate_id: str
    metrics: tuple[MetricDelta, ...]


class ParentChildDelta(FrozenModel):
    schema_version: Literal["ampgent.autoresearch-parent-child-delta.1"] = (
        "ampgent.autoresearch-parent-child-delta.1"
    )
    action_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    action_type: Literal[
        "masked_substitution", "controlled_crossover", "de_novo", "pepmlm_targeted"
    ]
    child_candidate_id: str
    baselines: tuple[BaselineMetricDeltas, ...]
    expected_metrics_improved: tuple[str, ...]
    expected_metrics_regressed: tuple[str, ...]
    expected_metrics_incomparable: tuple[str, ...]
    protected_metrics_regressed: tuple[str, ...]

    @computed_field(return_type=str)
    @property
    def delta_sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json", exclude={"delta_sha256"}))


def _compare_metric(
    metric_name: str,
    parent: MetricObservation | None,
    child: MetricObservation | None,
) -> MetricDelta:
    if parent is None or child is None:
        return MetricDelta(
            metric_name=metric_name,
            comparable=False,
            reason="metric_missing_from_parent_or_child",
        )
    if parent.status != "succeeded" or child.status != "succeeded":
        return MetricDelta(
            metric_name=metric_name,
            comparable=False,
            reason="metric_status_is_not_succeeded",
        )
    if (
        parent.version != child.version
        or parent.unit != child.unit
        or parent.direction != child.direction
        or parent.out_of_domain != child.out_of_domain
    ):
        return MetricDelta(
            metric_name=metric_name,
            comparable=False,
            reason="metric_version_unit_direction_or_ood_semantics_differ",
        )
    assert parent.numeric_value is not None and child.numeric_value is not None
    raw_delta = float(child.numeric_value) - float(parent.numeric_value)
    improvement = raw_delta if child.direction == "maximize" else -raw_delta
    return MetricDelta(
        metric_name=metric_name,
        comparable=True,
        reason="same_metric_contract",
        direction=child.direction,
        unit=child.unit,
        version=child.version,
        parent_value=float(parent.numeric_value),
        child_value=float(child.numeric_value),
        raw_delta_child_minus_parent=raw_delta,
        improvement_delta=improvement,
        out_of_domain=child.out_of_domain,
    )


def compute_parent_child_delta(
    action: EvolutionAction,
    child: CandidateEvidence,
    candidates_by_id: Mapping[str, CandidateEvidence],
) -> ParentChildDelta:
    """Compare a replay-validated child with every declared biological parent."""

    validate_action_child(action, candidates_by_id, child.sequence)
    baseline_specs: list[tuple[Literal["primary_parent", "donor_parent"], str]] = []
    if isinstance(action, (MaskedSubstitutionAction, ControlledCrossoverAction)) or (
        isinstance(action, PepMLMTargetedAction)
        and action.proposal_mode in {"masked_substitution", "controlled_crossover"}
    ):
        assert action.parent_candidate_id is not None
        baseline_specs.append(("primary_parent", action.parent_candidate_id))
    if isinstance(action, ControlledCrossoverAction) or (
        isinstance(action, PepMLMTargetedAction)
        and action.proposal_mode == "controlled_crossover"
    ):
        assert action.donor_candidate_id is not None
        baseline_specs.append(("donor_parent", action.donor_candidate_id))
    baselines: list[BaselineMetricDeltas] = []
    primary_metrics: dict[str, MetricDelta] = {}
    for role, candidate_id in baseline_specs:
        baseline = candidates_by_id[candidate_id]
        metric_names = sorted(set(baseline.metrics) | set(child.metrics))
        deltas = tuple(
            _compare_metric(
                metric_name,
                baseline.metrics.get(metric_name),
                child.metrics.get(metric_name),
            )
            for metric_name in metric_names
        )
        baselines.append(
            BaselineMetricDeltas(
                baseline_role=role,
                baseline_candidate_id=candidate_id,
                metrics=deltas,
            )
        )
        if role == "primary_parent":
            primary_metrics = {item.metric_name: item for item in deltas}

    improved: list[str] = []
    regressed: list[str] = []
    incomparable: list[str] = []
    for metric_name in action.expected_improvement_metrics:
        delta = primary_metrics.get(metric_name)
        if delta is None or not delta.comparable or delta.improvement_delta is None:
            incomparable.append(metric_name)
        elif delta.improvement_delta > 0:
            improved.append(metric_name)
        elif delta.improvement_delta < 0:
            regressed.append(metric_name)
    protected_regressed = sorted(
        metric_name
        for metric_name in action.protected_metrics
        if (delta := primary_metrics.get(metric_name)) is not None
        and delta.comparable
        and delta.improvement_delta is not None
        and delta.improvement_delta < 0
    )
    return ParentChildDelta(
        action_sha256=action.action_sha256,
        action_type=action.action_type,
        child_candidate_id=child.candidate_id,
        baselines=tuple(baselines),
        expected_metrics_improved=tuple(sorted(improved)),
        expected_metrics_regressed=tuple(sorted(regressed)),
        expected_metrics_incomparable=tuple(sorted(incomparable)),
        protected_metrics_regressed=tuple(protected_regressed),
    )


class ContinuationPolicy(FrozenModel):
    schema_version: Literal["ampgent.autoresearch-continuation-policy.1"] = (
        "ampgent.autoresearch-continuation-policy.1"
    )
    maximum_generations_per_run: int = Field(ge=1)
    minimum_high_quality_candidates: int = Field(ge=1)
    stagnation_patience_generations: int = Field(ge=1)


class ContinuationDecision(FrozenModel):
    next_action: Literal[
        "continue_evolution",
        "switch_strategy",
        "freeze_successor_run",
        "quality_goal_met",
    ]
    continue_required: bool
    high_quality_candidate_count: int = Field(ge=0)
    literal_high_quality_candidate_count: int = Field(default=0, ge=0)
    quality_gate: Literal["ood-qualified-wetlab-20-to-30-aa"] = (
        "ood-qualified-wetlab-20-to-30-aa"
    )
    archive_gain: bool
    consecutive_stagnant_generations: int = Field(ge=0)
    reasons: tuple[str, ...] = Field(min_length=1)


class MultiFrontArchiveUpdate(FrozenModel):
    schema_version: Literal["ampgent.autoresearch-archive-update.1"] = (
        "ampgent.autoresearch-archive-update.1"
    )
    previous_archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    current: MultiFrontArchiveSnapshot
    added_candidate_ids_by_archive: dict[str, tuple[str, ...]]
    removed_candidate_ids_by_archive: dict[str, tuple[str, ...]]
    new_candidate_ids: tuple[str, ...]
    new_family_count: int = Field(ge=0)
    continuation: ContinuationDecision

    @computed_field(return_type=str)
    @property
    def update_sha256(self) -> str:
        return sha256_json(self.model_dump(mode="json", exclude={"update_sha256"}))


def _high_quality_candidate_ids(snapshot: MultiFrontArchiveSnapshot) -> set[str]:
    return (
        set(snapshot.archive_members["activity_consensus"])
        & set(snapshot.archive_members["activity_safety_balance"])
        & set(snapshot.archive_members["stability_degradation"])
    )


def update_multi_front_archive(
    previous: MultiFrontArchiveSnapshot,
    candidates: Sequence[CandidateEvidence],
    archive_policy: MultiFrontArchivePolicy,
    continuation_policy: ContinuationPolicy,
    *,
    generation: int,
    prior_consecutive_stagnant_generations: int = 0,
) -> MultiFrontArchiveUpdate:
    """Rebuild all fronts, record exact turnover, and choose a non-scalarized next action."""

    if generation <= previous.generation:
        raise ValueError("archive update generation must advance")
    if previous.policy_sha256 != archive_policy.sha256():
        raise ValueError("archive update policy differs from the previous snapshot")
    current = build_multi_front_archive(candidates, archive_policy, generation=generation)
    added: dict[str, tuple[str, ...]] = {}
    removed: dict[str, tuple[str, ...]] = {}
    for name in ARCHIVE_NAMES:
        previous_ids = set(previous.archive_members[name])
        current_ids = set(current.archive_members[name])
        added[name] = tuple(sorted(current_ids - previous_ids))
        removed[name] = tuple(sorted(previous_ids - current_ids))
    new_candidate_ids = tuple(
        sorted(set(current.source_candidate_ids) - set(previous.source_candidate_ids))
    )
    by_id = {item.candidate_id: item for item in candidates}
    prior_novel_families = {
        by_id[candidate_id].family_key
        for candidate_id in previous.archive_members["novel_family"]
        if candidate_id in by_id
    }
    current_novel_families = {
        by_id[candidate_id].family_key
        for candidate_id in current.archive_members["novel_family"]
    }
    new_family_count = len(current_novel_families - prior_novel_families)
    archive_gain = any(added[name] for name in ARCHIVE_NAMES)
    stagnant = 0 if archive_gain else prior_consecutive_stagnant_generations + 1
    literal_high_quality_ids = _high_quality_candidate_ids(current)
    high_quality_count = sum(
        is_ood_qualified_wetlab_candidate(by_id[candidate_id])
        for candidate_id in literal_high_quality_ids
        if candidate_id in by_id
    )

    if generation >= continuation_policy.maximum_generations_per_run:
        next_action = "freeze_successor_run"
        continue_required = True
        reasons = ("per_run_generation_budget_reached_preserve_archive_in_successor",)
    elif high_quality_count < continuation_policy.minimum_high_quality_candidates:
        continue_required = True
        if stagnant >= continuation_policy.stagnation_patience_generations:
            next_action = "switch_strategy"
            reasons = (
                "ood_qualified_high_quality_quota_underfilled",
                "archive_stagnation_requires_operator_or_sampling_change",
            )
        else:
            next_action = "continue_evolution"
            reasons = ("ood_qualified_high_quality_quota_underfilled",)
    elif stagnant >= continuation_policy.stagnation_patience_generations:
        next_action = "quality_goal_met"
        continue_required = False
        reasons = ("high_quality_quota_met_and_archive_gain_saturated",)
    else:
        next_action = "continue_evolution"
        continue_required = True
        reasons = ("archive_still_gaining_after_quality_quota",)

    return MultiFrontArchiveUpdate(
        previous_archive_sha256=previous.archive_sha256,
        current=current,
        added_candidate_ids_by_archive=added,
        removed_candidate_ids_by_archive=removed,
        new_candidate_ids=new_candidate_ids,
        new_family_count=new_family_count,
        continuation=ContinuationDecision(
            next_action=next_action,
            continue_required=continue_required,
            high_quality_candidate_count=high_quality_count,
            literal_high_quality_candidate_count=len(literal_high_quality_ids),
            archive_gain=archive_gain,
            consecutive_stagnant_generations=stagnant,
            reasons=reasons,
        ),
    )


__all__ = [
    "ARCHIVE_NAMES",
    "ArchiveObjective",
    "BaselineMetricDeltas",
    "CandidateEvidence",
    "ContinuationDecision",
    "ContinuationPolicy",
    "ControlledCrossoverAction",
    "CrossoverFragment",
    "DeNovoAction",
    "EvolutionAction",
    "MaskedSubstitutionAction",
    "MetricDelta",
    "MetricObservation",
    "MultiFrontArchivePolicy",
    "MultiFrontArchiveSnapshot",
    "MultiFrontArchiveUpdate",
    "ParentChildDelta",
    "PepMLMCrossoverWindow",
    "PepMLMTargetedAction",
    "ResidueSubstitution",
    "apply_evolution_action",
    "build_multi_front_archive",
    "compute_parent_child_delta",
    "parse_persisted_archive_snapshot",
    "parse_evolution_action",
    "update_multi_front_archive",
    "is_ood_qualified_wetlab_candidate",
    "validate_action_child",
]
