from pathlib import Path
from uuid import uuid4

import yaml

from pepagent.sequence_space_exploration import (
    ExplorationBatchObservation,
    V39ExplorationSchedule,
    build_default_v39_exploration_contract,
    build_v39_round_execution_contract,
    next_exploration_action,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _observation(batch: int, *, novel: int, pareto: int) -> ExplorationBatchObservation:
    return ExplorationBatchObservation(
        batch_ordinal=batch,
        raw_occurrences=1800,
        valid_unique_sequences=1200,
        historically_novel_sequences=novel,
        sequence_family_count=100,
        safety_admissible_sequences=300,
        activity_supported_sequences=80,
        new_pareto_extensions=pareto,
    )


def test_default_exploration_contract_expands_space_without_first_k() -> None:
    contract = build_default_v39_exploration_contract()
    assert contract.expected_maximum_raw_occurrences == 7200
    assert len(contract.cells) == 72
    assert contract.score_all_valid_unique_sequences is True
    assert contract.persist_every_raw_occurrence is True
    assert len({(cell.generator_id, cell.seed) for cell in contract.cells}) == 72


def test_versioned_yaml_and_executable_exploration_contract_agree() -> None:
    payload = yaml.safe_load(
        (
            REPO_ROOT / "config" / "benchmarks" / "amp_sequence_space_exploration_v39.yaml"
        ).read_text(encoding="utf-8")
    )
    contract = build_default_v39_exploration_contract()
    assert payload["version"] == contract.policy_version
    assert payload["budget"]["maximum_rounds"] == contract.maximum_rounds
    assert payload["budget"]["maximum_raw_occurrences"] == (
        contract.expected_maximum_raw_occurrences
    )
    assert payload["sequence_metrics"]["protein_reference_boundary_is_hard_gate"] is False


def test_plateau_changes_strategy_instead_of_stopping() -> None:
    no_novelty = (
        _observation(0, novel=0, pareto=0),
        _observation(1, novel=0, pareto=0),
    )
    assert next_exploration_action(no_novelty, maximum_batches=4) == (
        "switch_generators_seeds_and_underexplored_families"
    )

    no_front_extension = (
        _observation(0, novel=50, pareto=0),
        _observation(1, novel=40, pareto=0),
    )
    assert next_exploration_action(no_front_extension, maximum_batches=4) == (
        "launch_frontier_refinement_with_parent_controls"
    )


def test_budget_exhaustion_freezes_successor_instead_of_silent_stop() -> None:
    observations = tuple(
        _observation(batch, novel=20, pareto=2) for batch in range(4)
    )
    assert next_exploration_action(observations, maximum_batches=4) == (
        "freeze_successor_exploration_contract"
    )


def test_v39_round_projection_binds_eighteen_cells_and_score_all_metrics() -> None:
    contract = build_default_v39_exploration_contract()
    binding, execution = build_v39_round_execution_contract(
        contract, round_ordinal=2
    )
    assert binding.round_ordinal == 2
    assert binding.exploration_contract_sha256 == contract.sha256()
    assert binding.execution_contract_sha256 == execution.sha256()
    assert binding.defer_structure_until_exploration_complete is True
    assert len(execution.cells) == 18
    assert execution.expected_raw_occurrences == 1800
    assert len(execution.required_sequence_metrics) == 12
    assert {cell.seed for cell in execution.cells} == {
        cell.seed for cell in contract.cells if cell.round_ordinal == 2
    }


def test_v39_outer_schedule_requires_unique_pre_frozen_round_runs() -> None:
    contract = build_default_v39_exploration_contract()
    rounds = []
    for ordinal in range(contract.maximum_rounds):
        binding, execution = build_v39_round_execution_contract(
            contract, round_ordinal=ordinal
        )
        run_id = uuid4()
        rounds.append(
            {
                "run_id": run_id,
                "workflow_id": f"v39-round-{ordinal}-{run_id}",
                "request": {
                    "run_id": str(run_id),
                    "exploration_round": binding.model_dump(mode="json"),
                    "execution_contract": execution.model_dump(mode="json"),
                },
            }
        )
    schedule = V39ExplorationSchedule(
        controller_run_id=uuid4(),
        exploration_contract=contract,
        rounds=tuple(rounds),
    )
    assert len(schedule.rounds) == 4
    assert len({item.run_id for item in schedule.rounds}) == 4


def test_v39_outer_schedule_rejects_reused_run_identity() -> None:
    contract = build_default_v39_exploration_contract()
    reused_run_id = uuid4()
    rounds = []
    for ordinal in range(contract.maximum_rounds):
        binding, execution = build_v39_round_execution_contract(
            contract, round_ordinal=ordinal
        )
        rounds.append(
            {
                "run_id": reused_run_id,
                "workflow_id": f"v39-round-{ordinal}",
                "request": {
                    "run_id": str(reused_run_id),
                    "exploration_round": binding.model_dump(mode="json"),
                    "execution_contract": execution.model_dump(mode="json"),
                },
            }
        )
    try:
        V39ExplorationSchedule(
            controller_run_id=uuid4(),
            exploration_contract=contract,
            rounds=tuple(rounds),
        )
    except ValueError as exc:
        assert "identities must be unique" in str(exc)
    else:
        raise AssertionError("reused v39 run identity was accepted")
