from uuid import uuid4

import pytest
from pydantic import ValidationError

from pepagent.v38_science_execution import (
    RawProposal,
    RefinementChildProposal,
    build_default_v38_sequence_contract,
    build_score_all_proposal_cohort,
    unchanged_parent_control_sha256,
)
from pepagent.v38_sequence_first_multitarget import KnowledgeUseTrace

SHA_A = "a" * 64
SHA_B = "b" * 64


def _valid_sequence(index: int) -> str:
    alphabet = "ACDEFGHIKLMNPQRSTVWY"
    return "K" + "".join(alphabet[(index + offset) % len(alphabet)] for offset in range(11))


def _frozen_proposals() -> list[RawProposal]:
    contract = build_default_v38_sequence_contract()
    return [
        RawProposal(
            generator_id=cell.generator_id,
            seed=cell.seed,
            raw_rank=rank,
            sequence=_valid_sequence(cell.ordinal * 100 + rank),
        )
        for cell in contract.cells
        for rank in range(1, 101)
    ]


def test_default_contract_is_nine_cells_and_900_score_all_occurrences() -> None:
    contract = build_default_v38_sequence_contract()
    assert len(contract.cells) == 9
    assert contract.expected_raw_occurrences == 900
    assert all(cell.requested_proposals == 100 for cell in contract.cells)
    assert contract.score_all_valid_unique_proposals
    assert contract.first_k_retention_forbidden
    assert len(contract.required_sequence_metrics) == 12
    assert {
        "llamp_log10_mic_um",
        "amp_read_log10_mic_um",
        "macrel_amp_probability",
        "toxinpred3_label",
        "macrel_hemolysis_label",
        "guruprasad_instability_index",
    }.issubset(contract.required_sequence_metrics)


def test_score_all_cohort_preserves_every_occurrence_without_first_k_cutoff() -> None:
    contract = build_default_v38_sequence_contract()
    proposals = _frozen_proposals()
    cohort = build_score_all_proposal_cohort(contract, proposals)
    assert cohort.raw_occurrence_count == 900
    assert cohort.promoted_unique_count + cohort.duplicate_count == 900
    assert cohort.invalid_count == 0
    assert cohort.first_k_retention_used is False
    assert len(cohort.occurrences) == 900


def test_score_all_cohort_records_invalid_and_duplicate_denominators() -> None:
    contract = build_default_v38_sequence_contract()
    proposals = _frozen_proposals()
    proposals[0] = proposals[0].model_copy(update={"sequence": "BAD*"})
    proposals[1] = proposals[1].model_copy(update={"sequence": proposals[2].sequence})
    cohort = build_score_all_proposal_cohort(contract, proposals)
    assert cohort.invalid_count == 1
    assert cohort.duplicate_count >= 1
    assert cohort.raw_occurrence_count == 900


def test_score_all_rejects_incomplete_cell_even_if_total_is_large() -> None:
    contract = build_default_v38_sequence_contract()
    proposals = _frozen_proposals()
    with pytest.raises(ValueError, match="exactly cover"):
        build_score_all_proposal_cohort(contract, proposals[:-1])


def test_refinement_child_requires_change_adopted_card_and_parent_control() -> None:
    parent_id = uuid4()
    parent = "KLLKLLKLLKLL"
    control = unchanged_parent_control_sha256(
        parent_candidate_id=parent_id,
        parent_sequence=parent,
        refinement_round=1,
    )
    trace = KnowledgeUseTrace(
        card_id="card-amp-edit",
        query_sha256=SHA_A,
        passage_sha256=SHA_B,
        decision="adopt",
        rationale="preserve charge while reducing a hydrophobic patch",
    )
    child = RefinementChildProposal(
        parent_candidate_id=parent_id,
        parent_sequence=parent,
        child_sequence="KLLKLAKLLKLL",
        refinement_round=1,
        mutation_rationale="replace one leucine to reduce the contiguous hydrophobic patch",
        knowledge_traces=(trace,),
        unchanged_parent_control_sha256=control,
    )
    assert child.parent_candidate_id == parent_id

    with pytest.raises(ValidationError, match="must differ"):
        RefinementChildProposal(
            parent_candidate_id=parent_id,
            parent_sequence=parent,
            child_sequence=parent,
            refinement_round=1,
            mutation_rationale="unchanged child is forbidden",
            knowledge_traces=(trace,),
            unchanged_parent_control_sha256=control,
        )
