from __future__ import annotations

from pathlib import Path

from pepagent.search_sufficiency import (
    EpsilonObjective,
    ParetoFamily,
    SaturationGate,
    assess_saturation,
    build_archive_snapshots,
    build_cross_seed_attainment_assessments,
    build_leave_one_objective_out_assessments,
    pareto_families_from_preregistration,
)
from pepagent.v33_preregistration import load_v33_preregistration

ROOT = Path(__file__).parents[1]


def _candidate(index: int, score: float, risk: float | None = None) -> dict[str, object]:
    return {
        "id": f"c{index:03d}",
        "metrics": {"score": score, "risk": score if risk is None else risk},
    }


def _two_objective_family(name: str = "test") -> ParetoFamily:
    return ParetoFamily(
        name=name,
        objectives=[
            EpsilonObjective(metric_name="score", direction="maximize", epsilon=0.1),
            EpsilonObjective(metric_name="risk", direction="minimize", epsilon=0.1),
        ],
    )


def test_archive_snapshots_preserve_stream_cells_cost_and_turnover() -> None:
    candidates = [_candidate(index, index / 100) for index in range(1, 201)]
    snapshots = build_archive_snapshots(
        seed=7,
        candidates_in_stream_order=candidates,
        family=_two_objective_family(),
        cumulative_cost_by_checkpoint={25: 25, 50: 50, 100: 100, 150: 150, 200: 200},
    )

    assert [snapshot.checkpoint for snapshot in snapshots] == [25, 50, 100, 150, 200]
    assert snapshots[-1].input_candidate_ids == [f"c{i:03d}" for i in range(1, 201)]
    assert snapshots[-1].previous_checkpoint == 150
    assert snapshots[-1].increment_candidate_count == 50
    assert snapshots[-1].increment_cost_units == 50
    assert 0.0 <= snapshots[-1].archive_turnover_fraction <= 1.0
    assert 0.0 <= snapshots[-1].epsilon_cell_turnover_fraction <= 1.0
    assert set(map(tuple, snapshots[-1].epsilon_cells)).issubset(
        set(map(tuple, snapshots[-1].cumulative_epsilon_cells))
    )


def test_saturation_uses_cell_stability_not_neutral_identity_growth() -> None:
    family = _two_objective_family("stable")
    candidates = [_candidate(index, 1.0) for index in range(1, 201)]
    snapshots = []
    loo = []
    development = {1, 2}
    confirmation = {3, 4}
    for seed in sorted(development | confirmation):
        snapshots.extend(
            build_archive_snapshots(
                seed=seed,
                candidates_in_stream_order=candidates,
                family=family,
                cumulative_cost_by_checkpoint={
                    25: 25,
                    50: 50,
                    100: 100,
                    150: 150,
                    200: 200,
                },
            )
        )
        loo.extend(
            build_leave_one_objective_out_assessments(
                seed=seed,
                candidates_in_stream_order=candidates,
                family=family,
                checkpoint=200,
                omitted_metrics={"risk"},
            )
        )
    assessment = assess_saturation(
        snapshots,
        required_seeds=development | confirmation,
        required_families={"stable"},
        development_seeds=development,
        confirmation_seeds=confirmation,
        leave_one_objective_out_assessments=loo,
        required_omitted_metrics_by_family={"stable": {"risk"}},
    )

    assert snapshots[-1].archive_turnover_fraction == 0.25
    assert snapshots[-1].epsilon_cell_turnover_fraction == 0.0
    assert assessment.verdict == "saturated_within_protocol_and_budget"
    assert assessment.cross_seed_attainment_assessments[0].symmetric_recurrence_passed
    assert not assessment.model_fragility_warnings


