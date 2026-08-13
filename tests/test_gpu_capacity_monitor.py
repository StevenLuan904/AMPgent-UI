from pathlib import Path

ROOT = Path(__file__).parents[1]
MONITOR = ROOT / "deploy" / "windows" / "check_ampgent_gpu_capacity.ps1"


def test_gpu_capacity_monitor_has_scoped_host32_probe_and_wake_contract() -> None:
    source = MONITOR.read_text(encoding="utf-8")
    assert 'Host32SshTarget = "LabServerNewDirect"' in source
    assert "foreach ($gpuIndex in 0..1)" in source
    assert "foreach ($gpuIndex in 2..3)" not in source
    assert 'host = "192.168.99.32"; gpu_indices = @(2, 3)' in source
    assert "observation_keys = $observationKeys" in source
    assert '"WAKE_REQUIRED=$($wakeRequired.ToString().ToLowerInvariant())"' in source


def test_gpu_capacity_monitor_never_uses_unscoped_nvidia_smi() -> None:
    source = MONITOR.read_text(encoding="utf-8")
    command_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith("nvidia-smi")
    ]
    assert command_lines
    assert all("-i $GpuIndex" in line for line in command_lines)


def test_gpu_capacity_monitor_suppresses_unreadable_proc_environ_errors() -> None:
    source = MONITOR.read_text(encoding="utf-8")
    assert '{ tr \'\\0\' \'\\n\' < "`$p/environ"; } 2>/dev/null' in source
