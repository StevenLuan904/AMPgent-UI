from pepagent.workers.v37_activities import _builtin_metric_output_contract


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
