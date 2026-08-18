from pathlib import Path

SCRIPT = Path("deploy/windows/start_v38_local_sequence_workers.ps1")


def test_v38_local_launcher_is_immutable_and_sequence_stage_only() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Get-FileHash -Algorithm SHA256" in text
    assert ".pepagent-source-revision" in text
    assert 'Name = "v38-control"' in text
    assert 'Name = "v38-generator"' in text
    assert 'Name = "v38-metrics"' in text
    assert "v38-boltz" not in text
    assert "v38-rosetta" not in text
    assert "192.168.99.32" not in text


def test_v38_local_launcher_refuses_foreign_or_mismatched_live_processes() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "$previous.ampgent_owned -ne $true" in text
    assert "$previous.source_revision -ne $SourceRevision" in text
    assert "$previous.release_sha256 -ne $ArchiveSha256" in text
    assert "supervisor_pid" in text
    assert "exactly one poller child" in text
    assert "Stop-Process" not in text
