from pathlib import Path


def test_ampgent_acea_execution_protocol_preserves_nonnegotiable_rules() -> None:
    root = Path(__file__).parents[1]
    protocol_path = root / "docs" / "ampgent-acea-execution-protocol.md"
    protocol = protocol_path.read_text(encoding="utf-8")
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")

    assert "docs/ampgent-acea-execution-protocol.md" in agents
    required_footprints = (
        "192.168.99.32",
        "synth `192.168.99.2` / GPU4",
        "AMPlify 已由用户永久停用",
        "absolute_difference <= 1e-8",
        "relative_difference <= 1e-6",
        "46796d6f-2c94-49fa-82e0-2d7716423b10",
        "0e9801456c1fcd6eddd3d87c6dbff9cd744228ace38208e03559d10af419cc7b",
        "20260911, 20260912, 20260913",
        "formal run not submitted",
        "amp_multiobjective_portfolio_v32.yaml",
        "database-only replay bundle",
        "Explicit positive-charge design is reserved",
        "255 passed",
        "role, physical host, PID, and explicit source revision",
        "不得为了推进而把任务发给位置或版本未知的 poller",
    )
    for footprint in required_footprints:
        assert footprint in protocol

    assert "sjtu@" not in protocol
    assert "forbids a weighted total" in protocol.lower()
