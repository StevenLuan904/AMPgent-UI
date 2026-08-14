from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_result_first_style_preserves_science_evidence_and_resource_boundaries() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    protocol = (
        ROOT / "docs" / "ampgent-acea-execution-protocol.md"
    ).read_text(encoding="utf-8")
    goals = (
        ROOT / "docs" / "ampgent-long-horizon-goals.zh-CN.md"
    ).read_text(encoding="utf-8")

    for document in (agents, protocol, goals):
        assert "host" in document
        assert "GPU" in document
        assert "PID/role" in document
        assert "source revision" in document
        assert "PostgreSQL" in document
        assert "replay" in document

    assert "第一性原则与结果优先执行方式" in protocol
    assert "第一性原则恢复目标" in protocol
    assert "直接提高短肽候选质量" in protocol
    assert "routine 工程缺陷" in protocol
    assert "900 条候选" in protocol
    assert "48 条结构短名单" in protocol
    assert "每 pose 16 个 Rosetta decoy" in protocol

    assert "从候选质量反推工作" in goals
    assert "工程形式从简" in goals
    assert "行动默认继续" in goals
    assert "不影响短肽结果" in goals
    assert "routine 修复默认直接推进" in goals
