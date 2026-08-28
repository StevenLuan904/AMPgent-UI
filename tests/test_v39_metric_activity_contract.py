import uuid

import pytest

from pepagent.v37_attempt_ledger import V37AttemptContext
from pepagent.workers.v37_activities import (
    _builtin_metric_output_contract,
    _metric_activity_logical_id,
)


def test_v39_physicochemical_activity_accepts_instability_output_contract() -> None:
    adapter_version, metrics = _builtin_metric_output_contract(
        "physicochemical-developability-modlamp-4.3.2-biopython-v39"
    )

    assert adapter_version == "2026.08.22-v2"
    assert metrics == frozenset(
        {
            "hydrophobic_moment_eisenberg",
            "hydrophobic_ratio_modlamp",
            "maximum_hydrophobic_run",
            "net_charge_ph7_4",
            "guruprasad_instability_index",
        }
    )


def test_legacy_physicochemical_activity_contract_remains_unchanged() -> None:
    adapter_version, metrics = _builtin_metric_output_contract(
        "physicochemical-developability-modlamp-4.3.2-v37"
    )

    assert adapter_version == "2026.08.04-v1"
    assert "guruprasad_instability_index" not in metrics


def test_metric_attempt_identity_distinguishes_score_all_activity_invocations() -> None:
    """Regression for FGF2 v5 ActivityIds 7 and 22 sharing one old ledger."""

    run_id = uuid.UUID("04065844-cd2b-5ce0-8ee4-99f7f3513403")
    first_logical_id = _metric_activity_logical_id(
        protocol="v38",
        plugin_name="physicochemical_developability",
        temporal_activity_id="7",
    )
    later_logical_id = _metric_activity_logical_id(
        protocol="v38",
        plugin_name="physicochemical_developability",
        temporal_activity_id="22",
    )

    assert first_logical_id != later_logical_id
    assert (
        V37AttemptContext(run_id, first_logical_id, "evaluate_v38_sequence_metric", 1).aggregate_id
        != V37AttemptContext(
            run_id, later_logical_id, "evaluate_v38_sequence_metric", 1
        ).aggregate_id
    )


def test_metric_attempt_identity_is_stable_across_true_temporal_retry() -> None:
    logical_id = _metric_activity_logical_id(
        protocol="v38",
        plugin_name="physicochemical_developability",
        temporal_activity_id="22",
    )

    assert logical_id == _metric_activity_logical_id(
        protocol="v38",
        plugin_name="physicochemical_developability",
        temporal_activity_id="22",
    )
    with pytest.raises(ValueError, match="identity is incomplete"):
        _metric_activity_logical_id(
            protocol="v38",
            plugin_name="physicochemical_developability",
            temporal_activity_id="",
        )
