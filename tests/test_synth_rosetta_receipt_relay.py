from __future__ import annotations

from pathlib import Path


def test_relay_keeps_structure_remote_and_uses_external_credential() -> None:
    source = (
        Path(__file__).parents[1]
        / "deploy"
        / "windows"
        / "relay_synth_rosetta_receipts.ps1"
    ).read_text(encoding="utf-8")

    assert "completion_receipt" not in source
    assert "rosetta_result" not in source
    assert "ConvertTo-SecureString" in source
    assert "REMOTE_GPU_TARGET_PASSWORD" in source
    assert "--bundle-jsonl-stdin" in source
    assert "Copy-Item" not in source
    assert "scp" not in source.casefold()
    assert "Remove-Item" in source
