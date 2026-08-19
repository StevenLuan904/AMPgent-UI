from pathlib import Path

SCRIPT = Path("deploy/remote/start_v38_worker.sh")


def test_v38_remote_launcher_is_role_and_placement_scoped() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "v38-boltz|v38-rosetta" in text
    assert "192.168.99.32:/data1/luanhaoyang/pepagent:v38-boltz:1" in text
    assert "synth:/sdd_data/pepagent:v38-rosetta:cpu" in text
    assert "v38-boltz:2" not in text
    assert "v38-boltz:3" not in text
    assert "GPU2" not in text
    assert "GPU3" not in text


def test_v38_remote_launcher_verifies_immutable_runtime_before_launch() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    launch = text.index("nohup env")
    required_before_launch = (
        ".pepagent-source-revision",
        "managed Boltz executable is missing",
        "2.2.1",
        "v38 service tunnel preflight failed",
        "ENVIRONMENT_SHA256",
        "WEIGHTS_SHA256",
        "GPU has compute processes; refusing launch",
    )
    for marker in required_before_launch:
        assert text.index(marker) < launch
    assert "pepagent.workers.v38_temporal_worker" in text
    assert "pepagent.workers.temporal_worker" not in text


def test_v38_remote_launcher_never_replaces_a_live_process() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "replacement requires external exact-ownership migration" in text
    assert "kill -0" in text
    assert "kill -9" not in text
    assert "pkill" not in text
    assert "schema=v38.remote-worker-receipt.1" in text
    assert "ampgent_owned=true" in text
    assert "foreign=false" in text
