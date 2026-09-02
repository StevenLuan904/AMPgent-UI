from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from Bio.SeqUtils.ProtParam import ProteinAnalysis
from pydantic import BaseModel, ConfigDict, Field

from pepagent.autoresearch_closed_loop import (
    CandidateEvidence,
    ControlledCrossoverAction,
    CrossoverFragment,
    DeNovoAction,
    EvolutionAction,
    MaskedSubstitutionAction,
    MultiFrontArchiveSnapshot,
    PepMLMTargetedAction,
    ResidueSubstitution,
    is_instability_score_qualified_wetlab_candidate,
)
from pepagent.provenance.hashing import sha256_text
from pepagent.sequence_family import ungapped_identity_and_coverage

GOLD_CANDIDATE_TARGET = 50
_HYDROPHOBIC = frozenset("AVILMFWYC")
_DE_NOVO_MAXIMUM_HYDROPHOBIC_FRACTION = 0.45
_DE_NOVO_MAXIMUM_HYDROPHOBIC_RUN = 2
_DE_NOVO_MINIMUM_NET_CHARGE = 3.0
_DE_NOVO_MAXIMUM_NET_CHARGE = 10.0
_DE_NOVO_MAXIMUM_HISTIDINE_FRACTION = 0.12
_DE_NOVO_ALPHABET = "KRHNQSTEDAILVFWYG"
_DE_NOVO_LENGTHS = (20, 21, 22, 23, 24, 25, 26)
_CANONICAL_AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"
_DE_NOVO_PROFILE_PRIOR_COUNTS = {
    "K": 22,
    "R": 10,
    "H": 4,
    "N": 5,
    "Q": 5,
    "S": 10,
    "T": 10,
    "E": 4,
    "D": 2,
    "A": 6,
    "G": 12,
    "V": 3,
    "L": 2,
    "I": 1,
    "F": 1,
    "W": 1,
    "Y": 2,
}
_DE_NOVO_EMPIRICAL_PROFILE_WEIGHT = 0.5
_DE_NOVO_MINIMUM_GOLD_PROFILE_COUNT = 3


def _sequence_prescreen(sequence: str) -> tuple[float, int, float]:
    """Return frozen stability, hydrophobic-run, and charge descriptors for planning."""

    analysis = ProteinAnalysis(sequence)
    maximum_run = 0
    current_run = 0
    for residue in sequence:
        if residue in _HYDROPHOBIC:
            current_run += 1
            maximum_run = max(maximum_run, current_run)
        else:
            current_run = 0
    return (
        float(analysis.instability_index()),
        maximum_run,
        float(analysis.charge_at_pH(7.4)),
    )


def _hydrophobic_fraction(sequence: str) -> float:
    return sum(residue in _HYDROPHOBIC for residue in sequence) / len(sequence)


def _proposal_quality_gate_passes(sequence: str) -> bool:
    """Reject rule proposals that cannot satisfy the frozen proposal quality gate."""

    instability, hydrophobic_run, charge = _sequence_prescreen(sequence)
    hydrophobic_fraction = _hydrophobic_fraction(sequence)
    return (
        math.isfinite(instability)
        and instability < 50.0
        and hydrophobic_run <= _DE_NOVO_MAXIMUM_HYDROPHOBIC_RUN
        and hydrophobic_fraction <= _DE_NOVO_MAXIMUM_HYDROPHOBIC_FRACTION
        and charge >= _DE_NOVO_MINIMUM_NET_CHARGE
    )


def _de_novo_prescreen_passes(sequence: str) -> bool:
    """Apply de-novo-specific composition guards after the shared quality gate."""

    _, _, charge = _sequence_prescreen(sequence)
    histidine_fraction = sequence.count("H") / len(sequence)
    return bool(
        _proposal_quality_gate_passes(sequence)
        and charge <= _DE_NOVO_MAXIMUM_NET_CHARGE
        and histidine_fraction <= _DE_NOVO_MAXIMUM_HISTIDINE_FRACTION
    )


def _shares_sequence_family(
    sequence: str,
    references: Collection[str],
) -> bool:
    """Return whether sequence has a direct edge under the frozen 80/80 contract."""

    for reference in references:
        shorter, longer = (
            (sequence, reference)
            if len(sequence) <= len(reference)
            else (reference, sequence)
        )
        if len(shorter) / len(longer) < 0.8:
            continue
        required_matches = math.ceil(0.8 * len(shorter) - 1e-12)
        block_count = len(shorter) - required_matches + 1
        base_width, remainder = divmod(len(shorter), block_count)
        start = 0
        possible = False
        for block_index in range(block_count):
            width = base_width + (1 if block_index < remainder else 0)
            end = start + width
            if shorter[start:end] in longer:
                possible = True
                break
            start = end
        if not possible:
            continue
        identity, coverage = ungapped_identity_and_coverage(sequence, reference)
        if identity >= 0.8 and coverage >= 0.8:
            return True
    return False