def test_saturation_requires_all_seed_family_checkpoints() -> None:
    family = _two_objective_family("stable")
    candidates = [_candidate(index, 1.0) for index in range(1, 201)]
    snapshots = build_archive_snapshots(
        seed=1,
        candidates_in_stream_order=candidates,
        family=family,
    )
    incomplete = assess_saturation(
        snapshots,
        required_seeds={1, 2},
        required_families={"stable"},
        gate=SaturationGate(
            require_cross_seed_attainment=False,
            require_cost_observations=False,
            require_leave_one_objective_out_reporting=False,
        ),
    )

    assert incomplete.verdict == "inconclusive_due_to_preregistered_shortfall"
    assert incomplete.missing_seed_family_checkpoints


def test_cross_seed_attainment_rejects_disjoint_tradeoff_regions() -> None:
    family = _two_objective_family("tradeoff")
    snapshots = []
    for seed in (1, 2):
        candidates = [_candidate(index, 10.0, 10.0) for index in range(1, 201)]
        snapshots.extend(
            build_archive_snapshots(
                seed=seed,
                candidates_in_stream_order=candidates,
                family=family,
            )
        )
    for seed in (3, 4):
        candidates = [_candidate(index, 0.0, 0.0) for index in range(1, 201)]
        snapshots.extend(
            build_archive_snapshots(
                seed=seed,
                candidates_in_stream_order=candidates,
                family=family,
            )
        )

    attainment = build_cross_seed_attainment_assessments(
        snapshots,
        development_seeds={1, 2},
        confirmation_seeds={3, 4},
        required_families={"tradeoff"},
        checkpoint=200,
    )
    assessment = assess_saturation(
        snapshots,
        required_seeds={1, 2, 3, 4},
        required_families={"tradeoff"},
        development_seeds={1, 2},
        confirmation_seeds={3, 4},
        gate=SaturationGate(
            require_cost_observations=False,
            require_leave_one_objective_out_reporting=False,
        ),
    )

    assert not attainment[0].symmetric_recurrence_passed
    assert assessment.verdict == "not_saturated_within_protocol_and_budget"
    assert assessment.failed_dimensions == ["cross_seed_attainment;family=tradeoff"]


def test_leave_one_objective_out_records_model_dependence_without_scalarizing() -> None:
    family = _two_objective_family("models")
    candidates = [
        _candidate(1, 1.0, 0.0),
        _candidate(2, 2.0, 1.0),
        _candidate(3, 3.0, 2.0),
    ]
    result = build_leave_one_objective_out_assessments(
        seed=9,
        candidates_in_stream_order=candidates,
        family=family,
        checkpoint=3,
        omitted_metrics={"risk"},
    )

    assert len(result) == 1
    assert result[0].full_archive_candidate_ids == ["c001", "c002", "c003"]
    assert result[0].reduced_archive_candidate_ids == ["c003"]
    assert result[0].selection_jaccard == 1 / 3


def test_archive_families_are_bound_to_frozen_preregistration() -> None:
    manifest = load_v33_preregistration(
        ROOT / "config" / "benchmarks" / "amp_charge_search_sufficiency_v33.yaml"
    )
    families = pareto_families_from_preregistration(manifest)

    assert set(families) == {
        "membrane",
        "activity_mic",
        "risk_control",
    }
    assert all(
        objective.epsilon > 0
        for family in families.values()
        for objective in family.objectives
    )


def test_archive_records_dominance_witness_for_removed_candidate() -> None:
    family = ParetoFamily(
        name="single",
        objectives=[
            EpsilonObjective(metric_name="score", direction="maximize", epsilon=0.1)
        ],
    )
    candidates = [_candidate(index, float(index)) for index in range(1, 4)]
    snapshots = build_archive_snapshots(
        seed=3,
        candidates_in_stream_order=candidates,
        family=family,
        checkpoints=(1, 2, 3),
    )

    assert snapshots[1].removed_candidate_ids == ["c001"]
    assert snapshots[1].removed_candidate_dominance_witnesses == {"c001": ["c002"]}
    assert snapshots[2].removed_candidate_dominance_witnesses == {"c002": ["c003"]}
