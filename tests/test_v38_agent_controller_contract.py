from pathlib import Path

import yaml

from pepagent.v38_agent_controller_cli import (
    _capacity_blocker,
    _probe_services,
    _refinement_provider_blocker,
    _validate_panel,
)

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_panel_has_two_qualified_branches_and_verified_coordinates() -> None:
    witnesses = _validate_panel(
        ROOT / "config" / "targets" / "amp_multitarget_panel_v38.yaml",
        ROOT / "var" / "target-qualification" / "v38",
    )
    assert [item.target_key for item in witnesses] == [
        "ec_gyrA_lei800",
        "se_pbp2a_allosteric",
    ]
    assert all(item.primary_pocket_id != item.wrong_pocket_id for item in witnesses)


def test_benchmark_distinguishes_controller_from_formal_science_submission() -> None:
    benchmark = yaml.safe_load(
        (ROOT / "config" / "benchmarks" / "amp_sequence_first_multitarget_v38.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert benchmark["scope"]["formal_run_authorized"] is True
    assert benchmark["scope"]["formal_run_submitted"] is False
    assert benchmark["sequence_first_agent"]["raw_proposal_policy"][
        "score_all_valid_unique_proposals_before_promotion"
    ] is True
    assert benchmark["sequence_first_agent"]["structure_admission"][
        "no_structure_dispatch_while_refinement_required"
    ] is True


def test_controller_supervisor_runs_capacity_and_five_minute_ticks() -> None:
    script = (
        ROOT / "deploy" / "windows" / "start_v38_agent_controller.ps1"
    ).read_text(encoding="utf-8")
    assert "check_ampgent_gpu_capacity.ps1" in script
    assert "--mode tick" in script
    assert script.count("$LASTEXITCODE -ne 0") == 2
    assert "[int]$TickSeconds = 300" in script
    assert "Start-Sleep -Seconds $TickSeconds" in script
    assert ".32" not in script


def test_controller_has_live_control_plane_probes() -> None:
    assert callable(_probe_services)


def test_controller_distinguishes_unreachable_busy_and_idle_gpu_capacity() -> None:
    assert _capacity_blocker({"observations": []}) == (
        "authorized_structure_gpu_currently_unreachable"
    )
    observed = [{"status": "observed"}, {"status": "observed"}]
    assert _capacity_blocker({"observations": observed, "idle_gpu_keys": []}) == (
        "authorized_structure_gpu_currently_busy"
    )
    assert _capacity_blocker(
        {"observations": observed, "idle_gpu_keys": ["192.168.99.19:4"]}
    ) is None


def test_controller_reports_unaccepted_refinement_provider_release() -> None:
    assert _refinement_provider_blocker() == (
        "v38_refinement_provider_release_not_delivered"
    )
