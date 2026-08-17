import pytest

from pepagent.v38_science_execution import build_default_v38_sequence_contract
from pepagent.workers.v38_activities import build_v38_score_all_cohort_from_results


def _sequence(index: int) -> str:
    alphabet = "ACDEFGHIKLMNPQRSTVWY"
    residues = []
    value = index
    for _ in range(11):
        residues.append(alphabet[value % len(alphabet)])
        value //= len(alphabet)
    return "K" + "".join(residues)


def _generated_cells() -> list[dict]:
    contract = build_default_v38_sequence_contract()
    return [
        {
            "result": {
                "generator_id": cell.generator_id,
                "seed": cell.seed,
                "raw_proposal_budget": 100,
                "records": [
                    {
                        "raw_rank": rank,
                        "sequence": _sequence(cell.ordinal * 100 + rank),
                    }
                    for rank in range(1, 101)
                ],
            }
        }
        for cell in contract.cells
    ]


def test_generation_results_form_one_complete_score_all_cohort() -> None:
    contract = build_default_v38_sequence_contract()
    cohort = build_v38_score_all_cohort_from_results(contract, _generated_cells())
    assert cohort.raw_occurrence_count == 900
    assert cohort.promoted_unique_count == 900
    assert cohort.invalid_count == 0
    assert cohort.duplicate_count == 0


def test_generation_results_reject_missing_or_duplicate_cells() -> None:
    contract = build_default_v38_sequence_contract()
    generated = _generated_cells()
    with pytest.raises(ValueError, match="exactly cover"):
        build_v38_score_all_cohort_from_results(contract, generated[:-1])
    with pytest.raises(ValueError, match="duplicated"):
        build_v38_score_all_cohort_from_results(
            contract,
            [generated[0], generated[0], *generated[2:]],
        )


def test_generation_results_reject_noncontiguous_raw_ranks() -> None:
    contract = build_default_v38_sequence_contract()
    generated = _generated_cells()
    generated[0]["result"]["records"][4]["raw_rank"] = 99
    with pytest.raises(ValueError, match="raw ranks"):
        build_v38_score_all_cohort_from_results(contract, generated)
