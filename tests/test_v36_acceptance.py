from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from pepagent.v36_acceptance import (
    V36AAcceptanceContract,
    load_v36a_acceptance_contract,
)

ROOT = Path(__file__).parents[1]
CONFIG = ROOT / "config" / "benchmarks" / "amp_harness_synthetic_acceptance_v36a.yaml"


def test_v36a_freezes_synthetic_database_acceptance_without_real_execution() -> None:
    contract = load_v36a_acceptance_contract(CONFIG)
    assert contract.execution_status == "preregistered_not_authorized"
    assert contract.implementation_revision is None
    assert contract.authorization.execution_authorized is False
    assert contract.authorization.submitted is False
    assert contract.data_boundary.synthetic_only is True
    assert contract.data_boundary.candidate_count == 0
    assert contract.data_boundary.evaluation_count == 0
    assert [scenario.terminal_decision for scenario in contract.scenarios] == [
        "promote_for_declared_scope",
        "rollback_to_registered_ancestor",
    ]
    assert contract.aggregate_expected_counts.outcome_count == 60
    assert (
        contract.acceptance_verdicts.success_does_not_authorize_real_harness_evolution
        is True
    )


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("authorization", "execution_authorized"), True, "Input should be False"),
        (("data_boundary", "candidate_count"), 1, "Input should be 0"),
        (
            ("aggregate_expected_counts", "evaluation_count"),
            1,
            "Input should be 0",
        ),
        (
            ("acceptance_verdicts", "success_does_not_prove_harness_improvement"),
            False,
            "Input should be True",
        ),
    ],
)
def test_v36a_rejects_scope_or_claim_drift(
    path: tuple[str, str], value: object, message: str
) -> None:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    payload[path[0]][path[1]] = value
    with pytest.raises(ValueError, match=message):
        V36AAcceptanceContract.model_validate(payload)


def test_v36a_rejects_parent_or_migration_byte_drift() -> None:
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    parent = (CONFIG.parent / payload["parent_contract_path"]).resolve()
    migration = (ROOT / payload["migration"]["path"]).resolve()
    with TemporaryDirectory() as directory:
        root = Path(directory)
        benchmark_dir = root / "config" / "benchmarks"
        migration_dir = root / "migrations" / "versions"
        benchmark_dir.mkdir(parents=True)
        migration_dir.mkdir(parents=True)
        config_copy = benchmark_dir / CONFIG.name
        parent_copy = benchmark_dir / parent.name
        migration_copy = migration_dir / migration.name
        config_copy.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        parent_copy.write_bytes(parent.read_bytes() + b"\n# drift\n")
        migration_copy.write_bytes(migration.read_bytes())
        with pytest.raises(ValueError, match="parent harness contract checksum mismatch"):
            load_v36a_acceptance_contract(config_copy)

        parent_copy.write_bytes(parent.read_bytes())
        migration_copy.write_bytes(migration.read_bytes() + b"\n# drift\n")
        with pytest.raises(ValueError, match="migration checksum mismatch"):
            load_v36a_acceptance_contract(config_copy)
