from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_EISENBERG_HYDROPHOBICITY = {
    "A": 0.62,
    "C": 0.29,
    "D": -0.90,
    "E": -0.74,
    "F": 1.19,
    "G": 0.48,
    "H": -0.40,
    "I": 1.38,
    "K": -1.50,
    "L": 1.06,
    "M": 0.64,
    "N": -0.78,
    "P": 0.12,
    "Q": -0.85,
    "R": -2.53,
    "S": -0.18,
    "T": -0.05,
    "V": 1.08,
    "W": 0.81,
    "Y": 0.26,
}


class BehaviorSpacePolicy(BaseModel):
    """Immutable, interpretable MAP-Elites behavior space for short peptides."""

    model_config = ConfigDict(frozen=True)

    policy_id: str = "ampgent-peptide-behavior-space-v1"
    charge_density_edges: tuple[float, ...] = (
        -1.0,
        -0.5,
        -0.25,
        0.0,
        0.1,
        0.2,
        0.35,
        0.5,
        0.75,
        1.0,
    )
    hydrophobicity_edges: tuple[float, ...] = (0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 1.0)
    hydrophobic_moment_edges: tuple[float, ...] = (
        0.0,
        0.1,
        0.2,
        0.3,
        0.45,
        0.6,
        0.8,
        1.2,
        2.0,
    )
    length_edges: tuple[float, ...] = (10.0, 14.0, 18.0, 22.0, 26.0, 31.0)
    activity_support_minimum: int = 2
    hemolysis_probability_maximum: float = 0.5

    @property
    def total_cell_count(self) -> int:
        return math.prod(
            len(edges) - 1
            for edges in (
                self.charge_density_edges,
                self.hydrophobicity_edges,
                self.hydrophobic_moment_edges,
                self.length_edges,
            )
        )


class BehaviorVector(BaseModel):
    model_config = ConfigDict(frozen=True)

    charge_density: float
    hydrophobicity: float
    hydrophobic_moment: float
    length: int


class QualityDiversityCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    sequence: str
    behavior: BehaviorVector
    quality: float = Field(ge=0.0, le=1.0)
    display_eligible: bool
    activity_support_count: int
    hemolysis_probability: float
    hemolysis_label: str
    operator_name: str = "unknown"
    parent_behavior: BehaviorVector | None = None


class ArchiveElite(BaseModel):
    model_config = ConfigDict(frozen=True)

    cell_id: str
    candidate_id: str
    sequence: str
    quality: float
    behavior: BehaviorVector


class CandidateContribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    cell_id: str | None
    contribution: Literal[
        "empty_cell",
        "incumbent_replacement",
        "same_cell_non_elite",
        "quality_gate_failed",
        "outside_behavior_space",
    ]
    incumbent_candidate_id: str | None = None
    quality: float
    property_displacement: PropertyDisplacement | None = None


class PropertyDisplacement(BaseModel):
    model_config = ConfigDict(frozen=True)

    charge_density: float
    hydrophobicity: float
    hydrophobic_moment: float
    length: float


class BatchCellOccupancy(BaseModel):
    model_config = ConfigDict(frozen=True)

    cell_id: str
    candidate_ids: tuple[str, ...]
    batch_elite_candidate_id: str


class OperatorArchiveEffect(BaseModel):
    model_config = ConfigDict(frozen=True)

    operator_name: str
    candidate_count: int
    quality_gate_pass_count: int
    new_cell_count: int
    incumbent_replacement_count: int
    repeated_cell_count: int
    mean_property_displacement: PropertyDisplacement | None = None