class _SequenceFamilyReferenceIndex:
    """Reusable exact prefilter for repeated 80/80 family queries.

    Peptides in the planner are at least ten residues long.  Any 80%-identity
    ungapped alignment at that length contains a shared contiguous 3-mer, so a
    length-aware 3-mer inverted index can discard impossible references without
    changing the frozen family decision.
    """

    def __init__(self, references: Collection[str] = ()) -> None:
        self._references: set[str] = set()
        self._by_length_and_trimer: dict[tuple[int, str], set[str]] = defaultdict(set)
        self.update(references)

    def update(self, references: Collection[str]) -> None:
        for reference in references:
            if reference in self._references:
                continue
            self._references.add(reference)
            if len(reference) < 3:
                continue
            for index in range(len(reference) - 2):
                self._by_length_and_trimer[(len(reference), reference[index : index + 3])].add(
                    reference
                )

    def shares_family(self, sequence: str) -> bool:
        if len(sequence) < 10:
            return _shares_sequence_family(sequence, self._references)
        minimum_length = math.ceil(0.8 * len(sequence) - 1e-12)
        maximum_length = math.floor(len(sequence) / 0.8 + 1e-12)
        candidates: set[str] = set()
        trimers = {sequence[index : index + 3] for index in range(len(sequence) - 2)}
        for reference_length in range(minimum_length, maximum_length + 1):
            for trimer in trimers:
                candidates.update(
                    self._by_length_and_trimer.get((reference_length, trimer), ())
                )
        return _shares_sequence_family(sequence, candidates)


def _adaptive_de_novo_alphabet(sequences: Collection[str]) -> str:
    """Build a deterministic activity/safety-shrunk target alphabet.

    The archive contributes target-specific evidence, but a fixed prior prevents a
    repeatedly expanded Lys/His-rich family from monopolising the de-novo proposal
    distribution. Callers provide one sequence per family so family population
    size cannot silently act as a weight.
    """

    if not sequences:
        return _DE_NOVO_ALPHABET
    if any(not sequence or set(sequence) - set(_CANONICAL_AMINO_ACIDS) for sequence in sequences):
        raise ValueError("de-novo profile contains a non-canonical peptide")
    counts = {residue: 0 for residue in _DE_NOVO_ALPHABET}
    for sequence in sequences:
        for residue in sequence:
            if residue in counts:
                counts[residue] += 1
    total_observed = sum(counts.values())
    prior_weight = 1.0 - _DE_NOVO_EMPIRICAL_PROFILE_WEIGHT
    return "".join(
        residue
        * max(
            1,
            round(
                100
                * _DE_NOVO_EMPIRICAL_PROFILE_WEIGHT
                * counts[residue]
                / total_observed
                + prior_weight * _DE_NOVO_PROFILE_PRIOR_COUNTS[residue]
            ),
        )
        for residue in _DE_NOVO_ALPHABET
    )


def _adaptive_de_novo_transition_alphabets(
    sequences: Collection[str],
) -> dict[str, str]:
    """Build smoothed first-order transition alphabets from elite sequences."""

    if not sequences:
        return {}
    if any(not sequence or set(sequence) - set(_CANONICAL_AMINO_ACIDS) for sequence in sequences):
        raise ValueError("de-novo transition profile contains a non-canonical peptide")
    transition_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {residue: 0 for residue in _DE_NOVO_ALPHABET}
    )
    for sequence in sequences:
        previous = "^"
        for residue in sequence:
            if residue in transition_counts[previous]:
                transition_counts[previous][residue] += 1
            previous = residue
    prior_weight = 1.0 - _DE_NOVO_EMPIRICAL_PROFILE_WEIGHT
    alphabets: dict[str, str] = {}
    for previous in sorted(transition_counts):
        counts = transition_counts[previous]
        total_observed = sum(counts.values())
        if total_observed == 0:
            continue
        alphabets[previous] = "".join(
            residue
            * max(
                1,
                round(
                    100
                    * _DE_NOVO_EMPIRICAL_PROFILE_WEIGHT
                    * counts[residue]
                    / total_observed
                    + prior_weight * _DE_NOVO_PROFILE_PRIOR_COUNTS[residue]
                ),
            )
            for residue in _DE_NOVO_ALPHABET
        )
    return alphabets


def _family_balanced_de_novo_profile(candidates: Collection[CandidateEvidence]) -> tuple[str, ...]:
    """Return one deterministic profile sequence per 80/80 family."""

    by_family: dict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        by_family[candidate.family_key].append(candidate.sequence)
    return tuple(min(by_family[family_key]) for family_key in sorted(by_family))


