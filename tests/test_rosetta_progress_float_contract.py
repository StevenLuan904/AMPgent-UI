from pathlib import Path


SCRIPT = Path("deploy/windows/show_rosetta_progress_float.ps1")


def test_progress_float_tracks_current_gap_batches_and_derives_totals() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "rosetta-poola-v7-pbp2a-extension59" in source
    assert "rosetta-poola-v8-gap-targets-extension177" in source
    assert "$host19Total += $pending + $completedItem + $failedItem" in source
    assert "$synthTotal += $pending + $completedItem + $failedItem" in source
    assert "host19_total = 345" not in source
    assert "total = 648" not in source


def test_progress_float_is_single_instance() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "Local\\AMPgentRosettaProgressFloat" in source
    assert "$singleton.WaitOne(0)" in source
    assert "if (-not $singletonAcquired)" in source
