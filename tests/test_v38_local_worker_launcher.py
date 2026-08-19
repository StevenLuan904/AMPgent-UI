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
    assert "$previous.foreign -eq $true" in text
    assert "$previous.role -ne $roleName" in text
    assert "supervisor_pid" in text
    assert "process tree does not match its exact receipt" in text


def test_v38_local_launcher_replacement_is_opt_in_and_exactly_scoped() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "[switch]$ReplaceOwned" in text
    assert "if (-not $ReplaceOwned)" in text
    assert "use -ReplaceOwned" in text
    ownership_gate = text.index("$previous.ampgent_owned -ne $true")
    tree_gate = text.index("process tree does not match its exact receipt")
    stop = text.index("Stop-Process")
    assert ownership_gate < stop
    assert tree_gate < stop
    assert "Stop-Process -Id ([int]$previous.pid)" in text
    assert "Stop-Process -Id ([int]$previous.supervisor_pid)" in text
    assert "Treat that exact-PID exit race as successful" in text


def test_v38_local_launcher_uses_short_workspace_work_root() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert '$workRoot = [IO.Path]::GetFullPath((Join-Path $workspace "var/work-v38"))' in text
    assert '$env:PEPAGENT_WORK_ROOT = $workRoot' in text
    assert '$env:PEPAGENT_WORK_ROOT = $oldWorkRoot' in text
    assert 'work_root = $workRoot' in text
    assert '$env:PEPAGENT_WORK_ROOT = $releasePath' not in text


def test_v38_local_launcher_uses_no_site_python_with_explicit_packages() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"-S", "-m", "pepagent.workers.v38_temporal_worker"' in text
    assert 'Lib\\site-packages' in text
    assert '$env:PYTHONPATH = "$sitePackages;' in text
