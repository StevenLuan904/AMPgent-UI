from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]
AGENT_RULES = ROOT / "AGENTS.md"
LARGE_DATA_LEDGER = ROOT / "docs" / "ampgent-large-data-location-ledger.zh-CN.md"


def test_agent_style_distinguishes_numeric_tolerance_from_integrity() -> None:
    rules = AGENT_RULES.read_text(encoding="utf-8")
    assert "absolute_difference <= 1e-8" in rules
    assert "relative_difference <= 1e-6" in rules
    assert "A difference around `1e-15`" in rules
    assert "Require exact equality for identities and integrity" in rules
    assert "Do not use fail-closed as a reflex" in rules


def test_retired_metric_and_current_gpu_boundaries_are_explicit() -> None:
    rules = AGENT_RULES.read_text(encoding="utf-8")
    assert "AMPlify is retired from this project by user decision" in rules
    assert "GPU2 and GPU3 on host `192.168.99.32` are absolutely prohibited" in rules
    assert "GPU4 on host `192.168.99.19` is allowed for AMPgent" in rules
    assert "not run or schedule work on any GPU on host `192.168.99.32`" in rules


def test_large_data_policy_separates_storage_from_scientific_authorization() -> None:
    rules = AGENT_RULES.read_text(encoding="utf-8")
    ledger = LARGE_DATA_LEDGER.read_text(encoding="utf-8")
    assert "Keep the local repository and workstation focused" in rules
    assert "Host `192.168.99.19` may store AMPgent-owned large models" in rules
    assert "This storage permission does not authorize an unapproved formal run" in rules
    assert "PostgreSQL + 对象存储" in ledger
    assert "192.168.99.32` 的 GPU2/GPU3 为绝对禁区" in ledger
    assert "/data1/huangyueshan/pepagent/data/{models,runtimes,artifacts,run-cache}" in ledger


def test_agent_periodically_assesses_the_measured_critical_path() -> None:
    rules = AGENT_RULES.read_text(encoding="utf-8")
    normalized_rules = " ".join(rules.split())
    assert "## Continuous environment and bottleneck assessment" in rules
    assert (
        "periodically perform a read-only engineering-environment assessment"
        in normalized_rules
    )
    assert (
        "control-plane health (API, PostgreSQL, object store, Temporal)"
        in normalized_rules
    )
    assert "only then Agent analysis or decision latency" in normalized_rules
    assert "Do not equate absent active workflows with healthy readiness" in normalized_rules
    assert "Scale workers, processes, or parallel agents only when" in normalized_rules
    assert "durable evidence-count deltas" in normalized_rules


def test_large_data_ledger_records_remote_release_placement() -> None:
    ledger = LARGE_DATA_LEDGER.read_text(encoding="utf-8")
    assert "v37.0.4 平台发布归档" in ledger
    assert (
        "platform-e1f1d0a3e7211a83cc1fdd62e2989ba2511844f9eb8ed791b85caf87c130a3dd.tar.gz"
        in ledger
    )
    assert "1,114,245 bytes" in ledger
    assert "1,123,713 bytes" in ledger
    assert (
        "platform-926a1c9cc9c1c52ffd12404190b3397bd0b2649dee941cc5a0cb8ff142cc8eba.tar.gz"
        in ledger
    )
    assert "1,124,763 bytes" in ledger
    assert (
        "platform-cda153111e3e4f6bbb01720f0587e899b178cf9ec2626cdae65bcaf17b3146f3.tar.gz"
        in ledger
    )
