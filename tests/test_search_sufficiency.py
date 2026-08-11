from __future__ import annotations

from pathlib import Path

from pepagent.search_sufficiency import (
    EpsilonObjective,
    ParetoFamily,
    SaturationGate,
    assess_saturation,
    build_archive_snapshots,
    pareto_families_from_preregistration,
)
from pepagent.v33_preregistration import load_v33_preregistration

ROOT = Path(__file__).parents[1]


def _candidate(index: int, score: float) -> dict[str, object]:
    return {"id": f"c{index:03d}", "metrics": {"score": score, "risk": score}}


def test_archive_snapshots_preserve_stream_and_track_turnover() -> None:
    family = ParetoFamily(
        name="test",
        objectives=[
            EpsilonObjective(metric_name="score", direction="maximize", epsilon=0.1),
            EpsilonObjective(metric_name="risk", direction="minimize", epsilon=0.1),
        ],
    )
    candidates = [_candidate(index, index / 100) for index in range(1, 201)]
    snapshots = build_archive_snapshots(
        seed=7,
        candidates_in_stream_order=candidates,
        family=family,
    )

    assert [snapshot.checkpoint for snapshot in snapshots] == [25, 50, 100, 150, 200]
    assert snapshots[-1].input_candidate_ids == [f"c{i:03d}" for i in range(1, 201)]
    assert snapshots[-1].previous_checkpoint == 150
    assert 0.0 <= snapshots[-1].archive_turnover_fraction <= 1.0


def test_saturation_requires_all_seed_family_checkpoints() -> None:
    family = ParetoFamily(
        name="stable",
        objectives=[
            EpsilonObjective(metric_name="score", direction="maximize", epsilon=1.0)
        ],
    )
    candidates = [_candidate(index, 1.0) for index in range(1, 201)]
    snapshots = []
    for seed in (1, 2):
        snapshots.extend(
            build_archive_snapshots(
                seed=seed,
                candidates_in_stream_order=candidates,
                family=family,
            )
        )
    assessment = assess_saturation(
        snapshots,
        required_seeds={1, 2},
        required_families={"stable"},
        gate=SaturationGate(
            maximum_new_epsilon_cells_per_increment=1,
            maximum_archive_turnover_fraction=0.10,
        ),
    )

    assert assessment.verdict == "not_saturated_within_protocol_and_budget"
    assert assessment.failing_seed_family_checkpoints

    incomplete = assess_saturation(
        [snapshot for snapshot in snapshots if snapshot.seed == 1],
        required_seeds={1, 2},
        required_families={"stable"},
    )
    assert incomplete.verdict == "inconclusive_due_to_preregistered_shortfall"
    assert incomplete.missing_seed_family_checkpoints


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
