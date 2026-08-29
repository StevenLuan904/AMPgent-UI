from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

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

GOLD_CANDIDATE_TARGET = 50
_HYDROPHOBIC = frozenset("AVILMFWYC")
_DE_NOVO_MOTIFS = (
    "KRWLAKIRKL",
    "KWKLFKKIGK",
    "RLLRKWLKKL",
    "KRLVKWIKQL",
    "WRKLLKIRKA",
    "KIRWLRKLLK",
    "RKWLKLIRKK",
    "KLLRWKIRQL",
)


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
        candidate_id: tuple(sorted(values))
        for candidate_id, values in receipts.items()
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
    parent: CandidateEvidence, *, known_sequences: set[str] | None = None
) -> ResidueSubstitution:
    """Choose one deterministic edit that breaks a hydrophobic/repetitive patch."""

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
    for candidate_position in positions:
        source = parent.sequence[candidate_position]
        preferred = (
            ("S", "N", "Q", "K")
            if source in _HYDROPHOBIC
            else ("Q", "N", "S", "K")
            if source in {"K", "R"}
            else ("K", "R", "S", "N")
        )
        for replacement in preferred:
            if replacement == source:
                continue
            child = (
                parent.sequence[:candidate_position]
                + replacement
                + parent.sequence[candidate_position + 1 :]
            )
            if child not in known:
                return ResidueSubstitution(
                    position_zero_based=candidate_position,
                    from_residue=source,
                    to_residue=replacement,
                )
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
    *, branch_key: str, seed: int, known_sequences: set[str]
) -> str:
    offset = int(sha256_text(f"{branch_key}:{seed}")[:8], 16)
    for attempt in range(len(_DE_NOVO_MOTIFS) ** 2 * 3):
        first = _DE_NOVO_MOTIFS[(offset + attempt) % len(_DE_NOVO_MOTIFS)]
        second = _DE_NOVO_MOTIFS[
            (offset + 1 + attempt // len(_DE_NOVO_MOTIFS)) % len(_DE_NOVO_MOTIFS)
        ]
        first_rotation = (seed + attempt) % len(first)
        second_rotation = (seed * 3 + attempt) % len(second)
        sequence = (
            first[first_rotation:]
            + first[:first_rotation]
            + second[second_rotation:]
            + second[:second_rotation]
        )
        if sequence not in known_sequences:
            return sequence
    alphabet = "KRLAIGFWQNST"
    for attempt in range(10_000):
        digest = sha256_text(f"{branch_key}:{seed}:fallback:{attempt}")
        sequence = "".join(
            alphabet[int(digest[index : index + 2], 16) % len(alphabet)]
            for index in range(0, 40, 2)
        )
        if sequence not in known_sequences:
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
    gold_target: int = GOLD_CANDIDATE_TARGET,
    de_novo_quota: float = 0.2,
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
        item
        for item in candidates
        if is_instability_score_qualified_wetlab_candidate(item)
    ]
    if not eligible:
        raise ValueError(
            "planner has no literal-hard-gate parent with Guruprasad instability <50; "
            "the successor must use a target-specific strict seed split"
        )
    improvement_counts, delta_receipts = _improvement_index(prior_deltas)
    known_sequences = {item.sequence for item in candidates}
    archive_sha = snapshot.archive_sha256
    literal_gold_count = len(_gold_candidate_ids(snapshot))
    gold_count = len(_instability_score_qualified_gold_candidate_ids(snapshot, by_id))

    actions: list[EvolutionAction] = []
    rationales: dict[str, str] = {}
    strategies: list[str] = []
    substitution_parent_id: str | None = None

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
    if substitution_pool:
        parent = substitution_pool[0]
        substitution_parent_id = parent.candidate_id
        edit = _mutation(parent, known_sequences=known_sequences)
        action = MaskedSubstitutionAction(
            branch_key=branch_key,
            generation=generation,
            seed=seed,
            operator_id="autoresearch-rule-substitution-v1",
            operator_release_sha256=operator_release_sha256,
            expected_improvement_metrics=("guruprasad_instability_index",),
            protected_metrics=(
                "macrel_amp_probability",
                "macrel_hemolysis_probability",
                "toxinpred3_hybrid_score",
            ),
            evidence_sha256s=_action_evidence(
                archive_sha, (parent.candidate_id,), delta_receipts
            ),
            parent_candidate_id=parent.candidate_id,
            parent_sequence_sha256=parent.sequence_sha256,
            substitutions=(edit,),
        )
        actions.append(action)
        strategies.append("substitution")
        rationales[action.action_sha256] = (
            "Edit one hydrophobic/repetitive position from the stability/safety front; "
            "prior positive child deltas break ties while activity and safety stay protected."
        )

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
    for primary in endpoint_pool:
        for donor in endpoint_pool:
            if primary.candidate_id == donor.candidate_id:
                continue
            first_length = max(5, len(primary.sequence) // 2)
            donor_length = min(len(donor.sequence), len(primary.sequence) - first_length)
            if donor_length < 5:
                continue
            fragments = (
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
            child = primary.sequence[:first_length] + donor.sequence[-donor_length:]
            if child not in known_sequences and child not in {primary.sequence, donor.sequence}:
                crossover_pair = (primary, donor)
                crossover_fragments = fragments
                break
        if crossover_pair is not None:
            break
    if crossover_pair is not None and crossover_fragments is not None:
        primary, donor = crossover_pair
        action = ControlledCrossoverAction(
            branch_key=branch_key,
            generation=generation,
            seed=seed + 1,
            operator_id="autoresearch-rule-crossover-v1",
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
            "Combine two distinct activity-model endpoints and retain both parents as "
            "controls; disagreement is preserved rather than averaged."
        )

    proposed = _unique_de_novo_sequence(
        branch_key=branch_key,
        seed=seed + 2,
        known_sequences=known_sequences,
    )
    action = DeNovoAction(
        branch_key=branch_key,
        generation=generation,
        seed=seed + 2,
        operator_id="autoresearch-rule-de-novo-v1",
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
    rationales[action.action_sha256] = (
        "Open a sequence family outside the current archive so elite parents cannot "
        "monopolize exploration."
    )

    pepmlm_pool = _lane_candidates(
        snapshot,
        ("model_disagreement", "novel_family", "activity_consensus"),
        candidates_by_id=by_id,
        improvement_counts=improvement_counts,
    ) or substitution_pool
    targeted_de_novo = math.ceil((len(actions) + 1) * de_novo_quota) > 1
    if targeted_de_novo:
        action = PepMLMTargetedAction(
            branch_key=branch_key,
            generation=generation,
            seed=seed + 3,
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
        parent = next(
            (
                item
                for item in pepmlm_pool
                if item.candidate_id != substitution_parent_id
            ),
            pepmlm_pool[0],
        )
        edit = _mutation(parent)
        action = PepMLMTargetedAction(
            branch_key=branch_key,
            generation=generation,
            seed=seed + 3,
            operator_id="pepmlm-targeted-action-v1",
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
            "Ask target-conditioned PepMLM to choose the residue at a frozen position "
            "from a conflict/novelty front, preserving all other parent residues."
        )

    if not actions:
        raise ValueError("multi-front planner produced no executable action")
    de_novo_count = sum(
        isinstance(item, DeNovoAction)
        or (isinstance(item, PepMLMTargetedAction) and item.proposal_mode == "de_novo")
        for item in actions
    )
    if de_novo_count < math.ceil(len(actions) * de_novo_quota):
        raise ValueError("planned action batch violates its de-novo exploration quota")
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
        "no_weighted_total_score": True,
        "de_novo_quota": de_novo_quota,
    }


__all__ = [
    "GOLD_CANDIDATE_TARGET",
    "PlannerDeltaEvidence",
    "build_multifront_rule_action_plan",
]
