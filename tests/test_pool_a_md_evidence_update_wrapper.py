from pathlib import Path


def test_evidence_update_wrapper_orders_sync_relay_and_refresh():
    source = (
        Path(__file__).parents[1] / "deploy/windows/update_pool_a_md_evidence.ps1"
    ).read_text(encoding="utf-8")
    operations = [
        "sync_pool_a_md_compact_evidence.py",
        "sync_synth_pool_a_md_compact_evidence.ps1",
        "relay_synth_pool_a_md_evidence.ps1",
        "refresh_pool_a_md_reports.py",
    ]
    positions = [source.index(operation) for operation in operations]
    assert positions == sorted(positions)
    assert "$ErrorActionPreference = 'Stop'" in source
    assert "SourceCommit -notmatch '^[0-9a-f]{40}$'" in source
    assert "Remove-Item Env:PYTHONPATH" in source
