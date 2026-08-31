from __future__ import annotations

from pathlib import Path


def test_v3_launcher_is_additive_and_has_no_gpu_role() -> None:
    text = Path(
        "deploy/windows/start_autoresearch_cpu_successor_v3_workers.ps1"
    ).read_text(encoding="utf-8")

    for role in ("control", "persistence", "metrics"):
        assert f"autoresearch-cpu-successor-v3-{role}" in text
        assert f"pepagent-autoresearch-cpu-successor-{role}-v3" in text
    assert "-WindowStyle Hidden" in text
    assert "replacement is forbidden" in text
    assert "multiple live workers already poll" in text
    assert "additive launch is forbidden" in text
    assert "PEPAGENT_DATABASE_POOL_SIZE = '2'" in text
    assert "PEPAGENT_DATABASE_MAX_OVERFLOW = '2'" in text
    assert "Stop-Process" not in text
    assert "gpu" not in text.lower().replace("gpu_task_started", "")


def test_launcher_can_start_an_isolated_v4_without_replacing_v3() -> None:
    text = Path(
        "deploy/windows/start_autoresearch_cpu_successor_v3_workers.ps1"
    ).read_text(encoding="utf-8")

    assert "[ValidateSet(3, 4, 5)][int]$QueueGeneration = 3" in text
    for role in ("control", "persistence", "metrics"):
        assert f"autoresearch-cpu-successor-v4-{role}" in text
        assert f"pepagent-autoresearch-cpu-successor-{role}-v4" in text
    assert '"var/run/autoresearch-cpu-successor-v$QueueGeneration"' in text
    assert "[ValidateSet('all', 'control', 'persistence', 'metrics')]" in text
    assert 'EndsWith("-$RoleFilter")' in text
    assert "Stop-Process" not in text

    for role in ("control", "persistence", "metrics"):
        assert f"autoresearch-cpu-successor-v5-{role}" in text
        assert f"pepagent-autoresearch-cpu-successor-{role}-v5" in text
