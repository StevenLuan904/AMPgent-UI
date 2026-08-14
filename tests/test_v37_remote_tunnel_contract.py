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
