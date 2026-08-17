import pytest

from pepagent.v38_science_execution import (
    V38_METRIC_OBSERVATIONS,
    build_default_v38_sequence_contract,
)
from pepagent.workers.v38_activities import (
    build_v38_metric_evaluation_rows,
    build_v38_score_all_cohort_from_results,
)


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


def _metric_result(plugin_name: str, candidates: list[dict]) -> dict:
    records = []
    for candidate in candidates:
        observations = []
        for metric_name in sorted(V38_METRIC_OBSERVATIONS[plugin_name]):
            is_label = metric_name.endswith("_label")
            observations.append(
                {
                    "metric_name": metric_name,
                    "numeric_value": None if is_label else 0.25,
                    "text_value": "low" if is_label else None,
                    "unit": None,
                }
            )
        records.append(
            {
                "candidate_id": candidate["id"],
                "sequence": candidate["sequence"],
                "status": "complete",
                "observations": observations,
                "raw": {"source": "test"},
            }
        )
    return {
        "result": {
            "plugin": {"name": plugin_name},
            "contract": {
                "reliability": "frozen",
                "default_trust": "screening",
            },
            "status": "complete",
            "records": records,
        },
        "provenance": {},
    }


def test_all_five_metric_plugins_cover_every_candidate_and_exactly_11_metrics() -> None:
    contract = build_default_v38_sequence_contract()
    candidates = [
        {"id": "candidate-a", "sequence": "KACDEFGHIKLM"},
        {"id": "candidate-b", "sequence": "KLMNPQRSTVWY"},
    ]
    observed = set()
    total_rows = 0
    for plugin_name in contract.metric_plugins:
        rows = build_v38_metric_evaluation_rows(
            contract=contract,
            candidates=candidates,
            metric_result=_metric_result(plugin_name, candidates),
        )
        assert len(rows) == len(candidates) * len(V38_METRIC_OBSERVATIONS[plugin_name])
        observed.update(row["metric_name"] for row in rows)
        total_rows += len(rows)
    assert observed == set(contract.required_sequence_metrics)
    assert total_rows == 22


def test_metric_rows_fail_closed_when_one_candidate_observation_is_missing() -> None:
    contract = build_default_v38_sequence_contract()
    candidates = [{"id": "candidate-a", "sequence": "KACDEFGHIKLM"}]
    result = _metric_result("toxicity_risk", candidates)
    result["result"]["records"][0]["observations"].pop()
    with pytest.raises(ValueError, match="missing declared"):
        build_v38_metric_evaluation_rows(
            contract=contract,
            candidates=candidates,
            metric_result=result,
        )
