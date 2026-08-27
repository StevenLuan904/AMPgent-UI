from pathlib import Path

ROOT = Path(__file__).parents[1]
MONITOR = ROOT / "deploy" / "windows" / "check_ampgent_gpu_capacity.ps1"
HOST19_PROBE_TUNNEL = ROOT / "deploy" / "tunnels" / "start_019_ampgent_probe_tunnel.ps1"
HOST32_PROBE_TUNNEL = ROOT / "deploy" / "tunnels" / "start_032_ampgent_probe_tunnel.ps1"


def test_gpu_capacity_monitor_has_scoped_host32_probe_and_wake_contract() -> None:
    source = MONITOR.read_text(encoding="utf-8")
    assert 'Host19SshTarget = "TargetServerDirect"' in source
    assert "Host19SshPort = 32222" in source
    assert 'Host32SshTarget = "LabServerNewDirect"' in source
    assert "Host32SshPort = 32223" in source
    assert "foreach ($gpuIndex in 0..3)" in source
    assert "read-only observation lanes" in source
    assert 'prohibited_use_scope = @(' in source
    assert 'host = "192.168.99.32"; gpu_indices = @(2, 3)' in source
    assert '$_.gpu_index -in @(2, 3)' in source
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


def test_gpu_probe_tunnels_are_supervised_and_use_nonreserved_local_ports() -> None:
    expected = ((HOST19_PROBE_TUNNEL, "32222"), (HOST32_PROBE_TUNNEL, "32223"))
    for path, port in expected:
        source = path.read_text(encoding="utf-8")
        assert "while ($true)" in source
        assert "ExitOnForwardFailure=yes" in source
        assert "ServerAliveInterval=15" in source
        assert "ServerAliveCountMax=2" in source
        assert "TCPKeepAlive=yes" in source
        assert f"[int]$LocalPort = {port}" in source
        assert "'-L'" in source
    host32 = HOST32_PROBE_TUNNEL.read_text(encoding="utf-8")
    assert "nvidia-smi" not in host32
    assert "GPU2" not in host32
    assert "GPU3" not in host32
