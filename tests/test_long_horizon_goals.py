from pathlib import Path


def test_long_horizon_goal_keeps_open_questions_and_replay_contract() -> None:
    root = Path(__file__).parents[1]
    goal = (root / "docs" / "ampgent-long-horizon-goals.zh-CN.md").read_text(
        encoding="utf-8"
    )
    protocol = (root / "docs" / "ampgent-acea-execution-protocol.md").read_text(
        encoding="utf-8"
    )
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")

    required_questions = (
        "显式正电性",
        "Pareto 搜索是否已经接近可达最优",
        "文献知识卡是否真的提高设计质量",
        "PepShot 是否帮助 Agent 避免结构性错误",
        "泛化到别的靶点",
        "harness evolving",
    )
    for question in required_questions:
        assert question in goal

    required_contracts = (
        "champion/challenger",
        "database+object-store-only replay",
        "不表示序列空间全局最优",
        "2×2",
        "019fad3e-76b8-7e32-8455-d2e9b31d33e5",
        "019fb910-f2dd-7be1-a7e6-bfe381512c25",
    )
    for contract in required_contracts:
        assert contract in goal

    assert "docs/ampgent-long-horizon-goals.zh-CN.md" in protocol
    assert "docs/ampgent-long-horizon-goals.zh-CN.md" in agents
    assert "版本规划不是 formal-run 授权" in protocol
