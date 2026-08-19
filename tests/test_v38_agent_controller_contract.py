from pathlib import Path

import yaml

from pepagent.v38_agent_controller_cli import (
    _capacity_blocker,
    _infer_science_stage,
    _owned_structure_worker_pid,
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
    assert "$python -S -m pepagent.v38_agent_controller_cli" in script
    assert ".venv\\Lib\\site-packages" in script
    assert script.count("$LASTEXITCODE -ne 0") == 2
    assert "[int]$TickSeconds = 300" in script
    assert "Start-Sleep -Seconds $TickSeconds" in script
    assert ".32" not in script


def test_controller_reads_latest_versioned_owned_structure_placement(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "var" / "state" / "controller.json"
    placement_root = tmp_path / "var" / "run" / "v38-workers"
    placement_root.mkdir(parents=True)
    (placement_root / "v38-structure-placement-recovery.json").write_text(
        """{
  "schema_version": "v38.worker-placement.1",
  "workers": {
    "v38-boltz": {
      "resource": "192.168.99.32:1",
      "pid": 168748,
      "ampgent_owned": true,
      "foreign": false
    }
  }
}\n""",
        encoding="utf-8",
    )
    assert _owned_structure_worker_pid(state_path) == 168748


def test_controller_has_live_control_plane_probes() -> None:
    assert callable(_probe_services)


def test_controller_distinguishes_unreachable_busy_and_idle_gpu_capacity() -> None:
    assert _capacity_blocker({"observations": []}) == (
        "authorized_structure_gpu_currently_unreachable"
    )
    observed = [
        {"host": "192.168.99.19", "gpu_index": 6, "status": "observed"},
        {"host": "192.168.99.32", "gpu_index": 1, "status": "observed"},
    ]
    assert _capacity_blocker({"observations": observed, "idle_gpu_keys": []}) == (
        "authorized_structure_gpu_currently_busy"
    )
    assert _capacity_blocker(
        {"observations": observed, "idle_gpu_keys": ["192.168.99.32:1"]}
    ) is None
    assert _capacity_blocker(
        {"observations": observed, "idle_gpu_keys": ["192.168.99.19:6"]}
    ) == "authorized_structure_gpu_currently_busy"
    owned_ready = [
        {
            "host": "192.168.99.32",
            "gpu_index": 1,
            "status": "observed",
            "compute_processes": None,
            "cuda_visible_devices_declarations": "769035",
        }
    ]
    assert _capacity_blocker(
        {"observations": owned_ready, "idle_gpu_keys": []},
        owned_structure_worker_pid=769035,
    ) is None
    assert _capacity_blocker(
        {"observations": owned_ready, "idle_gpu_keys": []},
        owned_structure_worker_pid=123,
    ) == "authorized_structure_gpu_currently_busy"


def test_controller_accepts_frozen_refinement_provider_release() -> None:
    assert _refinement_provider_blocker() is None


def test_controller_infers_submitted_science_stage_from_durable_evidence() -> None:
    counts = {
        "candidates": 900,
        "occurrences": 900,
        "tool_calls": 9,
        "decisions": 0,
        "structure_evidence_records": 0,
        "evaluations": 11,
        "replay_evidence_links": 0,
    }
    assert _infer_science_stage(counts, run_status="running") == "sequence_metrics"
    counts["decisions"] = 1
    assert _infer_science_stage(counts, run_status="running") == "sequence_admission"
    counts["structure_evidence_records"] = 1
    assert _infer_science_stage(counts, run_status="running") == (
        "parallel_target_structure"
    )
    counts["replay_evidence_links"] = 1
    assert _infer_science_stage(counts, run_status="running") == "pareto_and_replay"
    assert _infer_science_stage(counts, run_status="succeeded") == "terminal"