class PlannerDeltaEvidence(BaseModel):
    """Compact, immutable evidence that a prior child improved one metric."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1)
    metric_name: str = Field(min_length=1)
    delta_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    improved: bool


def _gold_candidate_ids(snapshot: MultiFrontArchiveSnapshot) -> set[str]:
    return (
        set(snapshot.archive_members["activity_consensus"])
        & set(snapshot.archive_members["activity_safety_balance"])
        & set(snapshot.archive_members["stability_degradation"])
    )


def _instability_score_qualified_gold_candidate_ids(
    snapshot: MultiFrontArchiveSnapshot,
    candidates_by_id: Mapping[str, CandidateEvidence],
) -> set[str]:
    return {
        candidate_id
        for candidate_id in _gold_candidate_ids(snapshot)
        if candidate_id in candidates_by_id
        and is_instability_score_qualified_wetlab_candidate(candidates_by_id[candidate_id])
    }


def _improvement_index(
    deltas: Sequence[PlannerDeltaEvidence],
) -> tuple[dict[str, int], dict[str, tuple[str, ...]]]:
    counts: dict[str, int] = defaultdict(int)
    receipts: dict[str, set[str]] = defaultdict(set)
    for item in deltas:
        if item.improved:
            counts[item.candidate_id] += 1
            receipts[item.candidate_id].add(item.delta_sha256)
    return counts, {
        candidate_id: tuple(sorted(values)) for candidate_id, values in receipts.items()
    }


def _lane_candidates(
    snapshot: MultiFrontArchiveSnapshot,
    lane_names: Sequence[str],
    *,
    candidates_by_id: Mapping[str, CandidateEvidence],
    improvement_counts: Mapping[str, int],
) -> list[CandidateEvidence]:
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for lane_name in lane_names:
        for candidate_id in snapshot.archive_members[lane_name]:
            if candidate_id not in seen:
                ordered_ids.append(candidate_id)
                seen.add(candidate_id)
    eligible = [
        candidates_by_id[candidate_id]
        for candidate_id in ordered_ids
        if candidate_id in candidates_by_id
        and is_instability_score_qualified_wetlab_candidate(candidates_by_id[candidate_id])
    ]
    return sorted(
        eligible,
        key=lambda item: (-int(improvement_counts.get(item.candidate_id, 0)), item.candidate_id),
    )


def _mutation(
    parent: CandidateEvidence,
    *,
    known_sequences: set[str] | None = None,
    excluded_sequence_sha256s: Collection[str] = (),
) -> ResidueSubstitution:
    """Choose a deterministic edit that remains below the literal stability gate."""

    best_start = 0
    best_length = 0
    start = 0
    while start < len(parent.sequence):
        end = start + 1
        if parent.sequence[start] in _HYDROPHOBIC:
            while end < len(parent.sequence) and parent.sequence[end] in _HYDROPHOBIC:
                end += 1
        if end - start > best_length:
            best_start, best_length = start, end - start
        start = end
    if best_length:
        position = best_start + (best_length - 1) // 2
    else:
        position = max(
            range(len(parent.sequence)),
            key=lambda index: (
                parent.sequence[index] in {"K", "R"},
                parent.sequence[index] == parent.sequence[index - 1] if index else False,
                -index,
            ),
        )
    positions = [position, *(index for index in range(len(parent.sequence)) if index != position)]
    known = known_sequences or set()
    parent_instability, parent_hydrophobic_run, parent_charge = _sequence_prescreen(parent.sequence)
    proposals: list[tuple[tuple[float | int, ...], ResidueSubstitution]] = []
    for position_rank, candidate_position in enumerate(positions):
        source = parent.sequence[candidate_position]
        preferred = (
            ("S", "N", "Q", "K")
            if source in _HYDROPHOBIC
            else ("Q", "N", "S", "K")
            if source in {"K", "R"}
            else ("K", "R", "S", "N")
        )
        for replacement_rank, replacement in enumerate(preferred):
            if replacement == source:
                continue
            child = (
                parent.sequence[:candidate_position]
                + replacement
                + parent.sequence[candidate_position + 1 :]
            )
            if child in known or sha256_text(child) in excluded_sequence_sha256s:
                continue
            instability, hydrophobic_run, charge = _sequence_prescreen(child)
            hydrophobic_fraction = _hydrophobic_fraction(child)
            if not _proposal_quality_gate_passes(child):
                continue
            proposals.append(
                (
                    (
                        int(
                            parent_hydrophobic_run >= 2
                            and hydrophobic_run >= parent_hydrophobic_run
                        ),
                        hydrophobic_run,
                        hydrophobic_fraction,
                        instability,
                        max(0.0, parent_charge - charge),
                        int(instability >= parent_instability),
                        position_rank,
                        replacement_rank,
                    ),
                    ResidueSubstitution(
                        position_zero_based=candidate_position,
                        from_residue=source,
                        to_residue=replacement,
                    ),
                )
            )
    if proposals:
        return min(proposals, key=lambda item: item[0])[1]
    raise ValueError("planner cannot find a unique one-residue substitution")


def _action_evidence(
    archive_sha256: str,
    candidate_ids: Sequence[str],
    delta_receipts: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                archive_sha256,
                *(
                    receipt
                    for candidate_id in candidate_ids
                    for receipt in delta_receipts.get(candidate_id, ())
                ),
            }
        )
    )


def _unique_de_novo_sequence(
    *,
    branch_key: str,
    seed: int,
    known_sequences: set[str],
    excluded_sequence_sha256s: Collection[str] = (),
    family_reference_sequences: Collection[str] = (),
    family_reference_index: _SequenceFamilyReferenceIndex | None = None,
    residue_alphabet: str = _DE_NOVO_ALPHABET,
    transition_alphabets: Mapping[str, str] | None = None,
) -> str:
    if not residue_alphabet or set(residue_alphabet) - set(_CANONICAL_AMINO_ACIDS):
        raise ValueError("de-novo residue alphabet must contain canonical amino acids")
    transition_alphabets = transition_alphabets or {}
    if any(
        (previous != "^" and previous not in _CANONICAL_AMINO_ACIDS)
        or not alphabet
        or set(alphabet) - set(_CANONICAL_AMINO_ACIDS)
        for previous, alphabet in transition_alphabets.items()
    ):
        raise ValueError("de-novo transition alphabets must contain canonical amino acids")
    reference_sequences = (
        family_reference_sequences
        if isinstance(family_reference_sequences, (set, frozenset))
        else frozenset(family_reference_sequences)
    )
    family_references = (
        reference_sequences
        if known_sequences.issubset(reference_sequences)
        else reference_sequences | known_sequences
    )
    family_reference_index = family_reference_index or _SequenceFamilyReferenceIndex(
        family_references
    )
    family_reference_index.update(family_references)
    for attempt in range(10_000):
        digest = sha256_text(f"{branch_key}:{seed}:family-opener-v7:{attempt}")
        length = _DE_NOVO_LENGTHS[int(digest[:2], 16) % len(_DE_NOVO_LENGTHS)]
        material = digest
        nonce = 0
        while len(material) < length * 2:
            nonce += 1
            material += sha256_text(f"{digest}:{nonce}")
        sequence_residues: list[str] = []
        previous = "^"
        for index in range(0, length * 2, 2):
            alphabet = transition_alphabets.get(previous, residue_alphabet)
            residue = alphabet[int(material[index : index + 2], 16) % len(alphabet)]
            sequence_residues.append(residue)
            previous = residue
        sequence = "".join(sequence_residues)
        if (
            sequence not in known_sequences
            and sha256_text(sequence) not in excluded_sequence_sha256s
            and _de_novo_prescreen_passes(sequence)
            and not family_reference_index.shares_family(sequence)
        ):
            return sequence
    raise ValueError("deterministic de-novo planner exhausted its sequence space")


def build_multifront_rule_action_plan(
    *,
    candidates: Sequence[CandidateEvidence],
    snapshot: MultiFrontArchiveSnapshot,
    branch_key: str,
    generation: int,
    seed: int,
    operator_release_sha256: str,
    target_sequence_sha256: str,
    prior_deltas: Sequence[PlannerDeltaEvidence] = (),
    historical_sequence_sha256s: Collection[str] = (),
    historical_family_representatives: Collection[str] = (),
    gold_target: int = GOLD_CANDIDATE_TARGET,
    de_novo_quota: float = 0.2,
    pepmlm_targeted_enabled: bool = True,
    required_parent_candidate_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Choose replayable actions without collapsing conflicting model fronts.

    Each archive lane remains independently selectable.  Prior positive deltas only
    order candidates inside those lanes; they never become a weighted total score.
    """

    if generation < 1:
        raise ValueError("planner generation must be positive")
    if gold_target < GOLD_CANDIDATE_TARGET:
        raise ValueError("each target branch requires at least 50 gold candidates")
    if not 0.1 <= de_novo_quota <= 0.5:
        raise ValueError("de-novo quota must remain between 0.1 and 0.5")
    by_id = {item.candidate_id: item for item in candidates}
    if len(by_id) != len(candidates):
        raise ValueError("planner candidates must have unique IDs")
    eligible = [
        item for item in candidates if is_instability_score_qualified_wetlab_candidate(item)
    ]
    if not eligible:
        raise ValueError(
            "planner has no literal-hard-gate parent with Guruprasad instability <50; "
            "the successor must use a target-specific strict seed split"
        )
    if len(set(required_parent_candidate_ids)) != len(required_parent_candidate_ids):
        raise ValueError("required planner parent IDs must be unique")
    missing_required_parent_ids = sorted(set(required_parent_candidate_ids) - set(by_id))
    if missing_required_parent_ids:
        raise ValueError("required planner parent is absent from the candidate cohort")
    ineligible_required_parent_ids = sorted(
        candidate_id
        for candidate_id in required_parent_candidate_ids
        if not is_instability_score_qualified_wetlab_candidate(by_id[candidate_id])
    )
    if ineligible_required_parent_ids:
        raise ValueError("required planner parent fails the literal stability hard gate")
    improvement_counts, delta_receipts = _improvement_index(prior_deltas)
    known_sequences = {item.sequence for item in candidates}
    family_reference_index = _SequenceFamilyReferenceIndex(
        historical_family_representatives
    )
    historical_sequence_sha256s = (
        historical_sequence_sha256s
        if isinstance(historical_sequence_sha256s, (set, frozenset))
        else frozenset(historical_sequence_sha256s)
    )
    if any(
        len(item) != 64 or set(item) - set("0123456789abcdef")
        for item in historical_sequence_sha256s
    ):
        raise ValueError("planner historical sequence exclusion contains an invalid SHA-256")
    if any(
        not item or set(item) - set("ACDEFGHIKLMNPQRSTVWY")
        for item in historical_family_representatives
    ):
        raise ValueError("planner historical family representative is not a canonical peptide")
    archive_sha = snapshot.archive_sha256
    literal_gold_count = len(_gold_candidate_ids(snapshot))
    qualified_gold_ids = _instability_score_qualified_gold_candidate_ids(snapshot, by_id)
    gold_count = len(qualified_gold_ids)
    qualified_gold = [by_id[candidate_id] for candidate_id in sorted(qualified_gold_ids)]
    if len(qualified_gold) >= _DE_NOVO_MINIMUM_GOLD_PROFILE_COUNT:
        de_novo_profile_candidates = qualified_gold
        de_novo_profile_source = "qualified_gold_archive"
    else:
        de_novo_profile_candidates = eligible
        de_novo_profile_source = "all_instability_qualified_families_fallback"
    de_novo_profile = _family_balanced_de_novo_profile(de_novo_profile_candidates)
    de_novo_alphabet = _adaptive_de_novo_alphabet(de_novo_profile)
    de_novo_transition_alphabets = _adaptive_de_novo_transition_alphabets(de_novo_profile)

    actions: list[EvolutionAction] = []
    rationales: dict[str, str] = {}
    strategies: list[str] = []
    substitution_parent_id: str | None = None
    forced_parent_action_sha256s: dict[str, str] = {}
    unmaterialized_forced_parent_candidate_ids: list[str] = []

    for forced_index, parent_id in enumerate(required_parent_candidate_ids):
        parent = by_id[parent_id]
        try:
            edit = _mutation(
                parent,
                known_sequences=known_sequences,
                excluded_sequence_sha256s=historical_sequence_sha256s,
            )
        except ValueError:
            unmaterialized_forced_parent_candidate_ids.append(parent_id)
            continue
        child = (
            parent.sequence[: edit.position_zero_based]
            + edit.to_residue
            + parent.sequence[edit.position_zero_based + 1 :]
        )
        known_sequences.add(child)
        action = MaskedSubstitutionAction(
            branch_key=branch_key,
            generation=generation,
            seed=seed + forced_index,
            operator_id="autoresearch-rule-family-coverage-substitution-v1",
            operator_release_sha256=operator_release_sha256,
            expected_improvement_metrics=(
                "amp_read_log10_mic_um",
                "llamp_log10_mic_um",
                "macrel_amp_probability",
            ),
            protected_metrics=(
                "guruprasad_instability_index",
                "macrel_hemolysis_probability",
                "toxinpred3_hybrid_score",
            ),
            evidence_sha256s=_action_evidence(archive_sha, (parent.candidate_id,), delta_receipts),
            parent_candidate_id=parent.candidate_id,
            parent_sequence_sha256=parent.sequence_sha256,
            substitutions=(edit,),
        )
        actions.append(action)
        strategies.append("forced_family_substitution")
        forced_parent_action_sha256s[parent_id] = action.action_sha256
        rationales[action.action_sha256] = (
            "Guarantee an executable quality-gated proposal from a specifically "
            "requested unresolved family before global multi-front allocation."
        )

    regular_seed_offset = len(required_parent_candidate_ids)

    substitution_pool = _lane_candidates(
        snapshot,
        (
            "stability_degradation",
            "activity_safety_balance",
            "model_disagreement",
            "activity_consensus",
        ),
        candidates_by_id=by_id,
        improvement_counts=improvement_counts,
    )
    for parent in substitution_pool:
        if parent.candidate_id in required_parent_candidate_ids:
            continue
        try:
            edit = _mutation(
                parent,
                known_sequences=known_sequences,
                excluded_sequence_sha256s=historical_sequence_sha256s,
            )
        except ValueError:
            continue
        substitution_parent_id = parent.candidate_id
        action = MaskedSubstitutionAction(
            branch_key=branch_key,
            generation=generation,
            seed=seed + regular_seed_offset,
            operator_id="autoresearch-rule-substitution-v3",
            operator_release_sha256=operator_release_sha256,
            expected_improvement_metrics=("guruprasad_instability_index",),
            protected_metrics=(
                "macrel_amp_probability",
                "macrel_hemolysis_probability",
                "toxinpred3_hybrid_score",
            ),
            evidence_sha256s=_action_evidence(archive_sha, (parent.candidate_id,), delta_receipts),
            parent_candidate_id=parent.candidate_id,
            parent_sequence_sha256=parent.sequence_sha256,
            substitutions=(edit,),
        )
        actions.append(action)
        strategies.append("substitution")
        rationales[action.action_sha256] = (
            "Edit one hydrophobic/repetitive position from the stability/safety front "
            "only after the frozen four-descriptor proposal quality gate; "
            "prior positive child deltas break ties while activity and safety stay protected."
        )
        break

    endpoint_pool = _lane_candidates(
        snapshot,
        (
            "amp_read_endpoint",
            "llamp_endpoint",
            "macrel_endpoint",
            "model_disagreement",
        ),
        candidates_by_id=by_id,
        improvement_counts=improvement_counts,
    )
    crossover_pair: tuple[CandidateEvidence, CandidateEvidence] | None = None
    crossover_fragments: tuple[CrossoverFragment, CrossoverFragment] | None = None
    crossover_key: tuple[float | int | str, ...] | None = None
    for primary in endpoint_pool:
        for donor in endpoint_pool:
            if primary.candidate_id == donor.candidate_id:
                continue
            _, primary_run, primary_charge = _sequence_prescreen(primary.sequence)
            _, donor_run, donor_charge = _sequence_prescreen(donor.sequence)
            maximum_parent_run = max(primary_run, donor_run)
            maximum_parent_hydrophobic_fraction = max(
                _hydrophobic_fraction(primary.sequence),
                _hydrophobic_fraction(donor.sequence),
            )
            minimum_parent_charge = min(primary_charge, donor_charge)
            minimum_child_charge = max(
                _DE_NOVO_MINIMUM_NET_CHARGE,
                minimum_parent_charge - 1.0,
            )
            for first_length in range(5, len(primary.sequence) - 4):
                donor_length = len(primary.sequence) - first_length
                if donor_length < 5 or donor_length > len(donor.sequence):
                    continue
                child = primary.sequence[:first_length] + donor.sequence[-donor_length:]
                if (
                    child in known_sequences
                    or child in {primary.sequence, donor.sequence}
                    or sha256_text(child) in historical_sequence_sha256s
                ):
                    continue
                instability, hydrophobic_run, charge = _sequence_prescreen(child)
                hydrophobic_fraction = _hydrophobic_fraction(child)
                if (
                    not _proposal_quality_gate_passes(child)
                    or hydrophobic_run > maximum_parent_run
                    or hydrophobic_fraction > maximum_parent_hydrophobic_fraction
                    or charge < minimum_child_charge
                ):
                    continue
                key = (
                    hydrophobic_run,
                    hydrophobic_fraction,
                    instability,
                    max(0.0, minimum_parent_charge - charge),
                    primary.candidate_id,
                    donor.candidate_id,
                    first_length,
                )
                if crossover_key is None or key < crossover_key:
                    crossover_key = key
                    crossover_pair = (primary, donor)
                    crossover_fragments = (
                        CrossoverFragment(
                            source_role="primary_parent",
                            source_start_zero_based=0,
                            source_end_exclusive=first_length,
                        ),
                        CrossoverFragment(
                            source_role="donor_parent",
                            source_start_zero_based=len(donor.sequence) - donor_length,
                            source_end_exclusive=len(donor.sequence),
                        ),
                    )
    if crossover_pair is not None and crossover_fragments is not None:
        primary, donor = crossover_pair
        action = ControlledCrossoverAction(
            branch_key=branch_key,
            generation=generation,
            seed=seed + regular_seed_offset + 1,
            operator_id="autoresearch-rule-crossover-v3",
            operator_release_sha256=operator_release_sha256,
            expected_improvement_metrics=(
                "amp_read_log10_mic_um",
                "llamp_log10_mic_um",
            ),
            protected_metrics=(
                "guruprasad_instability_index",
                "macrel_hemolysis_probability",
                "toxinpred3_hybrid_score",
            ),
            evidence_sha256s=_action_evidence(
                archive_sha,
                (primary.candidate_id, donor.candidate_id),
                delta_receipts,
            ),
            parent_candidate_id=primary.candidate_id,
            parent_sequence_sha256=primary.sequence_sha256,
            donor_candidate_id=donor.candidate_id,
            donor_sequence_sha256=donor.sequence_sha256,
            fragments=crossover_fragments,
        )
        actions.append(action)
        strategies.append("crossover")
        rationales[action.action_sha256] = (
            "Combine two distinct activity-model endpoints after a deterministic "
            "the frozen four-descriptor proposal quality gate; retain both "
            "parents as controls and preserve disagreement rather than averaging it."
        )

    proposed = _unique_de_novo_sequence(
        branch_key=branch_key,
        seed=seed + regular_seed_offset + 2,
        known_sequences=known_sequences,
        excluded_sequence_sha256s=historical_sequence_sha256s,
        family_reference_sequences=historical_family_representatives,
        family_reference_index=family_reference_index,
        residue_alphabet=de_novo_alphabet,
        transition_alphabets=de_novo_transition_alphabets,
    )
    action = DeNovoAction(
        branch_key=branch_key,
        generation=generation,
        seed=seed + 2,
        operator_id="autoresearch-rule-de-novo-v8",
        operator_release_sha256=operator_release_sha256,
        expected_improvement_metrics=("macrel_amp_probability",),
        protected_metrics=(
            "guruprasad_instability_index",
            "macrel_hemolysis_probability",
            "toxinpred3_hybrid_score",
        ),
        evidence_sha256s=(archive_sha,),
        peptide_length=len(proposed),
        proposed_sequence=proposed,
    )
    actions.append(action)
    strategies.append("de_novo")
    known_sequences.add(proposed)
    rationales[action.action_sha256] = (
        "Open a sequence family outside the current archive so elite parents cannot "
        "monopolize exploration."
    )

    if pepmlm_targeted_enabled:
        pepmlm_pool = (
            _lane_candidates(
                snapshot,
                ("model_disagreement", "novel_family", "activity_consensus"),
                candidates_by_id=by_id,
                improvement_counts=improvement_counts,
            )
            or substitution_pool
        )
        targeted_de_novo = math.ceil((len(actions) + 1) * de_novo_quota) > 1
        if targeted_de_novo:
            action = PepMLMTargetedAction(
                branch_key=branch_key,
                generation=generation,
                seed=seed + regular_seed_offset + 3,
                operator_id="pepmlm-targeted-action-v1",
                operator_release_sha256=operator_release_sha256,
                target_sequence_sha256=target_sequence_sha256,
                expected_improvement_metrics=("macrel_amp_probability",),
                protected_metrics=(
                    "guruprasad_instability_index",
                    "macrel_hemolysis_probability",
                    "toxinpred3_hybrid_score",
                ),
                evidence_sha256s=(archive_sha,),
                proposal_mode="de_novo",
                peptide_length=20,
            )
            actions.append(action)
            strategies.append("pepmlm_targeted")
            rationales[action.action_sha256] = (
                "Use target-conditioned PepMLM for a new family because the frozen "
                "de-novo quota requires an additional non-elite proposal."
            )
        elif pepmlm_pool:
            for parent in pepmlm_pool:
                if parent.candidate_id == substitution_parent_id:
                    continue
                try:
                    edit = _mutation(
                        parent,
                        known_sequences=known_sequences,
                        excluded_sequence_sha256s=historical_sequence_sha256s,
                    )
                except ValueError:
                    continue
                action = PepMLMTargetedAction(
                    branch_key=branch_key,
                    generation=generation,
                    seed=seed + regular_seed_offset + 3,
                    operator_id="pepmlm-targeted-action-v2",
                    operator_release_sha256=operator_release_sha256,
                    target_sequence_sha256=target_sequence_sha256,
                    expected_improvement_metrics=("macrel_amp_probability",),
                    protected_metrics=(
                        "guruprasad_instability_index",
                        "macrel_hemolysis_probability",
                        "toxinpred3_hybrid_score",
                    ),
                    evidence_sha256s=_action_evidence(
                        archive_sha, (parent.candidate_id,), delta_receipts
                    ),
                    proposal_mode="masked_substitution",
                    parent_candidate_id=parent.candidate_id,
                    parent_sequence_sha256=parent.sequence_sha256,
                    parent_length=len(parent.sequence),
                    mutation_positions_one_based=(edit.position_zero_based + 1,),
                )
                actions.append(action)
                strategies.append("pepmlm_targeted")
                rationales[action.action_sha256] = (
                    "Ask target-conditioned PepMLM to choose the residue at a frozen "
                    "quality-qualified position, preserving all other parent residues."
                )
                break

    if not actions:
        raise ValueError("multi-front planner produced no executable action")

    def de_novo_count() -> int:
        return sum(
            isinstance(item, DeNovoAction)
            or (isinstance(item, PepMLMTargetedAction) and item.proposal_mode == "de_novo")
            for item in actions
        )

    # A CPU-only successor cannot rely on PepMLM to satisfy a high frozen
    # exploration quota.  Deterministically add executable rule de-novo actions
    # until the quota is true for the *final* batch size.  The quota is capped at
    # 0.5 above, so this converges after at most one addition per non-de-novo action.
    additional_de_novo_count = 0
    while de_novo_count() < math.ceil(len(actions) * de_novo_quota):
        action_seed = seed + regular_seed_offset + 4 + additional_de_novo_count
        proposed = _unique_de_novo_sequence(
            branch_key=branch_key,
            seed=action_seed,
            known_sequences=known_sequences,
            excluded_sequence_sha256s=historical_sequence_sha256s,
            family_reference_sequences=historical_family_representatives,
            family_reference_index=family_reference_index,
            residue_alphabet=de_novo_alphabet,
            transition_alphabets=de_novo_transition_alphabets,
        )
        action = DeNovoAction(
            branch_key=branch_key,
            generation=generation,
            seed=action_seed,
            operator_id="autoresearch-rule-de-novo-v8",
            operator_release_sha256=operator_release_sha256,
            expected_improvement_metrics=("macrel_amp_probability",),
            protected_metrics=(
                "guruprasad_instability_index",
                "macrel_hemolysis_probability",
                "toxinpred3_hybrid_score",
            ),
            evidence_sha256s=(archive_sha,),
            peptide_length=len(proposed),
            proposed_sequence=proposed,
        )
        actions.append(action)
        strategies.append("de_novo")
        known_sequences.add(proposed)
        rationales[action.action_sha256] = (
            "Add a deterministic CPU-executable family opener so the frozen de-novo "
            "quota remains true after mutation and controlled crossover are included."
        )
        additional_de_novo_count += 1
    final_de_novo_count = de_novo_count()
    required_de_novo_count = math.ceil(len(actions) * de_novo_quota)
    if final_de_novo_count < required_de_novo_count:
        raise ValueError("planned action batch violates its de-novo exploration quota")
    requires_generator_gpu = any(isinstance(item, PepMLMTargetedAction) for item in actions)
    return {
        "schema_version": "ampgent.autoresearch-rule-plan.1",
        "branch_key": branch_key,
        "generation": generation,
        "gold_target": gold_target,
        "gold_candidate_count": gold_count,
        "literal_gold_candidate_count": literal_gold_count,
        "instability_score_qualified_gold_candidate_count": gold_count,
        # Deprecated audit alias only; no decision path consumes this field.
        "deprecated_ood_qualified_gold_candidate_count": gold_count,
        "gold_shortfall": max(gold_target - gold_count, 0),
        "quality_gate": "literal-hard-gates+guruprasad-score-lt-50",
        "archive_sha256": archive_sha,
        "strategies": strategies,
        "rationale_by_action_sha256": rationales,
        "actions": [item.model_dump(mode="json") for item in actions],
        "required_parent_candidate_ids": list(required_parent_candidate_ids),
        "forced_parent_action_sha256s": forced_parent_action_sha256s,
        "forced_parent_action_count": len(forced_parent_action_sha256s),
        "unmaterialized_forced_parent_candidate_ids": (unmaterialized_forced_parent_candidate_ids),
        "no_weighted_total_score": True,
        "de_novo_quota": de_novo_quota,
        "de_novo_action_count": final_de_novo_count,
        "required_de_novo_action_count": required_de_novo_count,
        "pepmlm_targeted_enabled": pepmlm_targeted_enabled,
        "requires_generator_gpu": requires_generator_gpu,
        "action_execution_mode": ("generator_gpu" if requires_generator_gpu else "cpu_rule_only"),
        "historical_sequence_exclusion_count": len(historical_sequence_sha256s),
        "historical_sequence_exclusion_sha256": sha256_text(
            "\n".join(sorted(historical_sequence_sha256s))
        ),
        "de_novo_profile_policy": {
            "operator_version": "autoresearch-rule-de-novo-v8",
            "profile_source": de_novo_profile_source,
            "minimum_gold_profile_count": _DE_NOVO_MINIMUM_GOLD_PROFILE_COUNT,
            "family_balanced": True,
            "family_profile_count": len(de_novo_profile),
            "empirical_profile_weight": _DE_NOVO_EMPIRICAL_PROFILE_WEIGHT,
            "activity_safety_prior_weight": 1.0 - _DE_NOVO_EMPIRICAL_PROFILE_WEIGHT,
            "activity_safety_prior_counts": _DE_NOVO_PROFILE_PRIOR_COUNTS,
            "first_order_transition_profile": True,
            "transition_context_count": len(de_novo_transition_alphabets),
        },
        "sequence_prescreen_policy": {
            "instability_method": "Guruprasad-Reddy-Pandit-1990-via-Biopython-ProtParam",
            "instability_max_exclusive": 50.0,
            "all_rule_proposals_share_quality_gate": True,
            "mutation_hydrophobic_run_maximum": _DE_NOVO_MAXIMUM_HYDROPHOBIC_RUN,
            "mutation_hydrophobic_fraction_maximum": (_DE_NOVO_MAXIMUM_HYDROPHOBIC_FRACTION),
            "mutation_net_charge_minimum": _DE_NOVO_MINIMUM_NET_CHARGE,
            "crossover_hydrophobic_run_parent_maximum": True,
            "crossover_hydrophobic_fraction_parent_maximum": True,
            "crossover_charge_loss_max": 1.0,
            "crossover_net_charge_minimum": _DE_NOVO_MINIMUM_NET_CHARGE,
            "de_novo_instability_max_exclusive": 50.0,
            "de_novo_hydrophobic_run_maximum": _DE_NOVO_MAXIMUM_HYDROPHOBIC_RUN,
            "de_novo_hydrophobic_fraction_maximum": (_DE_NOVO_MAXIMUM_HYDROPHOBIC_FRACTION),
            "de_novo_net_charge_minimum": _DE_NOVO_MINIMUM_NET_CHARGE,
            "de_novo_net_charge_maximum": _DE_NOVO_MAXIMUM_NET_CHARGE,
            "de_novo_histidine_fraction_maximum": (
                _DE_NOVO_MAXIMUM_HISTIDINE_FRACTION
            ),
            "toxin_and_hemolysis_remain_score_all_only": True,
        },
    }


__all__ = [
    "GOLD_CANDIDATE_TARGET",
    "PlannerDeltaEvidence",
    "build_multifront_rule_action_plan",
]
