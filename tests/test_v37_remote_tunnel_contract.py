from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TUNNEL = ROOT / "deploy/tunnels/start_019_pepagent_tunnels.ps1"
WORKER = ROOT / "deploy/remote/start_v37_worker.sh"


def test_host19_tunnel_supervisor_covers_all_worker_control_services() -> None:
    source = TUNNEL.read_text(encoding="utf-8")

    assert "while ($true)" in source
    assert "ExitOnForwardFailure=yes" in source
    assert "ServerAliveInterval=15" in source
    assert "ServerAliveCountMax=2" in source
    assert "TCPKeepAlive=yes" in source
    assert "127.0.0.1:55432:127.0.0.1:55432" in source
    assert "127.0.0.1:17233:127.0.0.1:7233" in source
    assert "127.0.0.1:19000:127.0.0.1:9000" in source
    assert "huangyueshan@192.168.99.19" in source
    assert "192.168.99.32" not in source


def test_v37_worker_fails_before_launch_when_a_service_tunnel_is_absent() -> None:
    source = WORKER.read_text(encoding="utf-8")

    preflight = source.index('"$PYTHON" - <<\'PY\'')
    launch = source.index("nohup env")
    assert preflight < launch
    assert '("127.0.0.1", 55432)' in source
    assert '("127.0.0.1", 17233)' in source
    assert '("127.0.0.1", 19000)' in source
    assert "socket.create_connection(address, timeout=3)" in source
    assert "v37 service tunnel preflight failed" in source


def test_v37_boltz_worker_validates_the_real_provider_runtime_before_gpu_claim() -> None:
    source = WORKER.read_text(encoding="utf-8")

    executable_gate = source.index('[[ -x "$BOLTZ_EXECUTABLE" ]]')
    package_gate = source.index('"$PYTHON" -c \'import boltz\'')
    cli_gate = source.index('BOLTZ_HELP="$($BOLTZ_EXECUTABLE predict --help)"')
    gpu_claim = source.index('OCCUPANTS="$(nvidia-smi')
    launch = source.index("nohup env")
    assert executable_gate < package_gate < cli_gate < gpu_claim < launch
    assert 'BOLTZ_EXECUTABLE="$ROOT/envs/gpu-worker-py311-v1/bin/boltz"' in source
    for option in (
        "--cache",
        "--diffusion_samples",
        "--recycling_steps",
        "--sampling_steps",
        "--use_msa_server",
        "--use_potentials",
        "--write_full_pae",
        "--write_full_pde",
    ):
        assert option in source