class QualityDiversityArchiveState(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "ampgent.quality-diversity-archive.1"
    policy: BehaviorSpacePolicy
    elites: tuple[ArchiveElite, ...]
    covered_cell_ids: tuple[str, ...]
    empty_cell_ids: tuple[str, ...]
    contributions: tuple[CandidateContribution, ...]
    batch_cell_occupancies: tuple[BatchCellOccupancy, ...]
    operator_effects: tuple[OperatorArchiveEffect, ...]
    eligible_batch_candidate_count: int
    diversity_gain: int
    incumbent_replacement_count: int
    best_peptide_quality: float | None
    mean_peptide_quality: float | None
    valid_cell_coverage: float
    archive_qd_score: float
    maximum_cell_concentration: float
    archive_relative_novelty: float


def alpha_helix_hydrophobic_moment(sequence: str) -> float:
    """Mean Eisenberg hydrophobic moment with a fixed 100-degree alpha-helix turn."""

    if not sequence or set(sequence) - set(_EISENBERG_HYDROPHOBICITY):
        raise ValueError("hydrophobic moment requires a non-empty canonical peptide")
    angle = math.radians(100.0)
    x = sum(
        _EISENBERG_HYDROPHOBICITY[residue] * math.cos(index * angle)
        for index, residue in enumerate(sequence)
    )
    y = sum(
        _EISENBERG_HYDROPHOBICITY[residue] * math.sin(index * angle)
        for index, residue in enumerate(sequence)
    )
    return math.hypot(x, y) / len(sequence)


def behavior_vector(
    sequence: str,
    *,
    net_charge: float,
    hydrophobicity: float,
    hydrophobic_moment: float | None = None,
) -> BehaviorVector:
    if not sequence:
        raise ValueError("behavior vector requires a non-empty peptide")
    return BehaviorVector(
        charge_density=net_charge / len(sequence),
        hydrophobicity=hydrophobicity,
        hydrophobic_moment=(
            alpha_helix_hydrophobic_moment(sequence)
            if hydrophobic_moment is None
            else hydrophobic_moment
        ),
        length=len(sequence),
    )


def _bin_index(value: float, edges: Sequence[float]) -> int | None:
    if not math.isfinite(value) or value < edges[0] or value > edges[-1]:
        return None
    if value == edges[-1]:
        return len(edges) - 2
    for index, (lower, upper) in enumerate(zip(edges, edges[1:], strict=True)):
        if lower <= value < upper:
            return index
    return None


def behavior_cell_id(behavior: BehaviorVector, policy: BehaviorSpacePolicy) -> str | None:
    indices = tuple(
        _bin_index(value, edges)
        for value, edges in (
            (behavior.charge_density, policy.charge_density_edges),
            (behavior.hydrophobicity, policy.hydrophobicity_edges),
            (behavior.hydrophobic_moment, policy.hydrophobic_moment_edges),
            (float(behavior.length), policy.length_edges),
        )
    )
    if any(index is None for index in indices):
        return None
    return "q{}-h{}-m{}-l{}".format(*indices)


def all_cell_ids(policy: BehaviorSpacePolicy) -> tuple[str, ...]:
    return tuple(
        f"q{charge}-h{hydrophobicity}-m{moment}-l{length}"
        for charge in range(len(policy.charge_density_edges) - 1)
        for hydrophobicity in range(len(policy.hydrophobicity_edges) - 1)
        for moment in range(len(policy.hydrophobic_moment_edges) - 1)
        for length in range(len(policy.length_edges) - 1)
    )


def _quality_gate_passes(candidate: QualityDiversityCandidate, policy: BehaviorSpacePolicy) -> bool:
    return bool(
        candidate.display_eligible
        and candidate.activity_support_count >= policy.activity_support_minimum
        and candidate.hemolysis_label.lower() == "low"
        and math.isfinite(candidate.hemolysis_probability)
        and candidate.hemolysis_probability <= policy.hemolysis_probability_maximum
    )


def _elite(candidate: QualityDiversityCandidate, cell_id: str) -> ArchiveElite:
    return ArchiveElite(
        cell_id=cell_id,
        candidate_id=candidate.candidate_id,
        sequence=candidate.sequence,
        quality=candidate.quality,
        behavior=candidate.behavior,
    )


def _displacement(candidate: QualityDiversityCandidate) -> PropertyDisplacement | None:
    parent = candidate.parent_behavior
    if parent is None:
        return None
    return PropertyDisplacement(
        charge_density=candidate.behavior.charge_density - parent.charge_density,
        hydrophobicity=candidate.behavior.hydrophobicity - parent.hydrophobicity,
        hydrophobic_moment=(candidate.behavior.hydrophobic_moment - parent.hydrophobic_moment),
        length=float(candidate.behavior.length - parent.length),
    )


def build_quality_diversity_archive(
    prior_candidates: Sequence[QualityDiversityCandidate],
    batch_candidates: Sequence[QualityDiversityCandidate],
    policy: BehaviorSpacePolicy | None = None,
) -> QualityDiversityArchiveState:
    """Build a quality-gated MAP-Elites archive without a quality/diversity blend."""

    policy = policy or BehaviorSpacePolicy()
    prior_elites: dict[str, ArchiveElite] = {}
    for candidate in prior_candidates:
        cell_id = behavior_cell_id(candidate.behavior, policy)
        if cell_id is None or not _quality_gate_passes(candidate, policy):
            continue
        incumbent = prior_elites.get(cell_id)
        proposed = _elite(candidate, cell_id)
        if incumbent is None or (proposed.quality, proposed.candidate_id) > (
            incumbent.quality,
            incumbent.candidate_id,
        ):
            prior_elites[cell_id] = proposed

    batch_by_cell: dict[str, list[QualityDiversityCandidate]] = defaultdict(list)
    contributions_by_id: dict[str, CandidateContribution] = {}
    operator_pass_counts: Counter[str] = Counter()
    for candidate in batch_candidates:
        cell_id = behavior_cell_id(candidate.behavior, policy)
        displacement = _displacement(candidate)
        if not _quality_gate_passes(candidate, policy):
            contributions_by_id[candidate.candidate_id] = CandidateContribution(
                candidate_id=candidate.candidate_id,
                cell_id=cell_id,
                contribution="quality_gate_failed",
                quality=candidate.quality,
                property_displacement=displacement,
            )
        elif cell_id is None:
            contributions_by_id[candidate.candidate_id] = CandidateContribution(
                candidate_id=candidate.candidate_id,
                cell_id=None,
                contribution="outside_behavior_space",
                quality=candidate.quality,
                property_displacement=displacement,
            )
        else:
            batch_by_cell[cell_id].append(candidate)
            operator_pass_counts[candidate.operator_name] += 1

    elites = dict(prior_elites)
    for cell_id, candidates in sorted(batch_by_cell.items()):
        winner = max(candidates, key=lambda item: (item.quality, item.candidate_id))
        incumbent = prior_elites.get(cell_id)
        if incumbent is None:
            contribution = "empty_cell"
            elites[cell_id] = _elite(winner, cell_id)
        elif (winner.quality, winner.candidate_id) > (
            incumbent.quality,
            incumbent.candidate_id,
        ):
            contribution = "incumbent_replacement"
            elites[cell_id] = _elite(winner, cell_id)
        else:
            contribution = "same_cell_non_elite"
        contributions_by_id[winner.candidate_id] = CandidateContribution(
            candidate_id=winner.candidate_id,
            cell_id=cell_id,
            contribution=contribution,
            incumbent_candidate_id=None if incumbent is None else incumbent.candidate_id,
            quality=winner.quality,
            property_displacement=_displacement(winner),
        )
        for candidate in candidates:
            if candidate.candidate_id == winner.candidate_id:
                continue
            contributions_by_id[candidate.candidate_id] = CandidateContribution(
                candidate_id=candidate.candidate_id,
                cell_id=cell_id,
                contribution="same_cell_non_elite",
                incumbent_candidate_id=(
                    winner.candidate_id if incumbent is None else incumbent.candidate_id
                ),
                quality=candidate.quality,
                property_displacement=_displacement(candidate),
            )

    contributions = tuple(
        contributions_by_id[candidate.candidate_id] for candidate in batch_candidates
    )
    effects: list[OperatorArchiveEffect] = []
    for operator_name in sorted({item.operator_name for item in batch_candidates}):
        candidates = [item for item in batch_candidates if item.operator_name == operator_name]
        candidate_contributions = [contributions_by_id[item.candidate_id] for item in candidates]
        displacements = [
            item.property_displacement
            for item in candidate_contributions
            if item.property_displacement is not None
        ]
        mean_displacement = None
        if displacements:
            mean_displacement = PropertyDisplacement(
                charge_density=sum(item.charge_density for item in displacements)
                / len(displacements),
                hydrophobicity=sum(item.hydrophobicity for item in displacements)
                / len(displacements),
                hydrophobic_moment=sum(item.hydrophobic_moment for item in displacements)
                / len(displacements),
                length=sum(item.length for item in displacements) / len(displacements),
            )
        effects.append(
            OperatorArchiveEffect(
                operator_name=operator_name,
                candidate_count=len(candidates),
                quality_gate_pass_count=operator_pass_counts[operator_name],
                new_cell_count=sum(
                    item.contribution == "empty_cell" for item in candidate_contributions
                ),
                incumbent_replacement_count=sum(
                    item.contribution == "incumbent_replacement" for item in candidate_contributions
                ),
                repeated_cell_count=sum(
                    item.contribution == "same_cell_non_elite" for item in candidate_contributions
                ),
                mean_property_displacement=mean_displacement,
            )
        )

    eligible_batch_count = sum(len(items) for items in batch_by_cell.values())
    diversity_gain = sum(item.contribution == "empty_cell" for item in contributions)
    all_ids = all_cell_ids(policy)
    covered = tuple(sorted(elites))
    qualities = [item.quality for item in elites.values()]
    maximum_cell_count = max((len(items) for items in batch_by_cell.values()), default=0)
    return QualityDiversityArchiveState(
        policy=policy,
        elites=tuple(elites[cell_id] for cell_id in covered),
        covered_cell_ids=covered,
        empty_cell_ids=tuple(cell_id for cell_id in all_ids if cell_id not in elites),
        contributions=contributions,
        batch_cell_occupancies=tuple(
            BatchCellOccupancy(
                cell_id=cell_id,
                candidate_ids=tuple(
                    item.candidate_id
                    for item in sorted(
                        candidates,
                        key=lambda item: (-item.quality, item.candidate_id),
                    )
                ),
                batch_elite_candidate_id=max(
                    candidates, key=lambda item: (item.quality, item.candidate_id)
                ).candidate_id,
            )
            for cell_id, candidates in sorted(batch_by_cell.items())
        ),
        operator_effects=tuple(effects),
        eligible_batch_candidate_count=eligible_batch_count,
        diversity_gain=diversity_gain,
        incumbent_replacement_count=sum(
            item.contribution == "incumbent_replacement" for item in contributions
        ),
        best_peptide_quality=max(qualities) if qualities else None,
        mean_peptide_quality=sum(qualities) / len(qualities) if qualities else None,
        valid_cell_coverage=len(elites) / policy.total_cell_count,
        archive_qd_score=sum(qualities),
        maximum_cell_concentration=(
            maximum_cell_count / eligible_batch_count if eligible_batch_count else 0.0
        ),
        archive_relative_novelty=(
            diversity_gain / eligible_batch_count if eligible_batch_count else 0.0
        ),
    )


def candidate_from_score_row(
    row: Mapping[str, str],
    *,
    parent_row: Mapping[str, str] | None = None,
) -> QualityDiversityCandidate:
    percentile_columns = (
        "amp_read_log10_mic_um__parent_benefit_percentile",
        "llamp_log10_mic_um__parent_benefit_percentile",
        "macrel_amp_probability__parent_benefit_percentile",
    )
    percentiles = [float(row[column]) for column in percentile_columns]
    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in percentiles):
        raise ValueError("quality percentiles must be finite values in [0, 1]")

    def vector(source: Mapping[str, str]) -> BehaviorVector:
        return behavior_vector(
            source["sequence"],
            net_charge=float(source["net_charge_ph7_4"]),
            hydrophobicity=float(source["hydrophobic_ratio_modlamp"]),
            hydrophobic_moment=float(source["hydrophobic_moment_eisenberg"]),
        )

    return QualityDiversityCandidate(
        candidate_id=row["sequence_sha256"],
        sequence=row["sequence"],
        behavior=vector(row),
        quality=sum(percentiles) / len(percentiles),
        display_eligible=str(row["display_eligible"]).lower() == "true",
        activity_support_count=int(row["activity_model_support_count_calibrated"]),
        hemolysis_probability=float(row["macrel_hemolysis_probability"]),
        hemolysis_label=row["macrel_hemolysis_label"],
        operator_name=row.get("action_type") or row.get("operator_id") or "baseline",
        parent_behavior=None if parent_row is None else vector(parent_row),
    )


__all__ = [
    "ArchiveElite",
    "BatchCellOccupancy",
    "BehaviorSpacePolicy",
    "BehaviorVector",
    "CandidateContribution",
    "OperatorArchiveEffect",
    "PropertyDisplacement",
    "QualityDiversityArchiveState",
    "QualityDiversityCandidate",
    "all_cell_ids",
    "alpha_helix_hydrophobic_moment",
    "behavior_cell_id",
    "behavior_vector",
    "build_quality_diversity_archive",
    "candidate_from_score_row",
]
